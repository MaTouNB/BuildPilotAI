from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433
    postgres_db: str = "buildpilot"
    postgres_user: str = "buildpilot"
    postgres_password: SecretStr | None = None

    demo_enabled: bool = False
    agent_provider: Literal["mock"] = "mock"

    auth_cookie_secure: bool = True
    auth_session_seconds: int = Field(default=28800, ge=300, le=86400)
    auth_allowed_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]
