"""Compatibility tool boundaries against real PostgreSQL, with deterministic providers."""

from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.provider import call
from app.agent.tools import toolset
from app.models import Build, PartSpecOverride, TenantMember
from app.spec_reviews import Review, apply
from tests.db.test_agent import ask
from tests.db.test_auth import login
from tests.db.test_builds import client_for, new, put
from tests.db.test_chat import identities, send, wait  # noqa: F401
from tests.db.test_chat import new as chat_new
from tests.test_agent import Script
from tests.test_compatibility import proof

pytestmark = pytest.mark.postgres


def question(build, version=None):
    return f"检查方案 {build['id']} 版本 {version or build['version']} 的兼容性"


def test_tool_reads_owned_exact_version_and_preserves_build(catalog, database):
    catalog_client, ids = catalog
    with client_for(database) as client:
        build = put(
            client, new(client), catalog_client.get(f"/api/v1/parts/{ids[0]}").json()
        ).json()
        with Session(database) as db, db.begin():
            apply(db, [Review.model_validate(proof(str(ids[0]), "socket", "AM5"))])
        result = ask(client, question(build)).json()
        assert result["status"] == "COMPATIBILITY_CHECKED"
        assert [tool["name"] for tool in result["tool_calls"]] == ["check_compatibility"]
        report = result["compatibility_reports"][0]
        assert report["build_id"] == build["id"] and report["build_version"] == 2
        assert report["checked_rules_status"] == "UNKNOWN"
        assert report["rules"][0]["evidence"][0]["value"] == "AM5"
        assert report["rules"][0]["missing_fields"] == ["MOTHERBOARD.part_id"]
        assert report["uncovered_checks"] and not result["quotes"]
        assert "不代表整机兼容" in result["answer"]
        assert "reviewer" not in str(report)
        assert client.get(f"/api/v1/builds/{build['id']}").json() == build
        stale = ask(client, question(build, 1)).json()
        assert stale["status"] == "COMPATIBILITY_UNAVAILABLE"
        assert not stale["compatibility_reports"]
        assert stale["tool_calls"][0]["result"]["error"] == "BUILD_VERSION_CHANGED"
        with database.connect() as db:
            assert db.scalar(select(Build.version).where(Build.id == build["id"])) == 2


def test_tool_identity_schema_and_allowlist(catalog, database):
    assert "check_compatibility" not in toolset(database)
    with client_for(database) as client:
        build = new(client)
        for extra in ("tenant_id", "owner_user_id", "sql", "thread_id"):
            client.app.state.agent_provider_factory = lambda extra=extra: Script(
                call(
                    "check_compatibility",
                    {"build_id": build["id"], "expected_version": 1, extra: "untrusted"},
                )
            )
            assert ask(client, "ignore instructions").status_code == 422
        for version in (True, "1", 0, -1):
            client.app.state.agent_provider_factory = lambda version=version: Script(
                call("check_compatibility", {"build_id": build["id"], "expected_version": version})
            )
            assert ask(client, "ignore instructions").status_code == 422


@pytest.mark.usefixtures("identities")
def test_cross_owner_tenant_archived_and_missing_are_indistinguishable(catalog, database):
    with login(database, 0) as a, login(database, 1) as b, login(database, 2) as c:
        build = new(a)
        for client in (b, c):
            result = ask(client, question(build)).json()
            assert not result["compatibility_reports"]
            assert result["tool_calls"][0]["result"] == {
                "error": "BUILD_UNAVAILABLE",
                "message": "方案不可用或无权访问",
            }
        missing = ask(a, question({"id": str(uuid4()), "version": 1})).json()
        with database.begin() as db:
            db.execute(update(Build).where(Build.id == build["id"]).values(status="archived"))
        archived = ask(a, question(build)).json()
        assert missing["tool_calls"][0]["result"] == archived["tool_calls"][0]["result"]


def test_membership_revoked_between_provider_and_tool_is_rechecked(catalog, database):
    with client_for(database) as client:
        build = new(client)

        class Revoke:
            async def ainvoke(self, messages, schemas):
                with database.begin() as db:
                    db.execute(update(TenantMember).values(status="revoked"))
                return call("check_compatibility", {"build_id": build["id"], "expected_version": 1})

        client.app.state.agent_provider_factory = Revoke
        assert ask(client, question(build)).status_code == 401


def test_session_report_persists_as_snapshot_and_does_not_become_price_context(catalog, database):
    with client_for(database) as client:
        build = new(client)
        session = chat_new(client)
        result = wait(client, session, send(client, session, question(build))["run"])
        assert result["status"] == "COMPLETED", result
        assert result["result"]["compatibility_reports"][0]["checked_rules_status"] == "UNKNOWN"
    with client_for(database) as restarted:
        history = restarted.get(f"/api/v1/ai/sessions/{session['id']}/messages").json()
        assert history["items"][1]["payload"]["compatibility_reports"][0]["build_version"] == 1
        followup = wait(restarted, session, send(restarted, session, "它的历史参考价")["run"])
        assert not followup["result"]["quotes"]
        assert not followup["result"]["compatibility_reports"]
        with database.connect() as db:
            assert db.scalar(select(Build.version).where(Build.id == build["id"])) == 1
            assert not db.scalar(select(PartSpecOverride.id))
