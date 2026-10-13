import logging
from typing import Literal

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger(__name__)


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "buildpilot-api"
    milestone: str = "M00"


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    application = FastAPI(title="BuildPilot AI", version="0.0.1")

    @application.get("/", response_model=Health)
    @application.get("/health", response_model=Health)
    def health() -> Health:
        return Health()

    @application.get("/ready", responses={503: {"description": "Database unavailable"}})
    def ready() -> JSONResponse:
        if config.postgres_password is None:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        try:
            with psycopg.connect(
                host=config.postgres_host,
                port=config.postgres_port,
                dbname=config.postgres_db,
                user=config.postgres_user,
                password=config.postgres_password.get_secret_value(),
                connect_timeout=3,
                options="-c statement_timeout=3000",
            ) as connection:
                connection.execute("SELECT 1").fetchone()
        except psycopg.Error:
            logger.warning("Database readiness check failed")
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ready", "database": "connected"})

    return application


app = create_app()
