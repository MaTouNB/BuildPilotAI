from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = (
        UniqueConstraint("input_sha256", "etl_version"),
        CheckConstraint("status IN ('RUNNING','SUCCEEDED','FAILED')"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str] = mapped_column(Text)
    dataset_updated_at: Mapped[date] = mapped_column(Date)
    input_sha256: Mapped[str] = mapped_column(String(64))
    etl_version: Mapped[str] = mapped_column(String(100))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    review_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    row_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    warnings_count: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportAttempt(Base):
    __tablename__ = "import_attempt"
    __table_args__ = (CheckConstraint("status IN ('RUNNING','SUCCEEDED','FAILED')"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batch.id"))
    status: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(100))
    review_document: Mapped[dict | None] = mapped_column(JSONB)
    selection_changes: Mapped[list] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HardwarePart(Base):
    __tablename__ = "hardware_part"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    display_name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, index=True)
    specifications: Mapped[dict] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean)
    quality_status: Mapped[str] = mapped_column(String(20))
    reviewed_by: Mapped[str] = mapped_column(Text)
    review_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RawImportRow(Base):
    __tablename__ = "raw_import_row"
    __table_args__ = (
        UniqueConstraint("batch_id", "source_file", "row_number"),
        UniqueConstraint("id", "batch_id"),
        Index("ix_raw_record_key", "source_record_key"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batch.id"), index=True)
    source_file: Mapped[str] = mapped_column(Text)
    row_number: Mapped[int] = mapped_column(Integer)
    line_number: Mapped[int] = mapped_column(Integer)
    end_line_number: Mapped[int] = mapped_column(Integer)
    source_record_key: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))
    raw_data: Mapped[dict] = mapped_column(JSONB)
    extra_columns: Mapped[list | None] = mapped_column(JSONB)
    normalized_data: Mapped[dict] = mapped_column(JSONB)
    normalization_status: Mapped[str] = mapped_column(String(20))
    normalized_part_id: Mapped[UUID | None] = mapped_column(ForeignKey("hardware_part.id"))


class PartSourceMap(Base):
    __tablename__ = "part_source_map"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    part_id: Mapped[UUID] = mapped_column(ForeignKey("hardware_part.id"), index=True)
    raw_import_row_id: Mapped[UUID] = mapped_column(ForeignKey("raw_import_row.id"), unique=True)
    source_record_key: Mapped[str] = mapped_column(String(64), index=True)
    dedupe_rule: Mapped[str] = mapped_column(String(40))
    review_status: Mapped[str] = mapped_column(String(20))
    reviewed_by: Mapped[str] = mapped_column(Text)
    review_reason: Mapped[str] = mapped_column(Text)


class PartPrice(Base):
    __tablename__ = "part_price"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_row_id", "batch_id"], ["raw_import_row.id", "raw_import_row.batch_id"]
        ),
        UniqueConstraint("batch_id", "source_row_id"),
        CheckConstraint("currency = 'USD'"),
        CheckConstraint("price_kind = 'historical_reference'"),
        CheckConstraint("price_usd IS NULL OR price_usd > 0"),
        CheckConstraint("price_usd IS NULL OR price_usd <> 'NaN'::numeric", name="ck_price_finite"),
        CheckConstraint(
            "NOT is_quotable OR (part_id IS NOT NULL AND price_usd IS NOT NULL "
            "AND selected_by IS NOT NULL AND selection_reason IS NOT NULL)"
        ),
        Index("ix_price_part_batch", "part_id", "batch_id"),
        Index("ix_price_usd", "price_usd"),
        Index(
            "uq_selected_price_per_part",
            "part_id",
            unique=True,
            postgresql_where=text("is_quotable"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    part_id: Mapped[UUID | None] = mapped_column(ForeignKey("hardware_part.id"))
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batch.id"))
    source_row_id: Mapped[UUID] = mapped_column(Uuid)
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    price_kind: Mapped[str] = mapped_column(String(30))
    dataset_updated_at: Mapped[date] = mapped_column(Date)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_name: Mapped[str] = mapped_column(String(100))
    quote_status: Mapped[str] = mapped_column(String(30))
    is_quotable: Mapped[bool] = mapped_column(Boolean)
    selected_by: Mapped[str | None] = mapped_column(Text)
    selection_reason: Mapped[str | None] = mapped_column(Text)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issue"
    __table_args__ = (
        UniqueConstraint("raw_import_row_id", "field_name", "rule_code"),
        CheckConstraint("severity IN ('ERROR','WARN','INFO')"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batch.id"), index=True)
    raw_import_row_id: Mapped[UUID] = mapped_column(ForeignKey("raw_import_row.id"))
    severity: Mapped[str] = mapped_column(String(10))
    field_name: Mapped[str] = mapped_column(String(100))
    rule_code: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
