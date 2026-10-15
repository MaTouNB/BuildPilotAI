"""Local operator-only, append-only spec review import. No HTTP write endpoint."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.compatibility import FIELDS, normalized
from app.db import make_engine
from app.models import HardwarePart, PartSpecOverride


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    part_id: UUID
    field_name: str
    value: Annotated[str, Field(max_length=120)]
    unit: str | None = None
    source_url: Annotated[str, Field(max_length=2000)]
    source_locator: Annotated[str, Field(min_length=1, max_length=2000)]
    retrieved_at: AwareDatetime
    review_status: Literal["PENDING", "APPROVED", "REJECTED"]
    reviewer: Annotated[str, Field(min_length=1, max_length=200)]
    version: Annotated[int, Field(strict=True, ge=1)]
    supersedes_id: UUID | None = None

    @model_validator(mode="after")
    def validate_evidence(self):
        url = urlsplit(self.source_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("来源必须是无凭据 HTTPS URL")
        if not self.reviewer.strip() or not self.source_locator.strip():
            raise ValueError("审核者和定位不能为空")
        if self.retrieved_at > datetime.now(UTC):
            raise ValueError("查阅时间不能在未来")
        if self.field_name not in {field for fields in FIELDS.values() for field in fields}:
            raise ValueError("未支持的规格字段")
        self.value = normalized(self.field_name, self.value, self.unit)
        return self


def apply(db, reviews):
    """Call inside a transaction; caller has offline operator authority."""
    count = 0
    for review in sorted(reviews, key=lambda row: (str(row.part_id), row.field_name, row.version)):
        part = db.scalar(
            select(HardwarePart).where(HardwarePart.id == review.part_id).with_for_update()
        )
        if (
            part is None
            or not part.is_active
            or review.field_name not in FIELDS.get(part.category, {})
        ):
            raise ValueError("商品不存在、停用或规格字段与类别不匹配")
        existing = db.get(PartSpecOverride, review.id)
        values = review.model_dump()
        if existing:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise ValueError("记录 ID 已存在且内容不同；必须追加新版本")
            continue
        latest = (
            db.scalar(
                select(func.max(PartSpecOverride.version)).where(
                    PartSpecOverride.part_id == review.part_id,
                    PartSpecOverride.field_name == review.field_name,
                )
            )
            or 0
        )
        if review.version != latest + 1:
            raise ValueError("版本必须顺序递增")
        if review.supersedes_id:
            parent = db.get(PartSpecOverride, review.supersedes_id)
            if (
                not parent
                or parent.part_id != review.part_id
                or parent.field_name != review.field_name
                or parent.version >= review.version
            ):
                raise ValueError("替代记录必须指向同商品同字段的早期版本")
        db.add(PartSpecOverride(**values))
        db.flush()
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    reviews = [Review.model_validate(row) for row in json.loads(args.manifest.read_text())]
    engine = make_engine()
    try:
        with Session(engine) as db, db.begin():
            count = apply(db, reviews)
        print(json.dumps({"appended": count}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
