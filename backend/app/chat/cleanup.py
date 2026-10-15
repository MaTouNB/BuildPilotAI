"""Retryable deleted-session cleanup. Only tombstones are eligible; no chat text printed."""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.chat.graph import acquire_worker_lock, checkpoint_connection, release_worker_lock, saver
from app.chat.store import limits
from app.db import make_engine
from app.models import ChatEvent, ChatMessage, ChatRun, ChatSession

logger = logging.getLogger(__name__)


def cleanup_one(engine, session_id):
    with Session(engine) as db:
        candidate = db.get(ChatSession, session_id)
        if (
            candidate is None
            or candidate.status != "deleted"
            or candidate.cleanup_status != "PENDING"
        ):
            return False
    with checkpoint_connection(engine) as conn:
        if not acquire_worker_lock(conn, session_id):
            return False
        try:
            with Session(engine) as db, db.begin():
                limits(db)
                row = db.scalar(
                    select(ChatSession).where(ChatSession.id == session_id).with_for_update()
                )
                if row is None or row.status != "deleted" or row.cleanup_status != "PENDING":
                    return False
                saver(conn).delete_thread(row.thread_id)
                run_ids = select(ChatRun.id).where(ChatRun.session_id == row.id)
                db.execute(delete(ChatEvent).where(ChatEvent.run_id.in_(run_ids)))
                db.execute(delete(ChatMessage).where(ChatMessage.session_id == row.id))
                db.execute(delete(ChatRun).where(ChatRun.session_id == row.id))
                row.cleanup_status = "DONE"
                row.title = "已删除会话"
            return True
        finally:
            release_worker_lock(conn, session_id)


def cleanup_pending(engine):
    with Session(engine) as db:
        pending = list(
            db.scalars(
                select(ChatSession.id)
                .where(ChatSession.status == "deleted", ChatSession.cleanup_status == "PENDING")
                .limit(100)
            )
        )
    cleaned, failed = 0, 0
    for session_id in pending:
        try:
            cleaned += cleanup_one(engine, session_id)
        except Exception:
            failed += 1
            logger.warning("Chat cleanup item failed; tombstone remains pending")
    with Session(engine) as db, db.begin():
        # Seven-day minimal tombstone retention; no messages or question text remain.
        result = db.execute(
            delete(ChatSession).where(
                ChatSession.status == "deleted",
                ChatSession.cleanup_status == "DONE",
                ChatSession.deleted_at < datetime.now(UTC) - timedelta(days=7),
            )
        )
        purged = result.rowcount
    return {
        "cleaned": cleaned,
        "failed": failed,
        "pending_checked": len(pending),
        "audit_rows_purged": purged,
    }


def main():
    engine = make_engine()
    try:
        result = cleanup_pending(engine)
        print(json.dumps(result))
        if result["failed"]:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
