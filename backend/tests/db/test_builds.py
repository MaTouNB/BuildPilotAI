from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app.auth import HASHER
from app.builds import SLOTS
from app.config import Settings
from app.main import create_app
from app.models import (
    Build,
    BuildItem,
    HardwarePart,
    PartPrice,
    RawImportRow,
    Tenant,
    TenantMember,
    UserAccount,
)

pytestmark = pytest.mark.postgres
DEMO_TENANT = UUID("00000000-0000-4000-8000-000000000091")
DEMO_USER = UUID("00000000-0000-4000-8000-000000000092")
TEST_PASSWORD = "synthetic-regression-password"
TEST_HASH = HASHER.hash(TEST_PASSWORD)


def client_for(database, enabled=True):
    client = TestClient(
        create_app(Settings(_env_file=None, auth_cookie_secure=False), engine=database)
    )
    if enabled:
        with database.begin() as db:
            if db.scalar(select(UserAccount.id).where(UserAccount.id == DEMO_USER)) is None:
                db.execute(insert(Tenant), dict(id=DEMO_TENANT, name="regression", status="active"))
                db.execute(
                    insert(UserAccount),
                    dict(
                        id=DEMO_USER,
                        username="regression",
                        password_hash=TEST_HASH,
                        status="active",
                    ),
                )
                db.execute(
                    insert(TenantMember),
                    dict(tenant_id=DEMO_TENANT, user_id=DEMO_USER, role="member", status="active"),
                )
        response = client.post(
            "/api/v1/auth/login", json={"username": "regression", "password": TEST_PASSWORD}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def headers(key=None):
    return {"Idempotency-Key": str(key or uuid4())}


def new(client, **fields):
    result = client.post(
        "/api/v1/builds", json={"name": "Synthetic build", **fields}, headers=headers()
    )
    assert result.status_code == 201, result.text
    return result.json()


def put(client, build, part, slot="CPU", quantity=1, **fields):
    return client.put(
        f"/api/v1/builds/{build['id']}/items/{slot}",
        json={
            "part_id": part["id"],
            "quantity": quantity,
            "expected_price_id": part["selected_price_id"],
            "expected_version": build["version"],
            **fields,
        },
        headers=headers(),
    )


def test_empty_complete_prices_incomplete_build_and_persistence(catalog, database):
    catalog_client, ids = catalog
    part = catalog_client.get(f"/api/v1/parts/{ids[0]}").json()
    with client_for(database) as client:
        build = new(client, budget_amount="10.00")
        assert build["total_usd"] == "0.00"
        assert not build["price_is_complete"] and not build["build_is_complete"]
        response = put(client, build, part)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["total_usd"] == "10.25" and saved["over_budget"]
        assert saved["price_is_complete"] and not saved["total_is_complete"]
        assert "MOTHERBOARD" in saved["missing_components"] and saved["pending_requirements"]
    with client_for(database) as restarted:
        assert restarted.get(f"/api/v1/builds/{build['id']}").json() == saved
        assert restarted.get("/api/v1/builds").json()["total"] == 1


def test_null_snapshot_slot_quantity_and_amount_validation(catalog, database):
    reader, ids = catalog
    missing = reader.get(f"/api/v1/parts/{ids[3]}").json()
    selected = reader.get(f"/api/v1/parts/{ids[0]}").json()
    with client_for(database) as client:
        build = new(client)
        null = put(client, build, missing).json()
        assert null["unpriced_slots"] == ["CPU"] and not null["price_is_complete"]
        assert null["items"][0]["price_snapshot_usd"] is None
        for quantity in [0, -1, 1.5, True, 100]:
            assert put(client, null, selected, quantity=quantity).status_code == 422
        assert put(client, null, selected, slot="GPU").status_code == 422
        assert put(client, null, selected, price_snapshot_usd="0.01").status_code == 422
        assert put(client, null, selected, expected_price_id=str(uuid4())).status_code == 409
        assert client.get(f"/api/v1/builds/{build['id']}").json()["version"] == 2
        for fields in [
            {"budget_currency": "CNY"},
            {"budget_amount": "NaN"},
            {"budget_amount": "0"},
            {"owner_user_id": str(uuid4())},
            {"tenant_id": str(uuid4())},
            {"name": " "},
        ]:
            assert (
                client.post(
                    "/api/v1/builds", json={"name": "x", **fields}, headers=headers()
                ).status_code
                == 422
            )


def test_snapshot_survives_price_change_quantity_and_replacement(catalog, database):
    reader, ids = catalog
    part = reader.get(f"/api/v1/parts/{ids[0]}").json()
    unselected = reader.get(f"/api/v1/parts/{ids[2]}").json()
    with client_for(database) as client:
        build = put(client, new(client), part).json()
        with database.begin() as db:
            db.execute(
                update(PartPrice).where(PartPrice.part_id == ids[0]).values(is_quotable=False)
            )
        with database.begin() as db:
            raw = dict(
                db.execute(
                    select(RawImportRow.__table__).where(RawImportRow.normalized_part_id == ids[0])
                )
                .mappings()
                .one()
            )
            raw.update(id=uuid4(), row_number=150, line_number=151, end_line_number=151)
            db.execute(insert(RawImportRow), raw)
            replacement = dict(
                db.execute(select(PartPrice.__table__).where(PartPrice.part_id == ids[0]))
                .mappings()
                .one()
            )
            replacement.update(
                id=uuid4(), source_row_id=raw["id"], price_usd=Decimal("99.99"), is_quotable=True
            )
            db.execute(insert(PartPrice), replacement)
        assert reader.get(f"/api/v1/parts/{ids[0]}").json()["price_usd"] == "99.99"
        unchanged = client.get(f"/api/v1/builds/{build['id']}").json()
        assert unchanged["total_usd"] == "10.25"
        changed = put(client, unchanged, part, quantity=3).json()
        assert (
            changed["total_usd"] == "30.75"
            and changed["items"][0]["part_price_id"] == part["selected_price_id"]
        )
        replaced = put(client, changed, unselected).json()
        assert replaced["items"][0]["price_snapshot_usd"] is None
        assert replaced["total_usd"] == "0.00" and replaced["unpriced_slots"] == ["CPU"]


def test_version_conflict_replay_update_remove_archive(catalog, database):
    reader, ids = catalog
    part = reader.get(f"/api/v1/parts/{ids[0]}").json()
    with client_for(database) as client:
        key = headers()
        created = client.post("/api/v1/builds", json={"name": "original"}, headers=key)
        assert (
            client.post("/api/v1/builds", json={"name": "original"}, headers=key).json()
            == created.json()
        )
        assert (
            client.post("/api/v1/builds", json={"name": "different"}, headers=key).status_code
            == 409
        )
        build = created.json()
        url = f"/api/v1/builds/{build['id']}"
        edit_key = headers()
        data = {
            "name": "renamed",
            "purpose": "fixture",
            "budget_amount": "100",
            "expected_version": 1,
        }
        updated = client.patch(url, json=data, headers=edit_key)
        assert updated.status_code == 200
        assert client.patch(url, json=data, headers=edit_key).json() == updated.json()
        assert client.patch(url, json=data, headers=headers()).status_code == 409
        added = put(client, updated.json(), part).json()
        deleted = client.request(
            "DELETE",
            url + "/items/CPU",
            json={"expected_version": added["version"]},
            headers=headers(),
        )
        assert deleted.status_code == 200 and not deleted.json()["items"]
        archived = client.request(
            "DELETE", url, json={"expected_version": deleted.json()["version"]}, headers=headers()
        )
        assert archived.status_code == 200 and archived.json()["status"] == "archived"
        assert client.get(url).status_code == 404
        assert client.get("/api/v1/builds").json()["total"] == 0


def test_demo_gate_origin_and_owner_scope(catalog, database):
    _, _ids = catalog
    with client_for(database, False) as client:
        assert client.get("/api/v1/builds").status_code == 401
        assert (
            client.post("/api/v1/builds", json={"name": "x"}, headers=headers()).status_code == 401
        )
    with client_for(database) as client:
        assert (
            client.get("/api/v1/builds", headers={"Origin": "https://evil.test"}).status_code == 403
        )
        assert client.get("/api/v1/builds", headers={"Host": "public.test"}).status_code == 403
        with database.begin() as db:
            tenant, user, build_id = uuid4(), uuid4(), uuid4()
            db.execute(insert(Tenant), dict(id=tenant, name="other", status="active"))
            db.execute(insert(UserAccount), dict(id=user, username=str(user), status="active"))
            db.execute(
                insert(Build),
                dict(
                    id=build_id,
                    tenant_id=tenant,
                    owner_user_id=user,
                    name="private",
                    purpose="",
                    budget_currency="USD",
                    version=1,
                    status="draft",
                ),
            )
        assert client.get(f"/api/v1/builds/{build_id}").status_code == 404
        assert (
            client.request(
                "DELETE",
                f"/api/v1/builds/{build_id}",
                json={"expected_version": 1},
                headers=headers(),
            ).status_code
            == 404
        )
        assert client.get("/api/v1/builds").json()["total"] == 0


def test_concurrent_version_and_idempotent_create(catalog, database):
    with client_for(database) as client:
        build = new(client)
        url = f"/api/v1/builds/{build['id']}"

        def edit(index):
            return client.patch(
                url, json={"name": str(index), "expected_version": 1}, headers=headers()
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(edit, range(2))) == [200, 409]
        key = headers()

        def create(_):
            return client.post("/api/v1/builds", json={"name": "same-request"}, headers=key).json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        assert results[0] == results[1]
        assert client.get("/api/v1/builds").json()["total"] == 2


def test_complete_eight_slots_decimal_and_memory_kit(catalog, database):
    with client_for(database):
        pass
    with database.begin() as db:
        build = Build(
            id=uuid4(),
            tenant_id=DEMO_TENANT,
            owner_user_id=DEMO_USER,
            name="complete synthetic fixture",
            purpose="",
            budget_currency="USD",
            version=1,
            status="draft",
        )
        db.execute(
            insert(Build),
            {
                "id": build.id,
                "tenant_id": DEMO_TENANT,
                "owner_user_id": DEMO_USER,
                "name": build.name,
                "purpose": "",
                "budget_currency": "USD",
                "version": 1,
                "status": "draft",
            },
        )
        # Category-correct fixtures; quantity counts kits, not memory modules.
        source = dict(db.execute(select(RawImportRow.__table__).limit(1)).mappings().one())
        for index, slot in enumerate(SLOTS):
            pid, rid, priceid = uuid4(), uuid4(), uuid4()
            db.execute(
                insert(HardwarePart),
                dict(
                    id=pid,
                    category=slot,
                    display_name=slot,
                    normalized_name=slot,
                    specifications={"modules": 2} if slot == "MEMORY" else {},
                    is_active=True,
                    quality_status="VALID",
                    reviewed_by="synthetic",
                    review_reason="fixture",
                ),
            )
            raw = {
                **source,
                "id": rid,
                "row_number": 200 + index,
                "line_number": 201 + index,
                "end_line_number": 201 + index,
                "normalized_part_id": pid,
            }
            db.execute(insert(RawImportRow), raw)
            db.execute(
                insert(PartPrice),
                dict(
                    id=priceid,
                    part_id=pid,
                    batch_id=raw["batch_id"],
                    source_row_id=rid,
                    price_usd=Decimal("0.10"),
                    currency="USD",
                    price_kind="historical_reference",
                    dataset_updated_at=date(2025, 7, 23),
                    source_name="synthetic",
                    quote_status="SELECTED",
                    is_quotable=True,
                    selected_by="synthetic",
                    selection_reason="fixture",
                ),
            )
            db.execute(
                insert(BuildItem),
                dict(
                    id=uuid4(),
                    build_id=build.id,
                    slot=slot,
                    part_id=pid,
                    part_name=slot,
                    quantity=1,
                    part_price_id=priceid,
                    price_snapshot_usd=Decimal("0.10"),
                    snapshot_currency="USD",
                    price_source="synthetic",
                    price_dataset_updated_at=date(2025, 7, 23),
                ),
            )
    with client_for(database) as client:
        data = client.get(f"/api/v1/builds/{build.id}").json()
        assert data["total_usd"] == "0.80" and data["total_is_complete"]
        assert data["checked_rules_status"] == "UNKNOWN"
        assert not data["pending_requirements"] and not data["missing_components"]


def test_missing_cpu_and_unknown_display_cooling_are_separate(catalog, database):
    reader, ids = catalog
    gpu = reader.get(f"/api/v1/parts/{ids[1]}").json()
    with client_for(database) as client:
        build = put(client, new(client), gpu, slot="GPU").json()
        assert build["price_is_complete"] and "CPU" in build["missing_components"]
        assert not build["build_is_complete"] and not build["total_is_complete"]
        assert len(build["pending_requirements"]) == 1


def test_disabled_demo_blocks_existing_replay(catalog, database):
    key = headers()
    with client_for(database) as client:
        build = client.post("/api/v1/builds", json={"name": "demo-owned"}, headers=key).json()
    with database.connect() as db:
        row = db.execute(
            select(Build.tenant_id, Build.owner_user_id).where(Build.id == build["id"])
        ).one()
        assert row == (DEMO_TENANT, DEMO_USER)
    with client_for(database, False) as client:
        assert client.get(f"/api/v1/builds/{build['id']}").status_code == 401
        assert (
            client.post("/api/v1/builds", json={"name": "demo-owned"}, headers=key).status_code
            == 401
        )
    with database.connect() as db:
        assert (
            db.execute(
                select(Build.tenant_id, Build.owner_user_id).where(Build.id == build["id"])
            ).one()
            == row
        )
