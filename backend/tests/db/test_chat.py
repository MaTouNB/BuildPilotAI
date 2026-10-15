import json
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from app.models import ChatEvent, ChatMessage, ChatRun, ChatSession
from tests.db.test_builds import client_for

pytestmark = pytest.mark.postgres


def new(client, key=None, title="Private fixture"):
    response = client.post(
        "/api/v1/ai/sessions", json={"title": title, "client_request_id": str(key or uuid4())}
    )
    assert response.status_code == 201, response.text
    assert "thread_id" not in response.text and "owner_user_id" not in response.text
    return response.json()


def send(client, session, message="查询 GPU 100%_literal 的历史参考价", key=None):
    response = client.post(
        f"/api/v1/ai/sessions/{session['id']}/messages",
        json={"message": message, "client_request_id": str(key or uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


def wait(client, session, run):
    path = f"/api/v1/ai/sessions/{session['id']}/runs/{run['id']}"
    deadline = monotonic() + 10
    while monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        value = response.json()
        if value["status"] != "RUNNING":
            return value
        sleep(0.03)
    pytest.fail("Run failed to reach a terminal state")


def test_postgres_checkpoint_context_restart_and_session_isolation(catalog, database):
    with client_for(database) as client:
        first = new(client)
        result = wait(client, first, send(client, first)["run"])
        assert result["status"] == "COMPLETED", result
        assert result["result"]["quotes"][0]["price_usd"] == "20.50"
        session_id = first["id"]
    # Rebuild the whole application/provider; state comes from Postgres, not a Python dict.
    with client_for(database) as restarted:
        continued = wait(restarted, first, send(restarted, first, "它的历史参考价")["run"])
        assert continued["status"] == "COMPLETED", continued
        assert continued["result"]["quotes"][0]["price_usd"] == "20.50"
        assert [item["name"] for item in continued["result"]["tool_calls"]] == [
            "get_part",
            "get_reference_price",
        ]
        second = new(restarted)
        separate = wait(restarted, second, send(restarted, second, "它的历史参考价")["run"])
        assert separate["status"] == "COMPLETED" and not separate["result"]["quotes"]
        messages = restarted.get(f"/api/v1/ai/sessions/{session_id}/messages").json()
        assert [row["role"] for row in messages["items"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        with database.connect() as db:
            thread_id = db.scalar(select(ChatSession.thread_id).where(ChatSession.id == session_id))
            assert thread_id != session_id
            assert (
                db.scalar(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id=:thread"),
                    {"thread": thread_id},
                )
                > 0
            )


def test_retries_sse_and_atomic_visible_history(catalog, database):
    with client_for(database) as client:
        create_key = uuid4()
        session = new(client, create_key)
        assert new(client, create_key)["id"] == session["id"]
        assert (
            client.post(
                "/api/v1/ai/sessions",
                json={"title": "different", "client_request_id": str(create_key)},
            ).status_code
            == 409
        )
        key = uuid4()
        accepted = send(client, session, key=key)
        replay = send(client, session, key=key)
        assert replay["run"]["id"] == accepted["run"]["id"]
        assert (
            client.post(
                f"/api/v1/ai/sessions/{session['id']}/messages",
                json={"message": "different", "client_request_id": str(key)},
            ).status_code
            == 409
        )
        done = wait(client, session, accepted["run"])
        assert done["status"] == "COMPLETED", done
        stream = client.get(f"/api/v1/ai/sessions/{session['id']}/runs/{done['id']}/events")
        assert stream.status_code == 200 and "text/event-stream" in stream.headers["content-type"]
        frames = [frame for frame in stream.text.split("\n\n") if frame.startswith("id:")]
        ids, kinds = [], []
        for frame in frames:
            lines = frame.splitlines()
            ids.append(int(lines[0].split(": ")[1]))
            kinds.append(lines[1].split(": ")[1])
            assert json.loads(lines[2][6:])["run_id"] == done["id"]
        assert ids == list(range(1, len(ids) + 1))
        assert (
            kinds[-1] == "run.completed"
            and kinds.count("tool.started") == 3
            and kinds.count("tool.completed") == 3
        )
        assert "message.delta" in kinds
        suffix = client.get(
            f"/api/v1/ai/sessions/{session['id']}/runs/{done['id']}/events?after_event_id={ids[-1]}"
        )
        assert not suffix.text
        assert send(client, session, key=key)["run"]["id"] == done["id"]
        with database.connect() as db:
            assert db.scalar(select(func.count()).select_from(ChatMessage)) == 2
            assert db.scalar(select(func.count()).select_from(ChatRun)) == 1


def test_deleted_session_denied_then_content_and_checkpoint_purged(catalog, database):
    from app.chat.cleanup import cleanup_pending

    with client_for(database) as client:
        session = new(client)
        done = wait(client, session, send(client, session)["run"])
        assert done["status"] == "COMPLETED", done
        path = f"/api/v1/ai/sessions/{session['id']}"
        version = client.get(path).json()["session"]["version"]
        assert client.request("DELETE", path, json={"expected_version": 1}).status_code == 409
        response = client.request("DELETE", path, json={"expected_version": version})
        assert response.status_code == 200
        assert client.get(path).status_code == 404
        assert client.get(path + "/messages").status_code == 404
        assert client.get(path + f"/runs/{done['id']}/events").status_code == 404
    cleanup_pending(database)
    with database.connect() as db:
        assert db.scalar(select(ChatSession.cleanup_status)) == "DONE"
        for model in (ChatMessage, ChatRun, ChatEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_interrupted_run_is_failed_not_reexecuted(catalog, database):
    from sqlalchemy.orm import Session

    from app.chat.store import event
    from app.etl.source import fingerprint

    with client_for(database) as client:
        session = new(client)
        run_id, key = uuid4(), uuid4()
        with Session(database) as db, db.begin():
            row = ChatRun(
                id=run_id,
                session_id=session["id"],
                client_request_id=key,
                request_hash=fingerprint("fixture interrupted"),
                status="RUNNING",
                last_event_id=0,
                deadline=datetime.now(UTC) - timedelta(seconds=1),
            )
            db.add(row)
            db.flush()
            db.add(
                ChatMessage(
                    id=uuid4(),
                    session_id=session["id"],
                    run_id=run_id,
                    sequence=1,
                    role="user",
                    status="RUNNING",
                    content="fixture interrupted",
                )
            )
            event(db, row, "run.started", {"status": "RUNNING"})
        result = client.get(f"/api/v1/ai/sessions/{session['id']}/runs/{run_id}").json()
        assert result["status"] == "FAILED" and result["error_code"] == "INTERRUPTED"
        replay = send(client, session, "fixture interrupted", key)
        assert replay["run"]["status"] == "FAILED"
        assert not client.get(f"/api/v1/ai/sessions/{session['id']}/messages").json()["items"][0][
            "payload"
        ]


@pytest.fixture
def identities(database):
    from sqlalchemy import insert

    from app.models import Tenant, TenantMember, UserAccount
    from tests.db.test_auth import PASSWORD_HASH

    tenants, users = sorted([uuid4(), uuid4()]), [uuid4(), uuid4(), uuid4()]
    with database.begin() as db:
        for index, tenant in enumerate(tenants):
            db.execute(insert(Tenant), dict(id=tenant, name=f"Synthetic {index}", status="active"))
        for index, user in enumerate(users):
            db.execute(
                insert(UserAccount),
                dict(
                    id=user,
                    username=f"fixture-user-{index}",
                    password_hash=PASSWORD_HASH,
                    status="active",
                ),
            )
        for tenant, user in [
            (tenants[0], users[0]),
            (tenants[1], users[0]),
            (tenants[0], users[1]),
            (tenants[1], users[2]),
        ]:
            db.execute(
                insert(TenantMember),
                dict(tenant_id=tenant, user_id=user, role="member", status="active"),
            )
    return tenants, users


def test_same_tenant_cross_owner_and_cross_tenant_denials(catalog, database, identities):
    from tests.db.test_auth import login

    with login(database, 0) as a, login(database, 1) as b, login(database, 2) as c:
        session = new(a)
        run = wait(a, session, send(a, session)["run"])
        path = f"/api/v1/ai/sessions/{session['id']}"
        for other in (b, c):
            assert other.get("/api/v1/ai/sessions").json()["total"] == 0
            for suffix in ("", "/messages", f"/runs/{run['id']}", f"/runs/{run['id']}/events"):
                assert other.get(path + suffix).status_code == 404
            assert (
                other.post(
                    path + "/messages", json={"message": "steal", "client_request_id": str(uuid4())}
                ).status_code
                == 404
            )
            assert other.post(path + f"/runs/{run['id']}/cancel").status_code == 404
            assert other.request("DELETE", path, json={"expected_version": 2}).status_code == 404
        switched = a.post("/api/v1/auth/select-tenant", json={"tenant_id": str(identities[0][1])})
        a.headers["X-CSRF-Token"] = switched.json()["csrf_token"]
        assert a.get(path).status_code == 404
        assert a.get("/api/v1/ai/sessions").json()["total"] == 0


def test_active_uniqueness_cancellation_and_fenced_completion(catalog, database):
    import asyncio
    from threading import Event

    from langchain_core.messages import AIMessage

    started, release = Event(), Event()

    class Gate:
        async def ainvoke(self, *args):
            started.set()
            await asyncio.to_thread(release.wait, 0.8)
            return AIMessage(content="must not become a completed message after cancellation")

    with client_for(database) as client:
        client.app.state.agent_provider_factory = Gate
        first, second = new(client), new(client)
        accepted = send(client, first)
        assert started.wait(1)
        try:
            body = {"message": "another request", "client_request_id": str(uuid4())}
            assert (
                client.post(f"/api/v1/ai/sessions/{first['id']}/messages", json=body).status_code
                == 409
            )
            assert (
                client.post(f"/api/v1/ai/sessions/{second['id']}/messages", json=body).status_code
                == 429
            )
            path = f"/api/v1/ai/sessions/{first['id']}/runs/{accepted['run']['id']}"
            cancelled = client.post(path + "/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
            assert (
                client.post(path + "/cancel").json()["last_event_id"]
                == cancelled.json()["last_event_id"]
            )
        finally:
            release.set()
    with database.connect() as db:
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 1
        assert db.scalar(select(ChatRun.status)) == "CANCELLED"
        kinds = list(db.scalars(select(ChatEvent.kind)))
        assert kinds.count("run.failed") == 1 and "run.completed" not in kinds


def test_stream_stops_after_membership_revocation(catalog, database, identities, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from langchain_core.messages import AIMessage

    from app.chat import routes
    from app.models import TenantMember
    from tests.db.test_auth import login

    started, release, permitted = Event(), Event(), Event()

    class Gate:
        async def ainvoke(self, *args):
            started.set()
            await asyncio.to_thread(release.wait, 0.8)
            return AIMessage(content="done")

    original = routes.resolve

    def observe(db, request, **kw):
        value = original(db, request, **kw)
        if request.url.path.endswith("/events"):
            permitted.set()
        return value

    monkeypatch.setattr(routes, "resolve", observe)
    with login(database, 0) as client:
        client.app.state.agent_provider_factory = Gate
        session = new(client)
        accepted = send(client, session)
        assert started.wait(1)
        path = f"/api/v1/ai/sessions/{session['id']}/runs/{accepted['run']['id']}"
        try:
            with ThreadPoolExecutor() as pool:
                stream = pool.submit(client.get, path + "/events")
                assert permitted.wait(1)
                with database.begin() as db:
                    db.execute(
                        update(TenantMember)
                        .where(
                            TenantMember.tenant_id == identities[0][0],
                            TenantMember.user_id == identities[1][0],
                        )
                        .values(status="revoked")
                    )
                release.set()
                text_value = stream.result(timeout=4).text
                assert "event: run.completed" not in text_value
            assert client.get(path).status_code == 401
        finally:
            release.set()


def test_cleanup_failure_is_pending_and_retryable(catalog, database, monkeypatch, caplog):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.chat.cleanup import cleanup_pending

    original = PostgresSaver.delete_thread

    def fail_cleanup(*args):
        raise RuntimeError("private-fixture-content-must-not-be-logged")

    with client_for(database) as client:
        session = new(client)
        done = wait(client, session, send(client, session)["run"])
        assert done["status"] == "COMPLETED"
        monkeypatch.setattr(PostgresSaver, "delete_thread", fail_cleanup)
        path = f"/api/v1/ai/sessions/{session['id']}"
        version = client.get(path).json()["session"]["version"]
        assert client.request("DELETE", path, json={"expected_version": version}).status_code == 200
        assert client.get(path).status_code == 404
    with database.connect() as db:
        assert db.scalar(select(ChatSession.cleanup_status)) == "PENDING"
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 2
    assert "private-fixture-content" not in caplog.text
    monkeypatch.setattr(PostgresSaver, "delete_thread", original)
    assert cleanup_pending(database)["cleaned"] == 1


def test_history_pagination_and_untrusted_identity_fields(catalog, database):
    with client_for(database) as client:
        session = new(client)
        for _ in range(2):
            assert wait(client, session, send(client, session)["run"])["status"] == "COMPLETED"
        path = f"/api/v1/ai/sessions/{session['id']}"
        latest = client.get(path + "/messages?limit=2").json()
        assert latest["has_older"]
        older = client.get(path + "/messages?limit=2&before=" + latest["before_cursor"]).json()
        assert not older["has_older"]
        assert set(row["id"] for row in latest["items"]).isdisjoint(
            row["id"] for row in older["items"]
        )
        other = new(client)
        assert (
            client.get(
                f"/api/v1/ai/sessions/{other['id']}/messages?before={latest['before_cursor']}"
            ).status_code
            == 404
        )
        for field in ("thread_id", "owner_user_id", "tenant_id", "history", "provider"):
            assert (
                client.post(
                    path + "/messages",
                    json={"message": "x", "client_request_id": str(uuid4()), field: "untrusted"},
                ).status_code
                == 422
            )
        csrf = client.headers.pop("X-CSRF-Token")
        assert (
            client.post(
                path + "/messages", json={"message": "x", "client_request_id": str(uuid4())}
            ).status_code
            == 403
        )
        client.headers["X-CSRF-Token"] = csrf
        assert client.get(path + "/messages?limit=51").status_code == 422


def test_checkpoint_context_is_bounded(catalog, database):
    from app.agent.provider import MockProvider
    from app.chat.graph import build_graph, checkpoint_connection, saver

    with client_for(database) as client:
        session = new(client)
        assert wait(client, session, send(client, session)["run"])["status"] == "COMPLETED"
        for _ in range(6):
            assert (
                wait(client, session, send(client, session, "它的历史参考价")["run"])["status"]
                == "COMPLETED"
            )
        with database.connect() as db:
            thread = db.scalar(select(ChatSession.thread_id))
        with checkpoint_connection(database) as conn:
            graph = build_graph(database, saver(conn), MockProvider, None, session_id=session["id"])
            state = graph.get_state({"configurable": {"thread_id": thread}}).values
            assert len(state["recent_questions"]) == 6
            assert state["recent_questions"] == ["它的历史参考价"] * 6
            assert state["last_part_id"] == str(catalog[1][1])


def test_checkpoint_migration_roundtrip_preserves_prior_schema(database):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import inspect

    with database.begin() as db:
        before = db.scalar(text("SELECT count(*) FROM user_account"))
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.attributes["connection"] = db
        command.downgrade(config, "m08_001")
        assert "chat_session" not in inspect(db).get_table_names()
        command.upgrade(config, "head")
        assert db.scalar(text("SELECT count(*) FROM checkpoint_migrations")) == len(
            PostgresSaver.MIGRATIONS
        )
        assert db.scalar(text("SELECT count(*) FROM user_account")) == before


def test_anonymous_cannot_access_session_endpoints(database):
    with client_for(database, enabled=False) as client:
        path = f"/api/v1/ai/sessions/{uuid4()}"
        assert client.get("/api/v1/ai/sessions").status_code == 401
        assert client.get(path + "/messages").status_code == 401
        assert (
            client.post("/api/v1/ai/sessions", json={"client_request_id": str(uuid4())}).status_code
            == 401
        )


def test_one_cleanup_failure_does_not_block_other_deleted_sessions(catalog, database, monkeypatch):
    from app.chat import cleanup

    with client_for(database) as client:
        first, second = new(client), new(client)
        for session in (first, second):
            assert wait(client, session, send(client, session)["run"])["status"] == "COMPLETED"
    with database.begin() as db:
        db.execute(
            update(ChatSession).values(
                status="deleted", cleanup_status="PENDING", deleted_at=datetime.now(UTC)
            )
        )
    original = cleanup.cleanup_one

    def unreliable(engine, session_id):
        if str(session_id) == first["id"]:
            raise RuntimeError("synthetic cleanup failure")
        return original(engine, session_id)

    monkeypatch.setattr(cleanup, "cleanup_one", unreliable)
    result = cleanup.cleanup_pending(database)
    assert result["failed"] == 1 and result["cleaned"] == 1
    with database.connect() as db:
        assert (
            db.scalar(select(ChatSession.cleanup_status).where(ChatSession.id == second["id"]))
            == "DONE"
        )
        assert (
            db.scalar(select(ChatSession.cleanup_status).where(ChatSession.id == first["id"]))
            == "PENDING"
        )


def test_checkpoint_subject_cannot_reference_a_different_session(catalog, database):
    from app.agent.provider import MockProvider
    from app.chat.graph import build_graph, checkpoint_connection, saver

    with client_for(database) as client:
        first, second = new(client), new(client)
        done = wait(client, first, send(client, first)["run"])
        assert done["status"] == "COMPLETED"
        with database.connect() as db:
            thread = db.scalar(select(ChatSession.thread_id).where(ChatSession.id == second["id"]))
        with checkpoint_connection(database) as conn:
            graph = build_graph(database, saver(conn), MockProvider, None, session_id=second["id"])
            graph.update_state(
                {"configurable": {"thread_id": thread}},
                {
                    "last_part_id": str(catalog[1][1]),
                    "subject_run_id": done["id"],
                },
            )
        poisoned = wait(client, second, send(client, second, "它的历史参考价")["run"])
        assert poisoned["status"] == "COMPLETED" and not poisoned["result"]["quotes"]


def test_deletion_preview_is_stale_when_a_new_answer_commits(catalog, database):
    with client_for(database) as client:
        session = new(client)
        accepted = send(client, session)
        preview_version = accepted["session"]["version"]
        assert wait(client, session, accepted["run"])["status"] == "COMPLETED"
        path = f"/api/v1/ai/sessions/{session['id']}"
        current = client.get(path).json()
        assert current["session"]["version"] > preview_version
        assert (
            client.request("DELETE", path, json={"expected_version": preview_version}).status_code
            == 409
        )
        assert len(client.get(path + "/messages").json()["items"]) == 2
