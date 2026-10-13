from unittest.mock import MagicMock, patch

import psycopg
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


def test_health_does_not_require_database():
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "buildpilot-api", "milestone": "M00"}


def test_readiness_requires_configuration():
    client = TestClient(create_app(Settings(_env_file=None, postgres_password=None)))
    assert client.get("/ready").status_code == 503


def test_database_failure_is_redacted():
    config = Settings(_env_file=None, postgres_password=SecretStr("test-only-secret"))
    with patch("app.main.psycopg.connect", side_effect=psycopg.OperationalError("secret host")):
        response = TestClient(create_app(config)).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "secret" not in response.text


def test_database_success_executes_probe():
    config = Settings(_env_file=None, postgres_password=SecretStr("test-only-secret"))
    with patch("app.main.psycopg.connect") as connect:
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        response = TestClient(create_app(config)).get("/ready")
        connection.execute.assert_called_once_with("SELECT 1")
    assert response.status_code == 200
    assert response.json()["database"] == "connected"
