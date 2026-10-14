"""Local, explicit Demo workspace. No Agent access and no multi-user authentication claim."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import func, select, text

from app.catalog import DB, Category, get_part
from app.etl.source import fingerprint
from app.models import Build, BuildItem, BuildRequest, PartPrice, Tenant, UserAccount

router = APIRouter(prefix="/api/v1/builds")
DEMO_TENANT = UUID("00000000-0000-4000-8000-000000000001")
DEMO_USER = UUID("00000000-0000-4000-8000-000000000002")
SLOTS = ["CPU", "MOTHERBOARD", "MEMORY", "STORAGE", "PSU", "CASE", "GPU", "COOLER"]
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


def demo(request: Request):
    if not request.app.state.settings.demo_enabled:
        raise HTTPException(401, "本地 Demo 已关闭，装机接口需要登录")
    host = request.url.hostname
    if (
        host not in {"127.0.0.1", "localhost", "::1", "testserver"}
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1", "testclient"}
    ):
        raise HTTPException(403, "Demo 装机功能只允许本地访问")
    origin = request.headers.get("origin")
    if origin and origin not in {
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    }:
        raise HTTPException(403, "拒绝跨站 Demo 请求")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "拒绝跨站 Demo 请求")
    # Never read identity from headers, body, query or user-controlled model context.
    return DEMO_TENANT, DEMO_USER


router.dependencies = [Depends(demo)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Input):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    purpose: Annotated[str, Field(max_length=500)] = ""
    budget_amount: Money | None = None
    budget_currency: Literal["USD"] = "USD"

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class Edit(Create):
    expected_version: Annotated[StrictInt, Field(ge=1)]


class Version(Input):
    expected_version: Annotated[StrictInt, Field(ge=1)]


class ItemEdit(Version):
    part_id: UUID
    expected_price_id: UUID | None
    quantity: Annotated[StrictInt, Field(ge=1, le=99)] = 1


class Item(BaseModel):
    slot: str
    part_id: str
    part_name: str
    quantity: int
    part_price_id: str | None
    price_snapshot_usd: str | None
    currency: str | None
    source_name: str | None
    dataset_updated_at: str | None
    subtotal_usd: str | None


class Result(BaseModel):
    id: str
    name: str
    purpose: str
    budget_amount: str | None
    budget_currency: str
    version: int
    status: str
    items: list[Item]
    total_usd: str
    price_is_complete: bool
    build_is_complete: bool
    total_is_complete: bool
    over_budget: bool
    missing_components: list[str]
    unpriced_slots: list[str]
    pending_requirements: list[str]
    checked_rules_status: Literal["UNKNOWN"]


class Results(BaseModel):
    items: list[Result]
    total: int
    page: int
    page_size: int


def render(db, build):
    rows = db.scalars(select(BuildItem).where(BuildItem.build_id == build.id)).all()
    rows.sort(key=lambda row: SLOTS.index(row.slot))
    items = [
        {
            "slot": row.slot,
            "part_id": str(row.part_id),
            "part_name": row.part_name,
            "quantity": row.quantity,
            "part_price_id": str(row.part_price_id) if row.part_price_id else None,
            "price_snapshot_usd": str(row.price_snapshot_usd)
            if row.price_snapshot_usd is not None
            else None,
            "currency": row.snapshot_currency,
            "source_name": row.price_source,
            "dataset_updated_at": row.price_dataset_updated_at.isoformat()
            if row.price_dataset_updated_at
            else None,
            "subtotal_usd": str(row.price_snapshot_usd * row.quantity)
            if row.price_snapshot_usd is not None
            else None,
        }
        for row in rows
    ]
    total = sum(
        (
            row.price_snapshot_usd * row.quantity
            for row in rows
            if row.price_snapshot_usd is not None
        ),
        Decimal("0.00"),
    )
    slots = {row.slot for row in rows}
    missing = [slot for slot in SLOTS[:6] if slot not in slots]
    pending = []
    if "GPU" not in slots:
        pending.append("缺少显卡，且没有经审核的可用集成显示方案证据")
    if "COOLER" not in slots:
        pending.append("缺少散热器，且没有经审核的已有散热方案证据")
    unpriced = [row.slot for row in rows if row.price_snapshot_usd is None]
    price_complete = bool(rows) and not unpriced
    build_complete = not missing and not pending
    return {
        "id": str(build.id),
        "name": build.name,
        "purpose": build.purpose,
        "budget_amount": str(build.budget_amount) if build.budget_amount is not None else None,
        "budget_currency": "USD",
        "version": build.version,
        "status": build.status,
        "items": items,
        "total_usd": str(total),
        "price_is_complete": price_complete,
        "build_is_complete": build_complete,
        "total_is_complete": price_complete and build_complete,
        "over_budget": build.budget_amount is not None and total > build.budget_amount,
        "missing_components": missing,
        "unpriced_slots": unpriced,
        "pending_requirements": pending,
        "checked_rules_status": "UNKNOWN",
    }


def owned(db, build_id, *, lock=False):
    query = select(Build).where(
        Build.id == build_id,
        Build.tenant_id == DEMO_TENANT,
        Build.owner_user_id == DEMO_USER,
        Build.status == "draft",
    )
    build = db.scalar(query.with_for_update() if lock else query)
    if build is None:
        raise HTTPException(404, "方案不存在或已归档")
    return build


def write(db, key, method, path, data, action):
    # One transaction holds request-key lock, build-version lock, snapshots and replay response.
    with db.begin():
        db.execute(text("SET LOCAL lock_timeout = '5000'"))
        db.execute(text("SET LOCAL statement_timeout = '5000'"))
        tenant, user = db.get(Tenant, DEMO_TENANT), db.get(UserAccount, DEMO_USER)
        if tenant is None or user is None or tenant.status != "demo" or user.status != "demo":
            raise HTTPException(403, "Demo 身份不可用")
        digest = hashlib.sha256(f"{DEMO_TENANT}:{DEMO_USER}:{key}".encode()).digest()
        db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], signed=True))))
        fingerprint = hashlib.sha256(
            json.dumps(
                {"method": method, "path": path, "data": data.model_dump(mode="json")},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        previous = db.scalar(
            select(BuildRequest).where(
                BuildRequest.tenant_id == DEMO_TENANT,
                BuildRequest.owner_user_id == DEMO_USER,
                BuildRequest.request_key == key,
            )
        )
        if previous:
            if previous.fingerprint != fingerprint:
                raise HTTPException(409, "幂等键已用于不同请求")
            return previous.response
        build = action()
        db.flush()
        response = render(db, build)
        db.add(
            BuildRequest(
                id=uuid4(),
                tenant_id=DEMO_TENANT,
                owner_user_id=DEMO_USER,
                request_key=key,
                fingerprint=fingerprint,
                response=response,
            )
        )
        return response


def change_version(build, expected):
    if build.version != expected:
        raise HTTPException(409, "方案版本已变化，请重新加载并核对修改")
    build.version += 1
    build.updated_at = datetime.now(UTC)


@router.get("", response_model=Results)
def list_builds(
    db: DB,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    conditions = [
        Build.tenant_id == DEMO_TENANT,
        Build.owner_user_id == DEMO_USER,
        Build.status == "draft",
    ]
    total = db.scalar(select(func.count()).select_from(Build).where(*conditions))
    builds = db.scalars(
        select(Build)
        .where(*conditions)
        .order_by(Build.created_at.desc(), Build.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [render(db, build) for build in builds],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Result, status_code=201)
def create(data: Create, db: DB, key: Key):
    def action():
        build = Build(
            id=uuid4(),
            tenant_id=DEMO_TENANT,
            owner_user_id=DEMO_USER,
            name=data.name,
            purpose=data.purpose,
            budget_amount=data.budget_amount,
            budget_currency="USD",
            version=1,
            status="draft",
        )
        db.add(build)
        return build

    return write(db, key, "POST", "", data, action)


@router.get("/{build_id}", response_model=Result)
def get_build(build_id: UUID, db: DB):
    return render(db, owned(db, build_id))


@router.patch("/{build_id}", response_model=Result)
def edit(build_id: UUID, data: Edit, db: DB, key: Key):
    def action():
        build = owned(db, build_id, lock=True)
        change_version(build, data.expected_version)
        build.name, build.purpose, build.budget_amount = data.name, data.purpose, data.budget_amount
        return build

    return write(db, key, "PATCH", str(build_id), data, action)


@router.put("/{build_id}/items/{slot}", response_model=Result)
def put_item(build_id: UUID, slot: Category, data: ItemEdit, db: DB, key: Key):
    def action():
        build = owned(db, build_id, lock=True)
        change_version(build, data.expected_version)
        # Share the ETL selection lock, so quote expectation and snapshot are atomic.
        price_lock = int(fingerprint(["price-selection", str(data.part_id)])[:16], 16) - 2**63
        db.execute(select(func.pg_advisory_xact_lock(price_lock)))
        part = get_part(db, data.part_id)
        if part.category != slot or not part.is_active:
            raise HTTPException(422, "配件类别与槽位不匹配或配件已停用")
        item = db.scalar(
            select(BuildItem).where(BuildItem.build_id == build_id, BuildItem.slot == slot)
        )
        if item and item.part_id == part.id:
            if item.part_price_id != data.expected_price_id:
                raise HTTPException(409, "原快照与已确认修改不一致，请重新加载")
            item.quantity = data.quantity
            return build
        price = db.scalar(
            select(PartPrice).where(PartPrice.part_id == part.id, PartPrice.is_quotable)
        )
        if (price.id if price else None) != data.expected_price_id:
            raise HTTPException(409, "目录选价已变化，请重新核对 Diff")
        if item:
            db.delete(item)
            db.flush()
        db.add(
            BuildItem(
                id=uuid4(),
                build_id=build_id,
                slot=slot,
                part_id=part.id,
                part_name=part.display_name,
                quantity=data.quantity,
                part_price_id=price.id if price else None,
                price_snapshot_usd=price.price_usd if price else None,
                snapshot_currency=price.currency if price else None,
                price_source=price.source_name if price else None,
                price_dataset_updated_at=price.dataset_updated_at if price else None,
            )
        )
        return build

    return write(db, key, "PUT", f"{build_id}/items/{slot}", data, action)


@router.delete("/{build_id}/items/{slot}", response_model=Result)
def delete_item(build_id: UUID, slot: Category, data: Version, db: DB, key: Key):
    def action():
        build = owned(db, build_id, lock=True)
        change_version(build, data.expected_version)
        item = db.scalar(
            select(BuildItem).where(BuildItem.build_id == build_id, BuildItem.slot == slot)
        )
        if item is None:
            raise HTTPException(404, "槽位没有配件")
        db.delete(item)
        return build

    return write(db, key, "DELETE", f"{build_id}/items/{slot}", data, action)


@router.delete("/{build_id}", response_model=Result)
def archive(build_id: UUID, data: Version, db: DB, key: Key):
    def action():
        build = owned(db, build_id, lock=True)
        change_version(build, data.expected_version)
        build.status = "archived"
        return build

    return write(db, key, "DELETE", str(build_id), data, action)
