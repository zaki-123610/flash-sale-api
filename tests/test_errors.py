import psycopg
from fastapi.testclient import TestClient

from app.db.pool import get_pool
from app.main import app


def call_with_broken_pool(error: Exception):
    def broken_pool():
        raise error

    app.dependency_overrides[get_pool] = broken_pool
    try:
        return TestClient(app, raise_server_exceptions=False).get("/products")
    finally:
        app.dependency_overrides.clear()


def test_unknown_route_returns_json_404(client):
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_malformed_json_returns_422(client, make_user):
    response = client.post(
        "/orders",
        content="{not json",
        headers={
            **make_user()["headers"],
            "Idempotency-Key": "k",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unexpected_error_returns_json_without_internals(client):
    response = call_with_broken_pool(RuntimeError("connection string with password=hunter2"))

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text


def test_database_outage_returns_503(client):
    response = call_with_broken_pool(psycopg.OperationalError("server closed the connection"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "server closed" not in response.text
