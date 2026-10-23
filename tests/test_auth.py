from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from app.core.config import settings
from tests.conftest import PASSWORD


def new_email() -> str:
    return f"user-{uuid4().hex}@example.com"


def test_register_creates_user(client):
    email = new_email()
    response = client.post("/auth/register", json={"email": email.upper(), "password": PASSWORD})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == email
    assert set(body) == {"id", "email", "created_at"}


def test_password_is_stored_as_bcrypt_hash(client, db):
    email = new_email()
    client.post("/auth/register", json={"email": email, "password": PASSWORD})

    row = db.execute("SELECT password_hash FROM users WHERE email = %s", (email,)).fetchone()
    assert row["password_hash"].startswith("$2b$")
    assert PASSWORD not in row["password_hash"]


def test_register_duplicate_email_returns_409(client):
    email = new_email()
    client.post("/auth/register", json={"email": email, "password": PASSWORD})

    response = client.post("/auth/register", json={"email": email.upper(), "password": PASSWORD})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "a@example.com", "password": "short"},
        {"email": "a@example.com", "password": "é" * 40},
        {"email": "a@example.com"},
        {"email": "a@example.com", "password": PASSWORD, "is_admin": True},
    ],
    ids=["bad-email", "short-password", "password-over-72-bytes", "missing-password", "extra-field"],
)
def test_register_rejects_invalid_payload(client, payload):
    response = client.post("/auth/register", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_login_returns_token_for_user(client, make_user):
    user = make_user()

    response = client.post("/auth/login", json={"email": user["email"], "password": PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_expire_minutes * 60
    claims = jwt.decode(body["access_token"], settings.jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == str(user["id"])


@pytest.mark.parametrize("wrong", ["password", "email"])
def test_login_with_wrong_credentials_returns_401(client, make_user, wrong):
    user = make_user()
    payload = {"email": user["email"], "password": PASSWORD}
    payload[wrong] = new_email() if wrong == "email" else "wrong-password"

    response = client.post("/auth/login", json=payload)

    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "invalid_credentials",
        "message": "Invalid email or password",
    }


def test_protected_route_without_token_returns_401(client):
    response = client.get("/orders")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


def _token(secret: str = settings.jwt_secret, **overrides) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": "1", "iat": now, "exp": now + timedelta(minutes=5), **overrides}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        _token(secret="another-secret-that-is-long-enough-123456"),
        _token(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        _token(sub="not-a-number"),
    ],
    ids=["garbage", "wrong-signature", "expired", "bad-subject"],
)
def test_invalid_token_returns_401(client, token):
    response = client.get("/orders", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
