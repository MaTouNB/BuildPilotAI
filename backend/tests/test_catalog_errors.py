from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.main import create_app


def test_missing_configuration_is_service_error():
    with TestClient(create_app(Settings(_env_file=None, postgres_password=None))) as client:
        response = client.get("/api/v1/parts")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_database_exception_is_redacted():
    with patch(
        "app.catalog.make_engine",
        side_effect=OperationalError("private SQL", {}, Exception("private secret")),
    ):
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/parts")
    assert response.status_code == 503
    assert "private" not in response.text
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
