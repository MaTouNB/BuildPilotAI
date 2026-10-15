"""Background read-only graph worker. Business terminal state is committed once."""

import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import resolve
from app.chat.graph import acquire_worker_lock, checkpoint_connection, invoke, release_worker_lock
from app.chat.store import complete, event, fail, get_run, limits, owned
from app.models import ChatMessage, ChatRun

logger = logging.getLogger(__name__)


class Stopped(Exception):
    pass


def process(application, request, actor, session_id, run_id):
    engine = application.state.catalog_engine
    try:
        with checkpoint_connection(engine) as conn:
            if not acquire_worker_lock(conn, session_id):
                raise HTTPException(409, "会话已有运行")
            try:
                with Session(engine) as db, db.begin():
                    limits(db)
                    if resolve(db, request, lock=True) != actor:
                        raise Stopped()
                    session = owned(db, session_id, actor, lock=True)
                    running = get_run(db, session_id, run_id)
                    if running.status != "RUNNING":
                        raise Stopped()
                    question = db.scalar(
                        select(ChatMessage.content).where(
                            ChatMessage.run_id == run_id, ChatMessage.sequence == 1
                        )
                    )
                    # Expunge the internal immutable thread mapping before ending the transaction.
                    db.expunge(session)

                def emit(kind, data):
                    with Session(engine) as db, db.begin():
                        limits(db)
                        if resolve(db, request) != actor:
                            raise Stopped()
                        owned(db, session_id, actor)
                        current = db.scalar(
                            select(ChatRun).where(ChatRun.id == run_id).with_for_update()
                        )
                        if current.status != "RUNNING":
                            raise Stopped()
                        event(db, current, kind, data)

                with application.state.agent_limits.acquire(actor):
                    result = invoke(
                        engine,
                        session,
                        run_id,
                        question,
                        application.state.agent_provider_factory,
                        emit,
                        conn,
                    )
                with Session(engine) as db, db.begin():
                    limits(db)
                    if resolve(db, request, lock=True) != actor:
                        raise Stopped()
                    owned(db, session_id, actor, lock=True)
                    current = db.scalar(
                        select(ChatRun).where(ChatRun.id == run_id).with_for_update()
                    )
                    complete(db, current, result)
            finally:
                release_worker_lock(conn, session_id)
    except Exception as exc:
        code = "EXECUTION_FAILED"
        if isinstance(exc, HTTPException):
            code = {
                401: "AUTH_REVOKED",
                403: "AUTH_REVOKED",
                429: "RATE_LIMITED",
                504: "TIMEOUT",
            }.get(exc.status_code, "EXECUTION_FAILED")
        elif isinstance(exc, Stopped):
            code = "CANCELLED"
        logger.warning("Chat run did not complete (%s)", code)
        # A DB outage leaves RUNNING; the deadline reconciler will mark it interrupted later.
        try:
            with Session(engine) as db, db.begin():
                limits(db)
                current = db.scalar(select(ChatRun).where(ChatRun.id == run_id).with_for_update())
                if current:
                    fail(db, current, code)
        except Exception:
            logger.warning("Chat terminal persistence deferred to deadline reconciliation")
            return
    finally:
        # Deletion marks access revoked first; cleanup waits for the graph advisory lock.
        from app.chat.cleanup import cleanup_one

        try:
            cleanup_one(engine, session_id)
        except Exception:
            logger.warning("Chat deletion cleanup pending; operator cleanup is retryable")


def track(application, coroutine):
    task = asyncio.create_task(coroutine)
    application.state.chat_tasks.add(task)

    def finished(future):
        application.state.chat_tasks.discard(future)
        if not future.cancelled() and future.exception() is not None:
            logger.warning("Chat background maintenance failed; cleanup remains pending")

    task.add_done_callback(finished)
    return task


def launch(application, request, actor, session_id, run_id):
    return track(
        application, asyncio.to_thread(process, application, request, actor, session_id, run_id)
    )
