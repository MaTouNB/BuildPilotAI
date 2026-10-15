"""Business history is authoritative. Never use a client thread id for checkpoint access."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text, update

from app.models import ChatEvent, ChatMessage, ChatRun, ChatSession


def lock_key(session_id):
    return int.from_bytes(
        sha256(("chat:" + str(session_id)).encode()).digest()[:8], "big", signed=True
    )


def limits(db):
    db.execute(text("SET LOCAL statement_timeout = '3000'"))
    db.execute(text("SET LOCAL lock_timeout = '1000'"))


def owned(db, session_id, actor, *, lock=False):
    query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.tenant_id == actor.tenant_id,
        ChatSession.owner_user_id == actor.user_id,
        ChatSession.status == "active",
    )
    value = db.scalar(query.with_for_update() if lock else query)
    if value is None:
        raise HTTPException(404, "会话不存在或已删除")
    return value


def get_run(db, session_id, run_id):
    value = db.scalar(select(ChatRun).where(ChatRun.id == run_id, ChatRun.session_id == session_id))
    if value is None:
        raise HTTPException(404, "运行不存在")
    return value


def session_json(value):
    return dict(
        id=str(value.id),
        title=value.title,
        version=value.version,
        created_at=value.created_at.isoformat(),
        updated_at=value.updated_at.isoformat(),
    )


def run_json(value):
    return dict(
        id=str(value.id),
        session_id=str(value.session_id),
        status=value.status,
        result=value.result if value.status == "COMPLETED" else None,
        error_code=value.error_code,
        last_event_id=value.last_event_id,
        created_at=value.created_at.isoformat(),
    )


def event(db, run, kind, data):
    # Caller holds the run row lock (or owns its new uncommitted row).
    run.last_event_id += 1
    db.add(
        ChatEvent(
            run_id=run.id,
            event_id=run.last_event_id,
            kind=kind,
            data={"run_id": str(run.id), **data},
        )
    )


def fail(db, run, code, *, cancelled=False):
    if run.status != "RUNNING":
        return
    run.status = "CANCELLED" if cancelled else "FAILED"
    run.error_code = code
    run.finished_at = datetime.now(UTC)
    db.execute(update(ChatMessage).where(ChatMessage.run_id == run.id).values(status=run.status))
    event(db, run, "run.failed", {"status": run.status, "error_code": code})


def expire(db, session_id=None):
    # A crashed worker loses its advisory lock. Do not fail a still-running worker.
    query = select(ChatRun).where(ChatRun.status == "RUNNING", ChatRun.deadline < datetime.now(UTC))
    if session_id is not None:
        query = query.where(ChatRun.session_id == session_id)
    for run in db.scalars(query.order_by(ChatRun.id).limit(100).with_for_update()).all():
        if db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(run.session_id)}
        ):
            fail(db, run, "INTERRUPTED")


def complete(db, run, result):
    if run.status != "RUNNING":
        return False
    session = db.get(ChatSession, run.session_id)
    if session.status != "active":
        fail(db, run, "SESSION_DELETED", cancelled=True)
        return False
    run.status, run.result, run.finished_at = "COMPLETED", result, datetime.now(UTC)
    session.version += 1
    session.updated_at = datetime.now(UTC)
    db.execute(update(ChatMessage).where(ChatMessage.run_id == run.id).values(status="COMPLETED"))
    db.add(
        ChatMessage(
            id=uuid4(),
            session_id=run.session_id,
            run_id=run.id,
            sequence=2,
            role="assistant",
            status="COMPLETED",
            content=result["answer"],
            payload=result,
        )
    )
    # These are chunks of the committed backend answer, not a claim of live LLM tokens.
    answer = result["answer"]
    for offset in range(0, len(answer), 24):
        event(db, run, "message.delta", {"text": answer[offset : offset + 24]})
    event(db, run, "run.completed", {"status": "COMPLETED"})
    return True
