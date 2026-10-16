from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import Build, BuildItem, PartSpecOverride
from app.spec_reviews import Review, apply
from tests.db.test_builds import client_for, new, put
from tests.test_compatibility import proof

pytestmark = pytest.mark.postgres


def test_review_import_append_only_conflicts_and_idempotency(catalog, database):
    _, ids = catalog
    first = Review.model_validate(proof(str(ids[0]), "socket", "AM5"))
    with Session(database) as db, db.begin():
        assert apply(db, [first]) == 1
        assert apply(db, [first]) == 0
    for statement in (update(PartSpecOverride).values(value="AM4"), delete(PartSpecOverride)):
        with pytest.raises(DBAPIError), database.begin() as db:
            db.execute(statement)
    changed = first.model_copy(update={"value": "AM4"})
    with pytest.raises(ValueError), Session(database) as db, db.begin():
        apply(db, [changed])
    invalid = first.model_copy(update={"id": uuid4(), "version": 2, "supersedes_id": uuid4()})
    with pytest.raises(ValueError), Session(database) as db, db.begin():
        apply(db, [invalid])
    wrong_category = first.model_copy(update={"id": uuid4(), "part_id": ids[1]})
    with pytest.raises(ValueError), Session(database) as db, db.begin():
        apply(db, [wrong_category])


def test_private_versioned_report_no_build_or_price_mutation(catalog, database):
    catalog_client, ids = catalog
    with client_for(database) as client:
        build = new(client)
        part = catalog_client.get(f"/api/v1/parts/{ids[0]}").json()
        build = put(client, build, part).json()
        path = f"/api/v1/builds/{build['id']}"
        review = Review.model_validate(proof(str(ids[0]), "socket", "AM5"))
        with Session(database) as db, db.begin():
            apply(db, [review])
        response = client.get(path + "/compatibility?expected_version=2")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["checked_rules_status"] == "UNKNOWN"
        assert report["build_version"] == 2
        assert report["rules"][0]["missing_fields"] == ["MOTHERBOARD.part_id"]
        assert report["rules"][0]["evidence"][0]["value"] == "AM5"
        assert "reviewer" not in str(report)
        assert response.headers["cache-control"] == "no-store"
        assert client.get(path + "/compatibility?expected_version=1").status_code == 409
        assert client.post(path + "/check", json={"expected_version": 2}).status_code == 200
        assert client.get(path).json() == build
        assert (
            client.get(f"/api/v1/builds/{uuid4()}/compatibility?expected_version=2").status_code
            == 404
        )
        with client_for(database, enabled=False) as anonymous:
            assert anonymous.get(path + "/compatibility?expected_version=2").status_code == 401
        with database.begin() as db:
            # Foreign user/tenant ownership must not leak evidence either.
            from app.builds import DEMO_TENANT, DEMO_USER

            db.execute(
                update(Build)
                .where(Build.id == build["id"])
                .values(tenant_id=DEMO_TENANT, owner_user_id=DEMO_USER)
            )
        assert client.get(path + "/compatibility?expected_version=2").status_code == 404
        assert client.post(path + "/check", json={"expected_version": 2}).status_code == 404
        with database.connect() as db:
            assert db.scalar(select(Build.version).where(Build.id == build["id"])) == 2
            assert (
                db.scalar(
                    select(BuildItem.price_snapshot_usd).where(BuildItem.build_id == build["id"])
                )
                == 10.25
            )


def test_report_pass_fail_conflict_supersession_and_pending(catalog, database):
    from app.models import HardwarePart

    _, ids = catalog
    parts = {"CPU": ids[0], "GPU": ids[1]}
    with client_for(database) as client:
        build = new(client)
        with Session(database) as db, db.begin():
            for slot in ("MOTHERBOARD", "MEMORY", "CASE"):
                part_id = uuid4()
                parts[slot] = part_id
                db.add(
                    HardwarePart(
                        id=part_id,
                        category=slot,
                        display_name="Synthetic " + slot,
                        normalized_name="synthetic",
                        specifications={},
                        is_active=True,
                        quality_status="REVIEW",
                        reviewed_by="fixture",
                        review_reason="test",
                    )
                )
            db.flush()
            for slot, part_id in parts.items():
                db.add(
                    BuildItem(
                        id=uuid4(),
                        build_id=build["id"],
                        slot=slot,
                        part_id=part_id,
                        part_name="Synthetic",
                        quantity=1,
                    )
                )
            rows = [
                proof(str(parts["CPU"]), "socket", "AM5"),
                proof(str(parts["MOTHERBOARD"]), "socket", "AM5"),
                proof(str(parts["MOTHERBOARD"]), "memory_type", "DDR5"),
                proof(str(parts["MEMORY"]), "memory_type", "DDR5"),
                proof(str(parts["GPU"]), "length_mm", "235"),
                proof(str(parts["CASE"]), "max_gpu_length_mm", "235"),
            ]
            apply(db, [Review.model_validate(row) for row in rows])
        path = f"/api/v1/builds/{build['id']}/compatibility?expected_version=1"
        result = client.get(path).json()
        assert result["checked_rules_status"] == "PASS"
        from tests.db.test_agent import ask
        from tests.db.test_agent_compatibility import question

        assert (
            ask(client, question(build)).json()["compatibility_reports"][0]["checked_rules_status"]
            == "PASS"
        )
        assert len(result["rules"][2]["evidence"]) == 2
        shorter = proof(str(parts["CASE"]), "max_gpu_length_mm", "234")
        shorter.update(version=2, supersedes_id=rows[-1]["id"])
        with Session(database) as db, db.begin():
            apply(db, [Review.model_validate(shorter)])
        assert client.get(path).json()["checked_rules_status"] == "FAIL"
        assert (
            ask(client, question(build)).json()["compatibility_reports"][0]["checked_rules_status"]
            == "FAIL"
        )
        conflict = proof(str(parts["CASE"]), "max_gpu_length_mm", "236")
        conflict.update(version=3)
        with Session(database) as db, db.begin():
            apply(db, [Review.model_validate(conflict)])
        assert client.get(path).json()["checked_rules_status"] == "UNKNOWN"
        pending = proof(str(parts["CASE"]), "max_gpu_length_mm", "236")
        pending.update(version=4, supersedes_id=shorter["id"], review_status="PENDING")
        with Session(database) as db, db.begin():
            apply(db, [Review.model_validate(pending)])
        assert client.get(path).json()["checked_rules_status"] == "UNKNOWN"
        replacement = {**pending, "id": str(uuid4()), "version": 5, "review_status": "APPROVED"}
        with Session(database) as db, db.begin():
            apply(db, [Review.model_validate(replacement)])
        assert client.get(path).json()["checked_rules_status"] == "PASS"
        # Price completeness stays independent of these checks.
        assert not client.get(f"/api/v1/builds/{build['id']}").json()["price_is_complete"]
