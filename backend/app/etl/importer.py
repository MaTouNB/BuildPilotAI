from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.etl import DATASET_UPDATED_AT, SOURCE
from app.etl.source import fingerprint
from app.models import (
    DataQualityIssue,
    HardwarePart,
    ImportAttempt,
    ImportBatch,
    PartPrice,
    PartSourceMap,
    RawImportRow,
)


def identifier(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, "buildpilot:" + value)


class IdentityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_record_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    part_id: UUID
    reviewed_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    select_price: StrictBool = False


class ReviewManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: list[IdentityReview]


def validate_reviews(data: dict, reviews: ReviewManifest | None) -> None:
    if reviews is None:
        return
    if reviews.input_sha256 != data["report"]["input_sha256"]:
        raise ValueError("Review input SHA-256 mismatch")
    seen, selected_parts = set(), set()
    for entry in reviews.entries:
        if entry.source_record_key in seen:
            raise ValueError("Review contains duplicate source keys")
        seen.add(entry.source_record_key)
        row = data["normalized"].get(entry.source_record_key)
        if row is None or row["quality_status"] == "ERROR" or row["category"] == "EXTENSION":
            raise ValueError("Only valid main-category source records can be identity-reviewed")
        if not entry.reviewed_by.strip() or not entry.reason.strip():
            raise ValueError("Review requires an actor and reason")
        if entry.select_price:
            if row["price_usd"] is None or entry.part_id in selected_parts:
                raise ValueError("Select exactly one non-null source price per part")
            selected_parts.add(entry.part_id)


def insert_rows(connection, model, records: list[dict]) -> None:
    for offset in range(0, len(records), 500):
        connection.execute(
            insert(model).on_conflict_do_nothing(index_elements=["id"]),
            records[offset : offset + 500],
        )


def database_counts(connection) -> dict:
    models = [ImportBatch, RawImportRow, HardwarePart, PartSourceMap, PartPrice, DataQualityIssue]
    return {
        model.__tablename__: connection.scalar(select(func.count()).select_from(model))
        for model in models
    }


def write_payload(connection, data: dict, batch_id: UUID, reviews: ReviewManifest | None) -> list:
    normalized = data["normalized"]
    approved = {}
    inherited = connection.execute(
        select(
            PartSourceMap.source_record_key,
            PartSourceMap.part_id,
            PartSourceMap.reviewed_by,
            PartSourceMap.review_reason,
        )
    ).all()
    for key, part_id, actor, reason in inherited:
        if key in approved and approved[key]["part_id"] != part_id:
            raise ValueError("Previously reviewed identity mappings disagree")
        approved[key] = {
            "part_id": part_id,
            "actor": actor,
            "reason": reason,
            "select_price": False,
        }
    if reviews is not None:
        seen = set()
        selected_parts = set()
        for entry in reviews.entries:
            key = entry.source_record_key
            if key in seen:
                raise ValueError("Review contains duplicate source keys")
            seen.add(key)
            row = normalized.get(key)
            if row is None or row["quality_status"] == "ERROR" or row["category"] == "EXTENSION":
                raise ValueError("Only valid main-category source records can be identity-reviewed")
            if not entry.reviewed_by.strip() or not entry.reason.strip():
                raise ValueError("Review requires an actor and reason")
            if key in approved and approved[key]["part_id"] != entry.part_id:
                raise ValueError("A reviewed source identity cannot be silently reassigned")
            if entry.select_price:
                if row["price_usd"] is None or entry.part_id in selected_parts:
                    raise ValueError("Select exactly one non-null source price per part")
                selected_parts.add(entry.part_id)
            approved[key] = {
                "part_id": entry.part_id,
                "actor": entry.reviewed_by,
                "reason": entry.reason,
                "select_price": entry.select_price,
            }

    part_specs = {}
    for key, review in approved.items():
        if key not in normalized:
            continue
        row = normalized[key]
        part_id = review["part_id"]
        identity = (row["category"], row["name_normalized"], row["specifications_json"])
        if part_id in part_specs and part_specs[part_id] != identity:
            raise ValueError("Different names/specifications cannot be collapsed into one part")
        part_specs[part_id] = identity
        current = (
            connection.execute(select(HardwarePart.__table__).where(HardwarePart.id == part_id))
            .mappings()
            .first()
        )
        if current is not None:
            if (
                current["category"],
                current["normalized_name"],
                current["specifications"],
            ) != identity:
                raise ValueError("Reviewed mapping disagrees with the existing canonical part")
        else:
            connection.execute(
                insert(HardwarePart).values(
                    id=part_id,
                    category=row["category"],
                    display_name=row["name_normalized"],
                    normalized_name=row["name_normalized"],
                    specifications=row["specifications_json"],
                    is_active=True,
                    quality_status=row["quality_status"],
                    reviewed_by=review["actor"],
                    review_reason=review["reason"],
                )
            )

    raw_values, prices, maps, issues = [], [], [], []
    changes = []
    for source in data["raw"]:
        key = source["source_record_key"]
        row = normalized[key]
        raw_id = identifier(f"raw:{batch_id}:{key}")
        review = approved.get(key)
        part_id = review["part_id"] if review else None
        raw_values.append(
            {
                "id": raw_id,
                "batch_id": batch_id,
                "source_file": source["source_file"],
                "row_number": source["source_row_number"],
                "line_number": source["source_line_number"],
                "end_line_number": source["source_end_line_number"],
                "source_record_key": key,
                "row_hash": source["source_record_hash"],
                "raw_data": source["raw_data"],
                "extra_columns": source["extra_columns"],
                "normalized_data": row,
                "normalization_status": row["quality_status"],
                "normalized_part_id": part_id,
            }
        )
        if review:
            maps.append(
                {
                    "id": identifier(f"map:{raw_id}"),
                    "part_id": part_id,
                    "raw_import_row_id": raw_id,
                    "source_record_key": key,
                    "dedupe_rule": "EXPLICIT_IDENTITY_REVIEW",
                    "review_status": "APPROVED",
                    "reviewed_by": review["actor"],
                    "review_reason": review["reason"],
                }
            )
        if row["quality_status"] != "ERROR":
            prices.append(
                {
                    "id": identifier(f"price:{raw_id}"),
                    "batch_id": batch_id,
                    "source_row_id": raw_id,
                    "part_id": part_id,
                    "price_usd": Decimal(row["price_usd"])
                    if row["price_usd"] is not None
                    else None,
                    "currency": "USD",
                    "price_kind": "historical_reference",
                    "dataset_updated_at": date.fromisoformat(DATASET_UPDATED_AT),
                    "observed_at": None,
                    "source_name": SOURCE,
                    "quote_status": row["quote_status"],
                    "is_quotable": False,
                    "selected_by": None,
                    "selection_reason": None,
                }
            )
        for issue in row["issues"]:
            issues.append(
                {
                    "id": identifier(f"issue:{raw_id}:{issue['field_name']}:{issue['rule_code']}"),
                    "batch_id": batch_id,
                    "raw_import_row_id": raw_id,
                    **issue,
                    "details": {"source_record_key": key},
                    "resolved_at": None,
                }
            )
    insert_rows(connection, RawImportRow, raw_values)
    insert_rows(connection, PartSourceMap, maps)
    insert_rows(connection, PartPrice, prices)
    insert_rows(connection, DataQualityIssue, issues)
    # Review-only retries link existing staging rows; monetary values remain immutable.
    for key, review in approved.items():
        if key not in normalized:
            continue
        raw_id = identifier(f"raw:{batch_id}:{key}")
        part_id = review["part_id"]
        connection.execute(
            update(RawImportRow).where(RawImportRow.id == raw_id).values(normalized_part_id=part_id)
        )
        connection.execute(
            update(PartPrice).where(PartPrice.source_row_id == raw_id).values(part_id=part_id)
        )
    selected = sorted(
        (key, value)
        for key, value in approved.items()
        if key in normalized and value["select_price"]
    )
    # Take part locks in a stable order for selections in different import batches.
    for part_id in sorted({str(review["part_id"]) for _, review in selected}):
        lock_key = int(fingerprint(["price-selection", part_id])[:16], 16) - 2**63
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    for key, review in selected:
        part_id = review["part_id"]
        price_id = identifier(f"price:{identifier(f'raw:{batch_id}:{key}')}")
        previous = connection.scalar(
            select(PartPrice.id).where(
                PartPrice.part_id == part_id, PartPrice.is_quotable.is_(True)
            )
        )
        connection.execute(
            update(PartPrice).where(PartPrice.part_id == part_id).values(is_quotable=False)
        )
        connection.execute(
            update(PartPrice)
            .where(PartPrice.id == price_id)
            .values(
                is_quotable=True,
                quote_status="REVIEWED_SELECTION",
                selected_by=review["actor"],
                selection_reason=review["reason"],
            )
        )
        changes.append(
            {
                "part_id": str(part_id),
                "previous_price_id": str(previous) if previous else None,
                "selected_price_id": str(price_id),
                "policy": "EXPLICIT_SOURCE_ROW",
            }
        )
    return changes


def apply_artifacts(engine: Engine, data: dict, reviews: ReviewManifest | None = None) -> dict:
    report = data["report"]
    validate_reviews(data, reviews)
    review_document = reviews.model_dump(mode="json") if reviews else None
    review_hash = fingerprint(review_document)
    batch_id = identifier(f"batch:{report['input_sha256']}:{report['pipeline_version']}")
    lock_key = int(fingerprint([str(batch_id)])[:16], 16) - 2**63
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
        connection.commit()
        attempt_id = uuid4()
        previous_status = "FAILED"
        try:
            with connection.begin():
                existing = (
                    connection.execute(
                        select(ImportBatch.__table__).where(ImportBatch.id == batch_id)
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    previous_status = existing["status"]
                    if existing["artifact_sha256"] != data["artifact_sha256"]:
                        raise ValueError(
                            "Same input/pipeline has different artifacts; bump pipeline version"
                        )
                    if (
                        existing["status"] == "SUCCEEDED"
                        and existing["review_sha256"] == review_hash
                    ):
                        return {
                            "batch_id": str(batch_id),
                            "status": "already_imported",
                            "counts": database_counts(connection),
                        }
                    connection.execute(
                        update(ImportBatch)
                        .where(ImportBatch.id == batch_id)
                        .values(status="RUNNING")
                    )
                else:
                    connection.execute(
                        insert(ImportBatch).values(
                            id=batch_id,
                            source_name=SOURCE,
                            source_url="https://github.com/docyx/pc-part-dataset",
                            dataset_updated_at=date.fromisoformat(DATASET_UPDATED_AT),
                            input_sha256=report["input_sha256"],
                            etl_version=report["pipeline_version"],
                            artifact_sha256=data["artifact_sha256"],
                            review_sha256=None,
                            status="RUNNING",
                            row_count=report["total_rows"],
                            error_count=report["error_rows"],
                            warnings_count=report["warning_rows"],
                            finished_at=None,
                        )
                    )
                connection.execute(
                    insert(ImportAttempt).values(
                        id=attempt_id,
                        batch_id=batch_id,
                        status="RUNNING",
                        error_code=None,
                        review_document=review_document,
                        selection_changes=[],
                        finished_at=None,
                    )
                )
            try:
                with connection.begin():
                    changes = write_payload(connection, data, batch_id, reviews)
                    connection.execute(
                        update(ImportBatch)
                        .where(ImportBatch.id == batch_id)
                        .values(
                            status="SUCCEEDED",
                            review_sha256=review_hash,
                            finished_at=datetime.now(UTC),
                        )
                    )
                    connection.execute(
                        update(ImportAttempt)
                        .where(ImportAttempt.id == attempt_id)
                        .values(
                            status="SUCCEEDED",
                            selection_changes=changes,
                            finished_at=datetime.now(UTC),
                        )
                    )
                    counts = database_counts(connection)
            except Exception as exc:
                # Re-raise after recording a redacted failure in a separate transaction.
                with connection.begin():
                    connection.execute(
                        update(ImportBatch)
                        .where(ImportBatch.id == batch_id)
                        .values(
                            status="SUCCEEDED" if previous_status == "SUCCEEDED" else "FAILED",
                        )
                    )
                    connection.execute(
                        update(ImportAttempt)
                        .where(ImportAttempt.id == attempt_id)
                        .values(
                            status="FAILED",
                            error_code=type(exc).__name__,
                            finished_at=datetime.now(UTC),
                        )
                    )
                raise
            return {"batch_id": str(batch_id), "status": "imported", "counts": counts}
        finally:
            if connection.in_transaction():
                connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            connection.commit()
