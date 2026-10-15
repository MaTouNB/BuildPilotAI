"""Owner-scoped session APIs and durable SSE. Checkpoints are never publicly addressed."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.orm import Session

from app.agent.routes import Chat
from app.auth import Auth, resolve
from app.catalog import DB
from app.chat.cleanup import cleanup_one
from app.chat.store import (
    event,
    expire,
    fail,
    get_run,
    limits,
    lock_key,
    owned,
    run_json,
    session_json,
)
from app.chat.worker import launch, track
from app.etl.source import fingerprint
from app.models import ChatEvent, ChatMessage, ChatRun, ChatSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai/sessions")


class NewSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, Field(min_length=1, max_length=120)] = "新会话"
    client_request_id: UUID


class Send(Chat):
    client_request_id: UUID


class Delete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: Annotated[StrictInt, Field(ge=1)]


def authorize_write(db, request, actor):
    limits(db)
    if resolve(db, request, lock=True) != actor:
        raise HTTPException(401, "身份已变化，请重新登录")


@router.post("", status_code=201)
def create(data: NewSession, request: Request, db: DB, actor: Auth):
    if not data.title.strip():
        raise HTTPException(422, "标题不能为空")
    with db.begin():
        authorize_write(db, request, actor)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key(actor.user_id)})
        existing = db.scalar(
            select(ChatSession).where(
                ChatSession.tenant_id == actor.tenant_id,
                ChatSession.owner_user_id == actor.user_id,
                ChatSession.create_request_id == data.client_request_id,
            )
        )
        if existing:
            if existing.status != "active":
                raise HTTPException(404, "会话已删除")
            if existing.title != data.title.strip():
                raise HTTPException(409, "相同请求标识不能创建不同会话")
            return session_json(existing)
        count = db.scalar(
            select(func.count())
            .select_from(ChatSession)
            .where(
                ChatSession.tenant_id == actor.tenant_id,
                ChatSession.owner_user_id == actor.user_id,
                ChatSession.status == "active",
            )
        )
        if count >= 30:
            raise HTTPException(429, "会话数量达到上限，请先删除不需要的会话")
        value = ChatSession(
            id=uuid4(),
            thread_id=str(uuid4()),
            tenant_id=actor.tenant_id,
            owner_user_id=actor.user_id,
            create_request_id=data.client_request_id,
            title=data.title.strip(),
            status="active",
            cleanup_status="NONE",
            version=1,
        )
        db.add(value)
        db.flush()
        return session_json(value)


@router.get("")
def sessions(
    db: DB,
    actor: Auth,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    page_size: Annotated[int, Query(ge=1, le=30)] = 20,
):
    conditions = [
        ChatSession.tenant_id == actor.tenant_id,
        ChatSession.owner_user_id == actor.user_id,
        ChatSession.status == "active",
    ]
    rows = db.scalars(
        select(ChatSession)
        .where(*conditions)
        .order_by(ChatSession.created_at.desc(), ChatSession.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [session_json(row) for row in rows],
        "total": db.scalar(select(func.count()).select_from(ChatSession).where(*conditions)),
        "page": page,
        "page_size": page_size,
    }


@router.get("/{session_id}")
def snapshot(session_id: UUID, db: DB, actor: Auth):
    with db.begin():
        limits(db)
        value = owned(db, session_id, actor)
        expire(db, session_id)
        db.flush()
        active = db.scalar(
            select(ChatRun).where(ChatRun.session_id == session_id, ChatRun.status == "RUNNING")
        )
        return {
            "session": session_json(value),
            "active_run": run_json(active) if active else None,
            "message_count": db.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.session_id == session_id)
            ),
        }


@router.get("/{session_id}/messages")
def messages(
    session_id: UUID,
    db: DB,
    actor: Auth,
    before: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
):
    owned(db, session_id, actor)
    query = (
        select(ChatMessage, ChatRun.created_at)
        .join(ChatRun, ChatRun.id == ChatMessage.run_id)
        .where(ChatMessage.session_id == session_id)
    )
    if before:
        cursor = db.execute(
            select(ChatMessage, ChatRun.created_at)
            .join(ChatRun, ChatRun.id == ChatMessage.run_id)
            .where(ChatMessage.id == before, ChatMessage.session_id == session_id)
        ).first()
        if cursor is None:
            raise HTTPException(404, "消息游标不存在")
        row, created = cursor
        query = query.where(
            tuple_(ChatRun.created_at, ChatRun.id, ChatMessage.sequence)
            < (created, row.run_id, row.sequence)
        )
    rows = db.execute(
        query.order_by(
            ChatRun.created_at.desc(), ChatRun.id.desc(), ChatMessage.sequence.desc()
        ).limit(limit + 1)
    ).all()
    more = len(rows) > limit
    values = [row for row, _ in rows[:limit]][::-1]
    return {
        "items": [
            {
                "id": str(row.id),
                "run_id": str(row.run_id),
                "role": row.role,
                "status": row.status,
                "content": row.content,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }
            for row in values
        ],
        "has_older": more,
        "before_cursor": str(values[0].id) if values else None,
    }


@router.post("/{session_id}/messages", status_code=202)
async def send(session_id: UUID, data: Send, request: Request, db: DB, actor: Auth):
    start = False
    with db.begin():
        authorize_write(db, request, actor)
        # Cross-process admission: global lock + active-row uniqueness + per-session lock.
        db.execute(text("SELECT pg_advisory_xact_lock(74707001)"))
        value = owned(db, session_id, actor, lock=True)
        expire(db)
        db.flush()
        previous = db.scalar(
            select(ChatRun).where(
                ChatRun.session_id == session_id,
                ChatRun.client_request_id == data.client_request_id,
            )
        )
        if previous:
            if previous.request_hash != fingerprint(data.message):
                raise HTTPException(409, "相同请求标识不能发送不同问题")
            response = {"run": run_json(previous), "session": session_json(value)}
        else:
            if not db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(session_id)}
            ):
                raise HTTPException(409, "会话已有运行或上次运行仍在结束")
            active = db.execute(
                select(ChatRun, ChatSession.tenant_id, ChatSession.owner_user_id)
                .join(ChatSession, ChatSession.id == ChatRun.session_id)
                .where(ChatRun.status == "RUNNING")
            ).all()
            if any(row.session_id == session_id for row, _, _ in active):
                raise HTTPException(409, "同一会话仅允许一个活动运行")
            if (
                len(active) >= 4
                or sum(tenant == actor.tenant_id for _, tenant, _ in active) >= 2
                or any(user == actor.user_id for _, _, user in active)
            ):
                raise HTTPException(429, "会话运行过多，请稍后重试", headers={"Retry-After": "2"})
            if (
                db.scalar(
                    select(func.count())
                    .select_from(ChatRun)
                    .where(ChatRun.session_id == session_id)
                )
                >= 100
            ):
                raise HTTPException(429, "本会话已达100轮上限，请新建会话")
            row = ChatRun(
                id=uuid4(),
                session_id=session_id,
                client_request_id=data.client_request_id,
                request_hash=fingerprint(data.message),
                status="RUNNING",
                last_event_id=0,
                deadline=datetime.now(UTC) + timedelta(seconds=60),
            )
            db.add(row)
            db.flush()
            db.add(
                ChatMessage(
                    id=uuid4(),
                    session_id=session_id,
                    run_id=row.id,
                    sequence=1,
                    role="user",
                    status="RUNNING",
                    content=data.message,
                )
            )
            event(db, row, "run.started", {"status": "RUNNING"})
            value.version += 1
            value.updated_at = datetime.now(UTC)
            db.flush()
            response = {"run": run_json(row), "session": session_json(value)}
            start = True
    if start:
        launch(request.app, request, actor, session_id, UUID(response["run"]["id"]))
    return response


@router.get("/{session_id}/runs/{run_id}")
def status(session_id: UUID, run_id: UUID, db: DB, actor: Auth):
    with db.begin():
        limits(db)
        owned(db, session_id, actor)
        expire(db, session_id)
        db.flush()
        return run_json(get_run(db, session_id, run_id))


@router.post("/{session_id}/runs/{run_id}/cancel")
def cancel(session_id: UUID, run_id: UUID, request: Request, db: DB, actor: Auth):
    with db.begin():
        authorize_write(db, request, actor)
        owned(db, session_id, actor, lock=True)
        row = db.scalar(
            select(ChatRun)
            .where(ChatRun.id == run_id, ChatRun.session_id == session_id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "运行不存在")
        fail(db, row, "CANCELLED", cancelled=True)
        return run_json(row)


@router.delete("/{session_id}")
async def remove(session_id: UUID, data: Delete, request: Request, db: DB, actor: Auth):
    with db.begin():
        authorize_write(db, request, actor)
        value = owned(db, session_id, actor, lock=True)
        if value.version != data.expected_version:
            raise HTTPException(409, "会话版本变化，请刷新后确认删除")
        value.status, value.cleanup_status = "deleted", "PENDING"
        value.deleted_at = datetime.now(UTC)
        value.version += 1
        for row in db.scalars(
            select(ChatRun)
            .where(ChatRun.session_id == session_id, ChatRun.status == "RUNNING")
            .with_for_update()
        ).all():
            fail(db, row, "SESSION_DELETED", cancelled=True)
    track(request.app, asyncio.to_thread(cleanup_one, request.app.state.catalog_engine, session_id))
    return {"status": "deleted", "cleanup_status": "PENDING"}


@router.get("/{session_id}/runs/{run_id}/events")
def events(
    session_id: UUID,
    run_id: UUID,
    request: Request,
    db: DB,
    actor: Auth,
    after_event_id: Annotated[int, Query(ge=0, le=1000)] = 0,
):
    owned(db, session_id, actor)
    get_run(db, session_id, run_id)
    db.rollback()
    engine = request.app.state.catalog_engine

    async def stream():
        cursor, started = after_event_id, monotonic()
        while monotonic() - started < 15:
            if await request.is_disconnected():
                return
            try:
                with Session(engine) as current, current.begin():
                    limits(current)
                    if resolve(current, request) != actor:
                        return
                    owned(current, session_id, actor)
                    expire(current, session_id)
                    current.flush()
                    running = get_run(current, session_id, run_id)
                    terminal, last = running.status != "RUNNING", running.last_event_id
                    rows = current.scalars(
                        select(ChatEvent)
                        .where(ChatEvent.run_id == run_id, ChatEvent.event_id > cursor)
                        .order_by(ChatEvent.event_id)
                        .limit(50)
                    ).all()
                    frames = [(row.event_id, row.kind, row.data) for row in rows]
            except HTTPException:
                return  # Permission revocation closes transport, never exposes more events.
            except Exception:
                logger.warning("Chat event stream unavailable; clients must query run status")
                return
            for event_id, kind, payload in frames:
                cursor = event_id
                data = json.dumps(payload, ensure_ascii=False)
                yield f"id: {event_id}\nevent: {kind}\ndata: {data}\n\n"
            if terminal and cursor >= last:
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
