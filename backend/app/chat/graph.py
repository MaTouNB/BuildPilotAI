"""Small genuine LangGraph with PostgresSaver, bounded structured short-term state."""

import asyncio
from contextlib import contextmanager
from typing import TypedDict
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from psycopg.rows import dict_row
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.provider import MockProvider, call
from app.agent.runtime import run
from app.agent.tools import toolset
from app.chat.store import lock_key
from app.models import ChatRun

FOLLOWUPS = {
    "它多少钱",
    "它多少钱？",
    "它的历史参考价",
    "这款的历史参考价",
    "再查一次",
    "再查一次历史参考价",
}


class State(TypedDict, total=False):
    question: str
    run_id: str
    last_part_id: str | None
    subject_run_id: str | None
    recent_questions: list[str]
    result: dict


@contextmanager
def checkpoint_connection(engine):
    # Reuse the engine's server-selected schema (including isolated test schemas).
    raw = engine.raw_connection()
    conn = raw.driver_connection
    factory, autocommit = conn.row_factory, conn.autocommit
    try:
        conn.rollback()
        conn.autocommit = True
        conn.row_factory = dict_row
        conn.execute("SET statement_timeout = '3000'")
        conn.execute("SET lock_timeout = '1000'")
        yield conn
    finally:
        try:
            if not conn.closed:
                conn.rollback()
                conn.execute("RESET statement_timeout")
                conn.execute("RESET lock_timeout")
                conn.row_factory = factory
                conn.autocommit = autocommit
        except Exception:
            raw.invalidate()
            raise
        finally:
            raw.close()


def saver(conn):
    return PostgresSaver(
        conn,
        serde=JsonPlusSerializer(
            pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[]
        ),
    )


class ContextMock(MockProvider):
    def __init__(self, part_id):
        self.part_id = part_id

    async def ainvoke(self, messages, schemas):
        from langchain_core.messages import HumanMessage, ToolMessage

        question = next(item.content for item in messages if isinstance(item, HumanMessage))
        if (
            self.part_id
            and question in FOLLOWUPS
            and not any(isinstance(item, ToolMessage) for item in messages)
        ):
            return call("get_part", {"part_id": self.part_id})
        return await super().ainvoke(messages, schemas)


def build_graph(engine, checkpointer, factory, emit, *, session_id, authorize=None):
    def answer(state: State):
        part_id = None
        subject_run = state.get("subject_run_id")
        if subject_run:
            # A checkpoint from a failed/uncommitted run cannot establish context.
            with Session(engine) as db:
                current = db.scalar(
                    select(ChatRun).where(
                        ChatRun.id == UUID(state["run_id"]), ChatRun.session_id == session_id
                    )
                )
                if current is None:
                    raise ValueError("Graph run is outside its authorized session")
                previous = db.scalar(
                    select(ChatRun).where(
                        ChatRun.id == UUID(subject_run),
                        ChatRun.session_id == session_id,
                        ChatRun.status == "COMPLETED",
                    )
                )
                if previous and state.get("last_part_id") in {
                    quote["part_id"] for quote in (previous.result or {}).get("quotes", [])
                }:
                    part_id = state.get("last_part_id")
        provider = factory()
        if type(provider) is MockProvider:
            provider = ContextMock(part_id)
        result = asyncio.run(
            run(provider, toolset(engine, authorize=authorize), state["question"], emit=emit)
        )
        result["persistence"] = "已保存到当前私人会话；仅保留有上限的结构化上下文"
        quotes = result["quotes"]
        return {
            "result": result,
            "last_part_id": quotes[-1]["part_id"] if quotes else part_id,
            "subject_run_id": state["run_id"] if quotes else subject_run if part_id else None,
            "recent_questions": [*state.get("recent_questions", []), state["question"]][-6:],
        }

    graph = StateGraph(State)
    graph.add_node("answer", answer)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=checkpointer)


def invoke(engine, session, run_id, question, factory, emit, conn, *, authorize=None):
    with tracing_context(enabled=False):
        graph = build_graph(
            engine, saver(conn), factory, emit, session_id=session.id, authorize=authorize
        )
        return graph.invoke(
            {"question": question, "run_id": str(run_id)},
            {
                "configurable": {"thread_id": session.thread_id},
                "recursion_limit": 4,
                "callbacks": [],
            },
        )["result"]


def acquire_worker_lock(conn, session_id):
    return conn.execute(
        "SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key(session_id),)
    ).fetchone()["acquired"]


def release_worker_lock(conn, session_id):
    conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key(session_id),))
