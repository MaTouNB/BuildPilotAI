"""Authenticated stateless mock chat. Limits are shared within one server process."""

from collections import deque
from contextlib import contextmanager
from threading import Lock
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.provider import MockProvider
from app.agent.runtime import run
from app.agent.tools import authorizer, toolset
from app.auth import Auth
from app.catalog import DB

router = APIRouter(prefix="/api/v1/ai")


class Chat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("message")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class Limits:
    """Bounded single-process guard: per user, tenant and globally. No stored chat."""

    def __init__(self):
        self.lock = Lock()
        self.windows = {}
        self.active = {}

    @contextmanager
    def acquire(self, actor):
        keys = [("user", actor.user_id), ("tenant", actor.tenant_id), ("global",)]
        with self.lock:
            now = monotonic()
            for key in list(self.windows):
                values = self.windows[key]
                while values and values[0] <= now - 60:
                    values.popleft()
                if not values and not self.active.get(key):
                    del self.windows[key]
            if len(self.windows) + len(keys) > 2048:
                raise HTTPException(429, "助手繁忙，请稍后重试", headers={"Retry-After": "60"})
            for key, per_minute, concurrent in zip(keys, [10, 30, 60], [1, 2, 4], strict=True):
                if (
                    len(self.windows.get(key, ())) >= per_minute
                    or self.active.get(key, 0) >= concurrent
                ):
                    raise HTTPException(
                        429, "助手请求过多或仍有查询运行，请稍后重试", headers={"Retry-After": "60"}
                    )
            for key in keys:
                self.windows.setdefault(key, deque()).append(now)
                self.active[key] = self.active.get(key, 0) + 1
        try:
            yield
        finally:
            with self.lock:
                for key in keys:
                    self.active[key] -= 1
                    if self.active[key] == 0:
                        del self.active[key]


@router.post("/chat")
async def chat(data: Chat, request: Request, db: DB, actor: Auth):
    # Authentication/CSRF and server context are resolved before provider/tool execution.
    # The provider is injected only in application construction/tests, never by HTTP fields.
    engine = request.app.state.catalog_engine
    db.rollback()
    with request.app.state.agent_limits.acquire(actor):
        provider = request.app.state.agent_provider_factory()
        return await run(
            provider, toolset(engine, authorize=authorizer(request, actor)), data.message
        )


def configure(application):
    application.state.agent_limits = Limits()
    application.state.agent_provider_factory = MockProvider
