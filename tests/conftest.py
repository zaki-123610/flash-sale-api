import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx2
import psycopg
import pytest
from dotenv import dotenv_values
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
_dotenv = dotenv_values(ROOT / ".env")

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or _dotenv.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.exit("Set TEST_DATABASE_URL (env or .env) to a dedicated test database.", returncode=2)
if TEST_DATABASE_URL == (os.environ.get("DATABASE_URL") or _dotenv.get("DATABASE_URL")):
    pytest.exit("TEST_DATABASE_URL must differ from DATABASE_URL: tests drop all tables.", returncode=2)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("JWT_SECRET", "test-only-secret-" + "x" * 32)
os.environ["BCRYPT_ROUNDS"] = "4"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def db():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("DROP TABLE IF EXISTS orders, products, users CASCADE")
        yield conn


@pytest.fixture(scope="session")
def client(db):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def make_product(db, client):
    def _make(stock: int, price_cents: int = 1000) -> int:
        row = db.execute(
            "INSERT INTO products (name, price_cents, stock) VALUES (%s, %s, %s) RETURNING id",
            (f"test-product-{uuid4().hex}", price_cents, stock),
        ).fetchone()
        return row["id"]

    return _make


@pytest.fixture
def make_user(client):
    def _make() -> dict:
        email = f"user-{uuid4().hex}@example.com"
        user = client.post("/auth/register", json={"email": email, "password": PASSWORD})
        assert user.status_code == 201, user.text
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert token.status_code == 200, token.text
        return {
            "id": user.json()["id"],
            "email": email,
            "headers": {"Authorization": f"Bearer {token.json()['access_token']}"},
        }

    return _make


@pytest.fixture
def stock_of(db):
    def _stock(product_id: int) -> int:
        return db.execute("SELECT stock FROM products WHERE id = %s", (product_id,)).fetchone()[
            "stock"
        ]

    return _stock


@pytest.fixture
def orders_in_db(db):
    def _orders(product_id: int) -> list[dict]:
        return db.execute(
            "SELECT * FROM orders WHERE product_id = %s ORDER BY id", (product_id,)
        ).fetchall()

    return _orders


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(db, tmp_path_factory):
    """The real app served by uvicorn in a separate process."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("server") / "uvicorn.log"
    env = {**os.environ, "DB_POOL_MIN_SIZE": "20", "DB_POOL_MAX_SIZE": "20"}
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                pytest.fail(f"uvicorn exited early:\n{log_path.read_text()}")
            try:
                if httpx2.get(f"{base_url}/health", timeout=1).status_code == 200:
                    break
            except httpx2.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"uvicorn did not start:\n{log_path.read_text()}")
            time.sleep(0.2)
        yield base_url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def buyers(db, live_server) -> list[dict]:
    """200 distinct registered users, as ready-to-use auth headers."""
    password_hash = hash_password(PASSWORD)
    emails = [f"buyer-{uuid4().hex}@example.com" for _ in range(200)]
    rows = db.execute(
        """
        INSERT INTO users (email, password_hash)
        SELECT email, %s FROM unnest(%s::text[]) AS email
        RETURNING id
        """,
        (password_hash, emails),
    ).fetchall()
    return [{"Authorization": f"Bearer {create_access_token(row['id'])}"} for row in rows]
