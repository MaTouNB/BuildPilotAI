"""Allowlisted LangChain tools; sessions and identity never come from model arguments."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import resolve
from app.builds import owned
from app.catalog import Category, get_part, summaries
from app.compatibility import report
from app.models import HardwarePart, ImportBatch, PartPrice, PartSourceMap, RawImportRow


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Search(Input):
    keywords: Annotated[str, Field(min_length=1, max_length=120)]
    category: Category | None = None
    priced_only: StrictBool = False
    limit: Annotated[int, Field(strict=True, ge=1, le=5)] = 5

    @field_validator("keywords")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("查询不能为空")
        return value.strip()


class PartID(Input):
    part_id: UUID


class BuildID(Input):
    build_id: UUID
    expected_version: Annotated[int, Field(strict=True, ge=1)]


def authorizer(request, actor):
    """Capture trusted authentication, never model-supplied identity or resource scope."""

    def authorize(db):
        if resolve(db, request, lock=True) != actor:
            raise HTTPException(401, "身份已变化，请重新登录")
        return actor

    return authorize


def toolset(engine, *, authorize=None):
    def search_parts(keywords, category=None, priced_only=False, limit=5):
        """Search reviewed public catalog by literal substring; maximum five candidates."""
        with Session(engine) as db, db.begin():
            readonly(db)
            query = select(HardwarePart).where(
                HardwarePart.is_active,
                HardwarePart.normalized_name.icontains(keywords.strip(), autoescape=True),
            )
            if category:
                query = query.where(HardwarePart.category == category)
            if priced_only:
                query = query.where(
                    select(PartPrice.id)
                    .where(
                        PartPrice.part_id == HardwarePart.id,
                        PartPrice.is_quotable,
                    )
                    .exists()
                )
            parts = db.scalars(
                query.order_by(HardwarePart.normalized_name, HardwarePart.id).limit(limit)
            ).all()
            # Price amounts are supplied only by the explicit reference-price tool.
            return {
                "items": [
                    {key: part[key] for key in ("id", "name", "category", "is_quotable")}
                    for part in summaries(db, parts)
                ]
            }

    def part_detail(part_id):
        """Get a reviewed public catalog identity and bounded original specifications."""
        with Session(engine) as db, db.begin():
            readonly(db)
            part = get_part(db, part_id)
            return {
                "id": str(part.id),
                "name": part.display_name[:200],
                "category": part.category,
                "specifications": {
                    str(key)[:60]: value[:120] if isinstance(value, str) else value
                    for key, value in list(part.specifications.items())[:12]
                    if value is None or isinstance(value, (str, int, float, bool))
                },
                "specification_note": "原始规格，不等同于已审核兼容性证据",
            }

    def reference_price(part_id):
        """Read selected USD historical reference price with exact original-row provenance."""
        with Session(engine) as db, db.begin():
            readonly(db)
            part = get_part(db, part_id)
            summary = summaries(db, [part])[0]
            selected = summary["price_metadata"]
            if selected is None:
                return {
                    "part_id": str(part.id),
                    "name": part.display_name[:200],
                    "price_usd": None,
                    "quote_status": summary["quote_status"],
                    "message": "没有已审核选定的历史参考报价，不猜价格",
                }
            source = db.execute(
                select(RawImportRow, ImportBatch)
                .join(ImportBatch, ImportBatch.id == RawImportRow.batch_id)
                .join(PartSourceMap, PartSourceMap.raw_import_row_id == RawImportRow.id)
                .where(
                    RawImportRow.id == UUID(selected["source_row_id"]),
                    PartSourceMap.part_id == part.id,
                )
            ).first()
            if source is None:
                raise ValueError("Selected quote lacks mapped provenance")
            row, batch = source
            return {
                "part_id": str(part.id),
                "name": part.display_name[:200],
                **selected,
                "source_url": batch.source_url,
                "source_file": row.source_file,
                "line_number": row.line_number,
                "row_hash": row.row_hash,
            }

    def check_compatibility(build_id, expected_version):
        """Read three checked compatibility rules for an owned build at an exact version.

        PASS is limited to checked rules, never whole-system compatibility.
        """
        with Session(engine) as db, db.begin():
            db.execute(text("SET LOCAL statement_timeout = '1000'"))
            db.execute(text("SET LOCAL lock_timeout = '500'"))
            actor = authorize(db)
            build = owned(db, build_id, actor, lock=True)
            if build.version != expected_version:
                raise HTTPException(409, "方案版本变化，请刷新后重新检查")
            return report(db, build)

    definitions = [
        ("search_parts", search_parts, Search),
        ("get_part", part_detail, PartID),
        ("get_reference_price", reference_price, PartID),
    ]
    if authorize is not None:
        definitions.append(("check_compatibility", check_compatibility, BuildID))
    return {
        name: StructuredTool.from_function(func=function, name=name, args_schema=schema)
        for name, function, schema in definitions
    }


def readonly(db):
    db.execute(text("SET TRANSACTION READ ONLY"))
    db.execute(text("SET LOCAL statement_timeout = '1000'"))
    db.execute(text("SET LOCAL lock_timeout = '500'"))


def bounded_json(value, maximum=16000):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > maximum:
        raise ValueError("Tool result exceeds output limit")
    return encoded
