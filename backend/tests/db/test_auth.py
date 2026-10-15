from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, update

from app.auth import COOKIE, HASHER, token_hash
from app.builds import DEMO_TENANT, DEMO_USER
from app.config import Settings
from app.main import create_app
from app.models import AuthSession, Build, Tenant, TenantMember, UserAccount

pytestmark = pytest.mark.postgres
PASSWORD = "synthetic-auth-fixture-password"
PASSWORD_HASH = HASHER.hash(PASSWORD)


@pytest.fixture
def accounts(database):
    tenants = [uuid4(), uuid4()]
    users = [uuid4(), uuid4(), uuid4()]
    with database.begin() as db:
        for i, tenant in enumerate(tenants):
            db.execute(insert(Tenant), dict(id=tenant, name=f"Fixture {i}", status="active"))
        for i, user in enumerate(users):
            db.execute(
                insert(UserAccount),
                dict(
                    id=user,
                    username=f"fixture-user-{i}",
                    password_hash=PASSWORD_HASH,
                    status="active",
                ),
            )
        for tenant, user, role in [
            (tenants[0], users[0], "admin"),
            (tenants[1], users[0], "member"),
            (tenants[0], users[1], "member"),
            (tenants[1], users[2], "member"),
        ]:
            db.execute(
                insert(TenantMember),
                dict(tenant_id=tenant, user_id=user, role=role, status="active"),
            )
    return tenants, users


def login(database, index=0):
    client = TestClient(
        create_app(Settings(_env_file=None, auth_cookie_secure=False), engine=database)
    )
    response = client.post(
        "/api/v1/auth/login", json={"username": f"fixture-user-{index}", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def new(client, name="fixture"):
    response = client.post(
        "/api/v1/builds", json={"name": name}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_password_cookie_no_plaintext_and_logout(accounts, database):
    client = login(database)
    token = client.cookies.get(COOKIE)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and PASSWORD not in me.text
    assert me.headers["Cache-Control"] == "no-store"
    with database.connect() as db:
        row = db.execute(select(AuthSession.__table__)).mappings().one()
        assert row["token_hash"] == token_hash(token) and row["token_hash"] != token
        stored = db.scalar(
            select(UserAccount.password_hash).where(UserAccount.id == accounts[1][0])
        )
        assert stored.startswith("$argon2id$") and PASSWORD not in stored
    build = new(client)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    assert client.get(f"/api/v1/builds/{build['id']}").status_code == 401
    client.cookies.set(COOKIE, token, path="/api/v1")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_three_accounts_same_tenant_and_cross_tenant_idor(accounts, database):
    tenants, users = accounts
    a, b, c = login(database, 0), login(database, 1), login(database, 2)
    # Choose a known authorized tenant explicitly; selected identity is never a build argument.
    switched = a.post("/api/v1/auth/select-tenant", json={"tenant_id": str(tenants[0])})
    assert switched.status_code == 200
    a.headers["X-CSRF-Token"] = switched.json()["csrf_token"]
    build = new(a)
    for other in [b, c]:
        assert other.get("/api/v1/builds").json()["total"] == 0
        path = f"/api/v1/builds/{build['id']}"
        assert other.get(path).status_code == 404
        for method, suffix, body in [
            ("PATCH", "", {"name": "stolen", "expected_version": 1}),
            ("DELETE", "", {"expected_version": 1}),
            ("DELETE", "/items/CPU", {"expected_version": 1}),
            (
                "PUT",
                "/items/CPU",
                {
                    "part_id": str(uuid4()),
                    "quantity": 1,
                    "expected_price_id": None,
                    "expected_version": 1,
                },
            ),
        ]:
            assert (
                other.request(
                    method, path + suffix, json=body, headers={"Idempotency-Key": str(uuid4())}
                ).status_code
                == 404
            )
    assert a.get(f"/api/v1/builds/{build['id']}").json()["version"] == 1
    assert (
        b.post("/api/v1/auth/select-tenant", json={"tenant_id": str(tenants[1])}).status_code == 403
    )
    for field in ["owner_user_id", "tenant_id", "user_id"]:
        assert (
            a.post(
                "/api/v1/builds",
                json={"name": "fake", field: str(users[1])},
                headers={"Idempotency-Key": str(uuid4())},
            ).status_code
            == 422
        )


def test_tenant_rotation_and_replay_scope(accounts, database):
    tenants, _ = accounts
    client = login(database)
    selected = client.post("/api/v1/auth/select-tenant", json={"tenant_id": str(tenants[0])})
    client.headers["X-CSRF-Token"] = selected.json()["csrf_token"]
    key = {"Idempotency-Key": str(uuid4())}
    first = client.post("/api/v1/builds", json={"name": "scope-a"}, headers=key)
    old_token = client.cookies.get(COOKIE)
    selected = client.post("/api/v1/auth/select-tenant", json={"tenant_id": str(tenants[1])})
    assert client.cookies.get(COOKIE) != old_token
    client.headers["X-CSRF-Token"] = selected.json()["csrf_token"]
    assert client.get(f"/api/v1/builds/{first.json()['id']}").status_code == 404
    second = client.post("/api/v1/builds", json={"name": "scope-a"}, headers=key)
    assert second.status_code == 201 and second.json()["id"] != first.json()["id"]
    with TestClient(
        create_app(Settings(_env_file=None, auth_cookie_secure=False), engine=database)
    ) as old:
        old.cookies.set(COOKIE, old_token, path="/api/v1")
        assert old.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("target", ["session", "user", "tenant", "member"])
def test_expiry_and_revocation_rechecked(accounts, database, target):
    tenants, users = accounts
    client = login(database)
    tenant = client.get("/api/v1/auth/me").json()["tenant_id"]
    build = new(client)
    with database.begin() as db:
        if target == "session":
            db.execute(
                update(AuthSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        elif target == "user":
            db.execute(
                update(UserAccount).where(UserAccount.id == users[0]).values(status="disabled")
            )
        elif target == "tenant":
            db.execute(update(Tenant).where(Tenant.id == tenant).values(status="disabled"))
        else:
            db.execute(
                update(TenantMember)
                .where(TenantMember.user_id == users[0], TenantMember.tenant_id == tenant)
                .values(status="revoked")
            )
    assert client.get(f"/api/v1/builds/{build['id']}").status_code == 401
    assert (
        client.request(
            "DELETE",
            f"/api/v1/builds/{build['id']}",
            json={"expected_version": 1},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 401
    )


def test_csrf_origin_and_forged_credentials(accounts, database):
    client = login(database)
    csrf_token = client.headers.pop("X-CSRF-Token")
    assert (
        client.post(
            "/api/v1/builds", json={"name": "x"}, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 403
    )
    client.headers["X-CSRF-Token"] = "forged"
    assert client.post("/api/v1/auth/logout").status_code == 403
    client.headers["X-CSRF-Token"] = csrf_token
    assert (
        client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.test"}).status_code
        == 403
    )
    with TestClient(
        create_app(
            Settings(_env_file=None, auth_cookie_secure=False, demo_enabled=True), engine=database
        )
    ) as stranger:
        assert (
            stranger.get(
                "/api/v1/builds",
                headers={"X-User-ID": str(accounts[1][0]), "X-Tenant-ID": str(accounts[0][0])},
            ).status_code
            == 401
        )
        stranger.cookies.set(COOKIE, "x" * 43, path="/api/v1")
        assert stranger.get("/api/v1/auth/me").status_code == 401


def test_login_failures_rate_limit_secure_cookie_and_rotation(accounts, database):
    client = login(database)
    original = client.cookies.get(COOKIE)
    second = client.post(
        "/api/v1/auth/login", json={"username": "fixture-user-0", "password": PASSWORD}
    )
    assert "httponly" in second.headers["set-cookie"].lower()
    assert "samesite=strict" in second.headers["set-cookie"].lower()
    assert original != client.cookies.get(COOKIE)
    for _ in range(18):
        response = client.post(
            "/api/v1/auth/login", json={"username": "fixture-user-0", "password": "wrong"}
        )
        assert response.status_code == 401
        assert "wrong" not in response.text
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "nonexistent", "password": "wrong"}
        ).status_code
        == 429
    )
    secure = TestClient(
        create_app(Settings(_env_file=None), engine=database), base_url="https://localhost"
    )
    # Different peer bucket for this isolated Secure-attribute check.
    with database.begin() as db:
        from app.models import LoginThrottle

        db.execute(delete(LoginThrottle))
    response = secure.post(
        "/api/v1/auth/login", json={"username": "fixture-user-0", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert "; secure" in response.headers["set-cookie"].lower()


def test_existing_demo_preserved_and_inaccessible(accounts, database):
    old_id = uuid4()
    with database.begin() as db:
        db.execute(
            insert(Build),
            dict(
                id=old_id,
                tenant_id=DEMO_TENANT,
                owner_user_id=DEMO_USER,
                name="legacy demo",
                purpose="",
                budget_currency="USD",
                version=4,
                status="draft",
            ),
        )
    client = login(database)
    assert client.get(f"/api/v1/builds/{old_id}").status_code == 404
    assert client.get("/api/v1/builds").json()["total"] == 0
    with database.connect() as db:
        row = db.execute(
            select(Build.tenant_id, Build.owner_user_id, Build.version).where(Build.id == old_id)
        ).one()
        assert row == (DEMO_TENANT, DEMO_USER, 4)


def test_replay_reauthorizes_resource_owner(accounts, database):
    tenants, users = accounts
    client = login(database)
    identity = client.post("/api/v1/auth/select-tenant", json={"tenant_id": str(tenants[0])}).json()
    client.headers["X-CSRF-Token"] = identity["csrf_token"]
    key = {"Idempotency-Key": str(uuid4())}
    created = client.post("/api/v1/builds", json={"name": "ownership fixture"}, headers=key)
    with database.begin() as db:
        db.execute(
            update(Build).where(Build.id == created.json()["id"]).values(owner_user_id=users[1])
        )
    assert (
        client.post("/api/v1/builds", json={"name": "ownership fixture"}, headers=key).status_code
        == 404
    )


def test_validation_never_echoes_password(accounts, database):
    client = login(database)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "INVALID USER", "password": "private-input-never-echo"},
    )
    assert response.status_code == 422
    assert "private-input-never-echo" not in response.text
    assert "input" not in response.json()["error"].get("details", [{}])[0]
