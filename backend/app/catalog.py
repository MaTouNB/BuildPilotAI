"""Read-only reviewed catalog. Monetary values are strings, never inferred prices."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import distinct, exists, func, select
from sqlalchemy.orm import Session

from app import catalog_schemas as schemas
from app.db import make_engine
from app.models import (
    DataQualityIssue,
    HardwarePart,
    ImportBatch,
    PartPrice,
    PartSourceMap,
    RawImportRow,
)

router = APIRouter(prefix="/api/v1")
CATEGORIES = {
    "CPU": "处理器",
    "GPU": "显卡",
    "MOTHERBOARD": "主板",
    "MEMORY": "内存",
    "STORAGE": "存储",
    "PSU": "电源",
    "CASE": "机箱",
    "COOLER": "散热器",
}
Category = Literal["CPU", "GPU", "MOTHERBOARD", "MEMORY", "STORAGE", "PSU", "CASE", "COOLER"]


def session(request: Request):
    # Credentials are not needed for liveness; initialize only on a catalog request.
    if request.app.state.catalog_engine is None:
        try:
            with request.app.state.engine_lock:
                if request.app.state.catalog_engine is None:
                    request.app.state.catalog_engine = make_engine(request.app.state.settings)
        except ValueError as exc:
            raise HTTPException(503, "目录数据库未配置") from exc
    with Session(request.app.state.catalog_engine) as db:
        yield db


DB = Annotated[Session, Depends(session)]


def price_json(price: PartPrice):
    return {
        "id": str(price.id),
        "price_usd": str(price.price_usd) if price.price_usd is not None else None,
        "currency": price.currency,
        "price_kind": price.price_kind,
        "dataset_updated_at": price.dataset_updated_at.isoformat(),
        "observed_at": price.observed_at.isoformat() if price.observed_at else None,
        "source_name": price.source_name,
        "quote_status": price.quote_status,
        "is_quotable": price.is_quotable,
        "source_row_id": str(price.source_row_id),
    }


def summaries(db, parts):
    ids = [part.id for part in parts]
    counts = (
        dict(
            db.execute(
                select(PartPrice.part_id, func.count(distinct(PartPrice.price_usd)))
                .where(PartPrice.part_id.in_(ids))
                .group_by(PartPrice.part_id)
            ).all()
        )
        if ids
        else {}
    )
    selected_prices = (
        {
            price.part_id: price
            for price in db.scalars(
                select(PartPrice).where(PartPrice.part_id.in_(ids), PartPrice.is_quotable)
            ).all()
        }
        if ids
        else {}
    )
    result = []
    for part in parts:
        selected = selected_prices.get(part.id)
        valid = counts.get(part.id, 0)
        status = (
            "SELECTED"
            if selected
            else "CONFLICT_REVIEW"
            if valid > 1
            else "UNSELECTED"
            if valid
            else "MISSING"
        )
        result.append(
            {
                "id": str(part.id),
                "category": part.category,
                "name": part.display_name,
                "quality_status": part.quality_status,
                "has_price": bool(valid),
                "is_quotable": selected is not None,
                "quote_status": status,
                "selected_price_id": str(selected.id) if selected else None,
                "price_usd": str(selected.price_usd) if selected else None,
                "price_metadata": price_json(selected) if selected else None,
            }
        )
    return result


def get_part(db, part_id):
    part = db.scalar(select(HardwarePart).where(HardwarePart.id == part_id, HardwarePart.is_active))
    if part is None:
        raise HTTPException(404, "商品不存在或尚未审核")
    return part


@router.get("/parts", response_model=schemas.Page[schemas.Part])
def parts(
    db: DB,
    q: Annotated[str, Query(max_length=120)] = "",
    category: Category | None = None,
    has_price: bool | None = None,
    quotable: bool | None = None,
    min_price_usd: Annotated[Decimal | None, Query(gt=0, max_digits=12, decimal_places=2)] = None,
    max_price_usd: Annotated[Decimal | None, Query(gt=0, max_digits=12, decimal_places=2)] = None,
    sort: Literal["name", "price_asc", "price_desc"] = "name",
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    if min_price_usd is not None and max_price_usd is not None and min_price_usd > max_price_usd:
        raise HTTPException(422, "价格下限不能大于上限")
    selected = (
        select(PartPrice.price_usd)
        .where(PartPrice.part_id == HardwarePart.id, PartPrice.is_quotable)
        .scalar_subquery()
    )
    has = exists().where(PartPrice.part_id == HardwarePart.id, PartPrice.price_usd.is_not(None))
    quoted = exists().where(PartPrice.part_id == HardwarePart.id, PartPrice.is_quotable)
    conditions = [HardwarePart.is_active]
    if q.strip():
        conditions.append(HardwarePart.normalized_name.icontains(q.strip(), autoescape=True))
    if category:
        conditions.append(HardwarePart.category == category)
    if has_price is not None:
        conditions.append(has if has_price else ~has)
    if quotable is not None:
        conditions.append(quoted if quotable else ~quoted)
    if min_price_usd is not None:
        conditions.append(selected >= min_price_usd)
    if max_price_usd is not None:
        conditions.append(selected <= max_price_usd)
    ordering = {
        "name": HardwarePart.normalized_name.asc(),
        "price_asc": selected.asc().nulls_last(),
        "price_desc": selected.desc().nulls_last(),
    }
    total = db.scalar(select(func.count()).select_from(HardwarePart).where(*conditions))
    rows = db.scalars(
        select(HardwarePart)
        .where(*conditions)
        .order_by(ordering[sort], HardwarePart.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": summaries(db, rows), "total": total, "page": page, "page_size": page_size}


@router.get("/parts/{part_id}", response_model=schemas.Detail)
def detail(part_id: UUID, db: DB):
    part = get_part(db, part_id)
    result = summaries(db, [part])[0]
    result["specifications"] = part.specifications
    source_rows = db.execute(
        select(RawImportRow, ImportBatch)
        .join(PartSourceMap, PartSourceMap.raw_import_row_id == RawImportRow.id)
        .join(ImportBatch, ImportBatch.id == RawImportRow.batch_id)
        .where(PartSourceMap.part_id == part_id)
        .order_by(RawImportRow.id)
        .limit(100)
    ).all()
    result["sources"] = [
        {
            "source_row_id": str(row.id),
            "source_name": batch.source_name,
            "source_url": batch.source_url,
            "dataset_updated_at": batch.dataset_updated_at.isoformat(),
            "input_sha256": batch.input_sha256,
            "source_file": row.source_file,
            "row_number": row.row_number,
            "line_number": row.line_number,
            "end_line_number": row.end_line_number,
            "row_hash": row.row_hash,
            "raw_data": row.raw_data,
        }
        for row, batch in source_rows
    ]
    ids = [row.id for row, _ in source_rows]
    issues = db.scalars(
        select(DataQualityIssue)
        .where(DataQualityIssue.raw_import_row_id.in_(ids), DataQualityIssue.resolved_at.is_(None))
        .order_by(DataQualityIssue.id)
        .limit(100)
    ).all()
    result["issues"] = [
        {
            "severity": issue.severity,
            "field": issue.field_name,
            "code": issue.rule_code,
            "message": issue.message,
        }
        for issue in issues
    ]
    result["source_count"] = db.scalar(
        select(func.count()).select_from(PartSourceMap).where(PartSourceMap.part_id == part_id)
    )
    result["evidence_limit"] = 100
    return result


@router.get("/parts/{part_id}/prices", response_model=schemas.Page[schemas.Price])
def prices(
    part_id: UUID,
    db: DB,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    get_part(db, part_id)
    query = select(PartPrice).where(PartPrice.part_id == part_id)
    rows = db.scalars(
        query.order_by(PartPrice.dataset_updated_at.desc(), PartPrice.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    total = db.scalar(
        select(func.count()).select_from(PartPrice).where(PartPrice.part_id == part_id)
    )
    return {
        "items": [price_json(price) for price in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/catalog/categories", response_model=schemas.Categories)
def categories(db: DB):
    counts = dict(
        db.execute(
            select(HardwarePart.category, func.count())
            .where(HardwarePart.is_active)
            .group_by(HardwarePart.category)
        ).all()
    )
    return {
        "items": [
            {"id": key, "name": name, "count": counts.get(key, 0)}
            for key, name in CATEGORIES.items()
        ]
    }


@router.get("/data-sources", response_model=schemas.Sources)
def sources(db: DB):
    batches = db.scalars(
        select(ImportBatch)
        .where(ImportBatch.status == "SUCCEEDED")
        .order_by(ImportBatch.finished_at.desc(), ImportBatch.id)
        .limit(100)
    ).all()
    reviewed = db.scalar(
        select(func.count()).select_from(HardwarePart).where(HardwarePart.is_active)
    )
    return {
        "reviewed_parts": reviewed,
        "items": [
            {
                "id": str(batch.id),
                "name": batch.source_name,
                "url": batch.source_url,
                "dataset_updated_at": batch.dataset_updated_at.isoformat(),
                "input_sha256": batch.input_sha256,
                "pipeline_version": batch.etl_version,
                "row_count": batch.row_count,
                "currency": "USD",
                "price_kind": "historical_reference",
            }
            for batch in batches
        ],
        "limit": 100,
    }
