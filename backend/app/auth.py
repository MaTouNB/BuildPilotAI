"""Database-backed opaque sessions and server-derived resource scope."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete, func, select, text

from app.catalog import DB
from app.models import AuthSession, LoginThrottle, Tenant, TenantMember, UserAccount

router = APIRouter(prefix="/api/v1/auth")
COOKIE = "buildpilot_session"
HASHER = PasswordHasher()
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))


def transaction_limits(db):
    db.execute(text("SET LOCAL lock_timeout = '5000'"))
    db.execute(text("SET LOCAL statement_timeout = '5000'"))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def csrf(token):
    return hmac.new(token.encode(), b"buildpilot-csrf-v1", hashlib.sha256).hexdigest()


def guard(request):
    config = request.app.state.settings
    origin = request.headers.get("origin")
    if origin and origin not in config.auth_allowed_origins:
        raise HTTPException(403, "拒绝非受信来源请求")
    allowed_hosts = {urlsplit(value).hostname for value in config.auth_allowed_origins}
    if request.client and request.client.host == "testclient":
        allowed_hosts.add("testserver")
    if (
        request.url.hostname not in allowed_hosts
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise HTTPException(403, "拒绝跨站或非受信主机请求")
    if not config.auth_cookie_secure and (
        request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1", "testclient"}
    ):
        raise HTTPException(403, "非安全Cookie只允许本地开发使用")


def require_csrf(request):
    token = request.cookies.get(COOKIE, "")
    supplied = request.headers.get("x-csrf-token", "")
    if not token or not hmac.compare_digest(supplied.encode(), csrf(token).encode()):
        raise HTTPException(403, "CSRF 校验失败，请刷新身份后重新确认")


@dataclass(frozen=True)
class Actor:
    tenant_id: UUID
    user_id: UUID
    session_hash: str
    role: str
    username: str


def resolve(db, request, *, lock=False):
    guard(request)
    token = request.cookies.get(COOKIE, "")
    if not 32 <= len(token) <= 128:
        raise HTTPException(401, "请先登录")
    query = (
        select(AuthSession, UserAccount, Tenant, TenantMember)
        .join(UserAccount, UserAccount.id == AuthSession.user_id)
        .join(Tenant, Tenant.id == AuthSession.tenant_id)
        .join(
            TenantMember,
            (TenantMember.tenant_id == AuthSession.tenant_id)
            & (TenantMember.user_id == AuthSession.user_id),
        )
        .where(
            AuthSession.token_hash == token_hash(token),
            AuthSession.expires_at > datetime.now(UTC),
            UserAccount.status == "active",
            UserAccount.password_hash.is_not(None),
            Tenant.status == "active",
            TenantMember.status == "active",
        )
    )
    row = db.execute(query.with_for_update() if lock else query).first()
    if row is None:
        raise HTTPException(401, "会话已失效，请重新登录")
    auth_session, user, tenant, membership = row
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_csrf(request)
    return Actor(tenant.id, user.id, auth_session.token_hash, membership.role, user.username)


def current_actor(request: Request, db: DB):
    # Release the read transaction; writes re-resolve and lock auth inside their transaction.
    actor = resolve(db, request)
    db.rollback()
    return actor


Auth = Annotated[Actor, Depends(current_actor)]


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    password: SecretStr = Field(min_length=1, max_length=128)


class SelectTenant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID


class PublicUser(BaseModel):
    id: str
    username: str


class PublicTenant(BaseModel):
    id: str
    name: str
    role: str


class Identity(BaseModel):
    user: PublicUser
    tenant_id: str
    tenants: list[PublicTenant]
    csrf_token: str


def identity(db, actor, token):
    tenants = db.execute(
        select(Tenant, TenantMember.role)
        .join(TenantMember, Tenant.id == TenantMember.tenant_id)
        .where(
            TenantMember.user_id == actor.user_id,
            TenantMember.status == "active",
            Tenant.status == "active",
        )
        .order_by(Tenant.name, Tenant.id)
    ).all()
    return {
        "user": {"id": str(actor.user_id), "username": actor.username},
        "tenant_id": str(actor.tenant_id),
        "tenants": [
            {"id": str(tenant.id), "name": tenant.name, "role": role} for tenant, role in tenants
        ],
        "csrf_token": csrf(token),
    }


def set_session(response, request, token):
    response.set_cookie(
        COOKIE,
        token,
        max_age=request.app.state.settings.auth_session_seconds,
        path="/api/v1",
        httponly=True,
        secure=request.app.state.settings.auth_cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/login", response_model=Identity)
def login(data: Login, request: Request, response: Response, db: DB):
    guard(request)
    error = None
    result = None
    with db.begin():
        transaction_limits(db)
        # Persist rate limit even on invalid credentials. Raw usernames/IPs are not retained.
        bucket = token_hash("login:" + (request.client.host if request.client else "unknown"))
        db.execute(select(func.pg_advisory_xact_lock(int(bucket[:16], 16) - 2**63)))
        throttle = db.get(LoginThrottle, bucket)
        now = datetime.now(UTC)
        if throttle is None:
            throttle = LoginThrottle(bucket=bucket, attempts=0, window_started_at=now)
            db.add(throttle)
        if now - throttle.window_started_at >= timedelta(minutes=5):
            throttle.attempts, throttle.window_started_at = 0, now
        if throttle.attempts >= 20:
            error = HTTPException(429, "登录尝试过多，请稍后重试", headers={"Retry-After": "300"})
        else:
            throttle.attempts += 1
            user = db.scalar(
                select(UserAccount).where(UserAccount.username == data.username).with_for_update()
            )
            try:
                valid = HASHER.verify(
                    user.password_hash if user and user.password_hash else DUMMY_HASH,
                    data.password.get_secret_value(),
                )
            except (VerificationError, InvalidHashError):
                valid = False
            membership = (
                db.execute(
                    select(TenantMember, Tenant)
                    .join(Tenant, Tenant.id == TenantMember.tenant_id)
                    .where(
                        TenantMember.user_id == user.id if user else False,
                        TenantMember.status == "active",
                        Tenant.status == "active",
                    )
                    .order_by(Tenant.id)
                    .with_for_update()
                ).first()
                if user
                else None
            )
            if not valid or user is None or user.status != "active" or membership is None:
                error = HTTPException(401, "用户名或密码错误，或账号不可用")
            else:
                if HASHER.check_needs_rehash(user.password_hash):
                    user.password_hash = HASHER.hash(data.password.get_secret_value())
                old = request.cookies.get(COOKIE)
                if old:
                    db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(old)))
                token = secrets.token_urlsafe(32)
                db.add(
                    AuthSession(
                        token_hash=token_hash(token),
                        user_id=user.id,
                        tenant_id=membership[0].tenant_id,
                        expires_at=now
                        + timedelta(seconds=request.app.state.settings.auth_session_seconds),
                    )
                )
                result = identity(
                    db,
                    Actor(
                        membership[0].tenant_id,
                        user.id,
                        token_hash(token),
                        membership[0].role,
                        user.username,
                    ),
                    token,
                )
                set_session(response, request, token)
    if error:
        raise error
    return result


@router.get("/me", response_model=Identity)
def me(actor: Auth, db: DB, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return identity(db, actor, request.cookies[COOKIE])


@router.post("/logout")
def logout(request: Request, response: Response, db: DB):
    guard(request)
    token = request.cookies.get(COOKIE)
    if token:
        require_csrf(request)
        with db.begin():
            transaction_limits(db)
            db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    response.delete_cookie(
        COOKIE,
        path="/api/v1",
        secure=request.app.state.settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"status": "signed_out"}


@router.post("/select-tenant", response_model=Identity)
def select_tenant(data: SelectTenant, request: Request, response: Response, db: DB):
    with db.begin():
        transaction_limits(db)
        actor = resolve(db, request, lock=True)
        membership = db.scalar(
            select(TenantMember)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(
                TenantMember.user_id == actor.user_id,
                TenantMember.tenant_id == data.tenant_id,
                TenantMember.status == "active",
                Tenant.status == "active",
            )
            .with_for_update()
        )
        if membership is None:
            raise HTTPException(403, "无权选择此租户")
        db.execute(delete(AuthSession).where(AuthSession.token_hash == actor.session_hash))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=token_hash(token),
                user_id=actor.user_id,
                tenant_id=data.tenant_id,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=request.app.state.settings.auth_session_seconds),
            )
        )
        result = identity(
            db,
            Actor(
                data.tenant_id, actor.user_id, token_hash(token), membership.role, actor.username
            ),
            token,
        )
        set_session(response, request, token)
    return result
