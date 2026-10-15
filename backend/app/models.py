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
        UniqueConstraint("id", "part_id", name="uq_price_id_part"),
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


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))


class UserAccount(Base):
    __tablename__ = "user_account"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))


class Build(Base):
    __tablename__ = "build"
    __table_args__ = (
        CheckConstraint("version >= 1"),
        CheckConstraint("status IN ('draft','archived')"),
        CheckConstraint("budget_currency = 'USD'"),
        CheckConstraint(
            "budget_amount IS NULL OR (budget_amount > 0 AND budget_amount <> 'NaN'::numeric)"
        ),
        Index("ix_build_owner", "tenant_id", "owner_user_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"))
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"))
    name: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(Text)
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_currency: Mapped[str] = mapped_column(String(3))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BuildItem(Base):
    __tablename__ = "build_item"
    __table_args__ = (
        UniqueConstraint("build_id", "slot"),
        ForeignKeyConstraint(["part_price_id", "part_id"], ["part_price.id", "part_price.part_id"]),
        CheckConstraint(
            "slot IN ('CPU','GPU','MOTHERBOARD','MEMORY','STORAGE','PSU','CASE','COOLER')"
        ),
        CheckConstraint("quantity BETWEEN 1 AND 99"),
        CheckConstraint("snapshot_currency IS NULL OR snapshot_currency = 'USD'"),
        CheckConstraint(
            "price_snapshot_usd IS NULL OR (price_snapshot_usd > 0 AND "
            "price_snapshot_usd <> 'NaN'::numeric)"
        ),
        CheckConstraint(
            "(price_snapshot_usd IS NULL AND part_price_id IS NULL AND "
            "snapshot_currency IS NULL AND price_source IS NULL AND "
            "price_dataset_updated_at IS NULL) OR (price_snapshot_usd IS NOT NULL AND "
            "part_price_id IS NOT NULL AND snapshot_currency IS NOT NULL AND "
            "price_source IS NOT NULL AND price_dataset_updated_at IS NOT NULL)"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    build_id: Mapped[UUID] = mapped_column(ForeignKey("build.id"), index=True)
    slot: Mapped[str] = mapped_column(String(30))
    part_id: Mapped[UUID] = mapped_column(ForeignKey("hardware_part.id"))
    part_name: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    part_price_id: Mapped[UUID | None] = mapped_column(Uuid)
    price_snapshot_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    snapshot_currency: Mapped[str | None] = mapped_column(String(3))
    price_source: Mapped[str | None] = mapped_column(Text)
    price_dataset_updated_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BuildRequest(Base):
    __tablename__ = "build_request"
    __table_args__ = (UniqueConstraint("tenant_id", "owner_user_id", "request_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"))
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"))
    request_key: Mapped[UUID] = mapped_column(Uuid)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantMember(Base):
    __tablename__ = "tenant_member"
    __table_args__ = (CheckConstraint("role IN ('member','admin')"),)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))


class AuthSession(Base):
    __tablename__ = "auth_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginThrottle(Base):
    __tablename__ = "login_throttle"
    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PartSpecOverride(Base):
    __tablename__ = "part_spec_override"
    __table_args__ = (
        UniqueConstraint("part_id", "field_name", "version"),
        UniqueConstraint("id", "part_id", "field_name"),
        ForeignKeyConstraint(
            ["supersedes_id", "part_id", "field_name"],
            [
                "part_spec_override.id",
                "part_spec_override.part_id",
                "part_spec_override.field_name",
            ],
        ),
        CheckConstraint("version > 0"),
        CheckConstraint("review_status IN ('PENDING','APPROVED','REJECTED')"),
        CheckConstraint("length(trim(reviewer)) > 0"),
        CheckConstraint("length(trim(source_locator)) > 0"),
        CheckConstraint("source_url LIKE 'https://%'"),
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    part_id: Mapped[UUID] = mapped_column(ForeignKey("hardware_part.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(50))
    value: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(20))
    source_url: Mapped[str] = mapped_column(Text)
    source_locator: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_status: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "chat_session"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", "create_request_id"),
        CheckConstraint("version > 0"),
        CheckConstraint("status IN ('active','deleted')"),
        CheckConstraint("cleanup_status IN ('NONE','PENDING','DONE')"),
        Index("ix_chat_session_owner", "tenant_id", "owner_user_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"))
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"))
    create_request_id: Mapped[UUID] = mapped_column(Uuid)
    thread_id: Mapped[str] = mapped_column(String(36), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="active")
    cleanup_status: Mapped[str] = mapped_column(String(20), default="NONE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatRun(Base):
    __tablename__ = "chat_run"
    __table_args__ = (
        UniqueConstraint("session_id", "client_request_id"),
        UniqueConstraint("id", "session_id"),
        CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED','CANCELLED')"),
        CheckConstraint("last_event_id >= 0"),
        Index(
            "uq_chat_run_active",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("chat_session.id"), index=True)
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(40))
    last_event_id: Mapped[int] = mapped_column(Integer, default=0)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        ForeignKeyConstraint(["run_id", "session_id"], ["chat_run.id", "chat_run.session_id"]),
        CheckConstraint("role IN ('user','assistant')"),
        CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED','CANCELLED')"),
        CheckConstraint("sequence IN (1,2)"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("chat_session.id"), index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatEvent(Base):
    __tablename__ = "chat_event"
    __table_args__ = (
        CheckConstraint("event_id > 0"),
        CheckConstraint(
            "kind IN ('run.started','tool.started','tool.completed',"
            "'message.delta','run.completed','run.failed')"
        ),
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("chat_run.id"), primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
