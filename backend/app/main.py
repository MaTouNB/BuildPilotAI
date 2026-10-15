import logging
from contextlib import asynccontextmanager
from threading import Lock
from typing import Literal
from uuid import uuid4

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.agent.routes import configure as configure_agent
from app.agent.routes import router as agent_router
from app.auth import router as auth_router
from app.builds import router as builds_router
from app.catalog import router
from app.config import Settings

logger = logging.getLogger(__name__)


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "buildpilot-api"
    milestone: str = "M00"


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application):
        yield
        if engine is None and application.state.catalog_engine is not None:
            application.state.catalog_engine.dispose()

    application = FastAPI(title="BuildPilot AI", version="0.6.0", lifespan=lifespan)
    application.state.engine_lock = Lock()
    application.state.settings = config
    application.state.catalog_engine = engine
    application.include_router(router)
    application.include_router(builds_router)
    application.include_router(auth_router)
    application.include_router(agent_router)
    configure_agent(application)

    @application.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        if request.url.path == "/api/v1/ai/chat":
            # Bound the raw JSON body before parsing, including chunked requests.
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 8192:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "INPUT_TOO_LARGE",
                                "message": "助手请求过大",
                                "request_id": request.state.request_id,
                                "details": None,
                            }
                        },
                    )
                    response.headers["Cache-Control"] = "no-store"
                    response.headers["X-Request-ID"] = request.state.request_id
                    return response
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith(("/api/v1/auth", "/api/v1/builds", "/api/v1/ai")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def error(request, status, code, message, details=None, headers=None):
        return JSONResponse(
            status_code=status,
            headers=headers,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request.state.request_id,
                    "details": details,
                }
            },
        )

    @application.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(
            request,
            exc.status_code,
            {
                401: "AUTH_REQUIRED",
                403: "FORBIDDEN",
                404: "NOT_FOUND",
                409: "VERSION_CONFLICT",
                422: "INVALID_QUERY",
                429: "RATE_LIMITED",
                503: "DATABASE_UNAVAILABLE",
                502: "AGENT_EXECUTION_FAILED",
                504: "AGENT_TIMEOUT",
            }.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Exclude input/context: they may contain confidential input or non-JSON objects.
        return error(
            request,
            422,
            "INVALID_QUERY",
            "请求参数不合法",
            [{"loc": list(item["loc"]), "type": item["type"]} for item in exc.errors()],
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        logger.warning("Catalog database unavailable")
        return error(request, 503, "DATABASE_UNAVAILABLE", "目录数据库暂时不可用，请稍后重试")

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
