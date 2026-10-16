import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.agent import runtime
from app.agent.provider import MockProvider, call
from app.agent.routes import Chat, Limits
from app.agent.tools import Search, bounded_json


def execute(provider, tools=None, question="查 RTX 3060 的历史参考价"):
    return asyncio.run(runtime.run(provider, tools or {}, question))


class Script:
    def __init__(self, *replies):
        self.replies = iter(replies)

    async def ainvoke(self, messages, schemas):
        return next(self.replies)


@pytest.mark.parametrize(
    "args",
    [
        {"keywords": "x", "limit": 0},
        {"keywords": "x", "limit": 6},
        {"keywords": "x", "limit": True},
        {"keywords": "x", "limit": "2"},
        {"keywords": "x", "tenant_id": str(uuid4())},
        {"keywords": "x", "sql": "SELECT 1"},
        {"keywords": " "},
        {"keywords": "x" * 121},
        {"keywords": "x", "category": "secret"},
        {"keywords": "x", "priced_only": "yes"},
    ],
)
def test_strict_gateway_schema(args):
    with pytest.raises(ValidationError):
        Search.model_validate(args)


def test_no_tool_fabrication_never_becomes_quote():
    result = execute(Script(AIMessage(content="RTX 3060 今日人民币 999 元，已确认兼容")))
    assert result["status"] == "NO_PRICE_EVIDENCE"
    assert not result["quotes"] and not result["tool_calls"]
    assert "999" not in result["answer"]


def test_unknown_tool_is_denied_before_execution():
    with pytest.raises(HTTPException) as error:
        execute(Script(call("execute_sql", {"sql": "DROP TABLE build"})))
    assert error.value.status_code == 422


def test_invalid_model_args_are_not_echoed():
    tool = StructuredTool.from_function(
        lambda keywords, limit=5: {}, name="search_parts", description="fixture", args_schema=Search
    )
    with pytest.raises(HTTPException) as error:
        execute(
            Script(call("search_parts", {"keywords": "secret", "limit": 999})),
            {"search_parts": tool},
        )
    assert error.value.status_code == 422 and "secret" not in error.value.detail


def test_tool_count_and_duplicate_call_ids():
    calls = [call("search_parts", {"keywords": "x"}).tool_calls[0] for _ in range(5)]
    with pytest.raises(HTTPException) as error:
        execute(Script(AIMessage(content="", tool_calls=calls)))
    assert error.value.status_code == 422
    tool = StructuredTool.from_function(
        lambda keywords, **kw: {}, name="search_parts", description="fixture", args_schema=Search
    )
    calls = [calls[0], calls[0]]
    with pytest.raises(HTTPException) as error:
        execute(Script(AIMessage(content="", tool_calls=calls)), {"search_parts": tool})
    assert error.value.status_code == 502


def test_model_timeout_and_failure_are_redacted(monkeypatch):
    class Slow:
        async def ainvoke(self, *args):
            await asyncio.sleep(0.1)

    monkeypatch.setattr(runtime, "MODEL_TIMEOUT", 0.01)
    with pytest.raises(HTTPException) as error:
        execute(Slow())
    assert error.value.status_code == 504

    class Failure:
        async def ainvoke(self, *args):
            raise RuntimeError("private-key-should-not-escape")

    with pytest.raises(HTTPException) as error:
        execute(Failure())
    assert error.value.status_code == 502 and "private-key" not in error.value.detail


def test_model_output_and_context_bounds(monkeypatch):
    with pytest.raises(HTTPException) as error:
        execute(Script(AIMessage(content="x" * 2001)))
    assert error.value.status_code == 502
    monkeypatch.setattr(runtime, "MAX_CONTEXT_BYTES", 10)
    with pytest.raises(HTTPException) as error:
        execute(Script(AIMessage(content="x")))
    assert error.value.status_code == 422
    with pytest.raises(ValueError):
        bounded_json({"result": "x" * 17000})


def test_mock_refuses_current_or_cny_and_requires_disambiguation():
    assert not execute(MockProvider(), question="人民币实时价格")["tool_calls"]
    tool = StructuredTool.from_function(
        lambda keywords, **kw: {"items": [{"id": "a"}, {"id": "b"}]},
        name="search_parts",
        description="fixture",
        args_schema=Search,
    )
    result = execute(MockProvider(), {"search_parts": tool})
    assert result["status"] == "NEEDS_SELECTION" and len(result["tool_calls"]) == 1


def test_user_tenant_global_concurrency_and_release():
    limits = Limits()
    tenant = uuid4()
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=tenant)
    with limits.acquire(actor):
        with pytest.raises(HTTPException):
            with limits.acquire(actor):
                pass
        second = SimpleNamespace(user_id=uuid4(), tenant_id=tenant)
        with limits.acquire(second):
            with pytest.raises(HTTPException):
                with limits.acquire(SimpleNamespace(user_id=uuid4(), tenant_id=tenant)):
                    pass
    assert not limits.active
    with ExitStack() as stack:
        for _ in range(4):
            stack.enter_context(limits.acquire(SimpleNamespace(user_id=uuid4(), tenant_id=uuid4())))
        with pytest.raises(HTTPException):
            stack.enter_context(limits.acquire(SimpleNamespace(user_id=uuid4(), tenant_id=uuid4())))


def test_rate_window_and_failure_release(monkeypatch):
    from app.agent import routes

    time = [0.0]
    monkeypatch.setattr(routes, "monotonic", lambda: time[0])
    limits = Limits()
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4())
    for _ in range(10):
        with limits.acquire(actor):
            pass
    with pytest.raises(HTTPException):
        with limits.acquire(actor):
            pass
    time[0] = 61
    with pytest.raises(ValueError), limits.acquire(actor):
        raise ValueError("failure")
    assert not limits.active
    with limits.acquire(actor):
        pass


def test_chat_cannot_supply_identity_provider_or_history():
    for field in ("tenant_id", "user_id", "provider", "history"):
        with pytest.raises(ValidationError):
            Chat.model_validate({"message": "x", field: "untrusted"})


def test_full_model_names_are_preserved_for_disambiguation():
    from langchain_core.messages import HumanMessage

    model = MockProvider()
    name = "MSI GeForce RTX 3060 Ventus 2X 12G"
    response = asyncio.run(model.ainvoke([HumanMessage(content=f"查询 {name} 的历史参考价")], []))
    assert response.tool_calls[0]["args"]["keywords"] == name


def test_timed_out_worker_retains_capacity_until_actual_completion(monkeypatch):
    from threading import BoundedSemaphore, Event

    release = Event()
    monkeypatch.setattr(runtime, "WORKERS", BoundedSemaphore(1))
    monkeypatch.setattr(runtime, "TOOL_TIMEOUT", 0.02)

    def slow(keywords, **kw):
        release.wait(1)
        return {"items": []}

    tool = StructuredTool.from_function(
        slow, name="search_parts", description="fixture", args_schema=Search
    )

    async def scenario():
        try:
            with pytest.raises(HTTPException) as error:
                await runtime.run(
                    Script(call("search_parts", {"keywords": "x"})), {"search_parts": tool}, "x"
                )
            assert error.value.status_code == 504
            assert not runtime.WORKERS.acquire(blocking=False)
        finally:
            release.set()
        await asyncio.sleep(0.05)
        assert runtime.WORKERS.acquire(blocking=False)
        runtime.WORKERS.release()

    asyncio.run(scenario())


def test_external_tracing_is_disabled_even_with_host_env(monkeypatch):
    from langsmith import Client

    calls = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "synthetic-test-only")
    monkeypatch.setattr(Client, "create_run", lambda *args, **kwargs: calls.append("create"))
    monkeypatch.setattr(Client, "update_run", lambda *args, **kwargs: calls.append("update"))
    tool = StructuredTool.from_function(
        lambda keywords, **kw: {"items": []},
        name="search_parts",
        description="fixture",
        args_schema=Search,
    )
    execute(MockProvider(), {"search_parts": tool})
    assert not calls


def test_compatibility_prose_alone_cannot_create_report():
    result = execute(Script(AIMessage(content="所有硬件完全兼容，PASS")))
    assert result["compatibility_reports"] == []
    assert result["status"] == "NO_PRICE_EVIDENCE"
    assert "PASS" not in result["answer"]


def test_mock_compatibility_requires_explicit_version_and_identifier():
    from langchain_core.messages import HumanMessage

    model = MockProvider()
    identifier = str(uuid4())
    valid = asyncio.run(
        model.ainvoke([HumanMessage(content=f"检查方案 {identifier} 版本 2 的兼容性")], [])
    )
    assert valid.tool_calls[0]["name"] == "check_compatibility"
    assert valid.tool_calls[0]["args"] == {"build_id": identifier, "expected_version": 2}
    for question in ("检查我的方案兼容性", f"检查方案 {identifier} 的兼容性"):
        response = asyncio.run(model.ainvoke([HumanMessage(content=question)], []))
        assert not response.tool_calls


def test_vague_compatibility_question_requests_explicit_build_version():
    result = execute(MockProvider(), question="检查我的方案兼容性")
    assert result["status"] == "COMPATIBILITY_INPUT_REQUIRED"
    assert "版本" in result["answer"] and not result["tool_calls"]
