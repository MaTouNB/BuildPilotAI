"""Verify a local full import and its idempotence without deleting database data."""

import hashlib
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.db import make_engine
from app.etl import EXPECTED_SHA256
from app.etl.artifacts import load_artifacts
from app.etl.importer import apply_artifacts
from app.models import DataQualityIssue, PartPrice, RawImportRow

root = Path(__file__).resolve().parents[1]
source = root / "data/raw/csv.zip"
if not source.exists():
    raise SystemExit("Local raw ZIP required; it is intentionally excluded from Git")
assert hashlib.sha256(source.read_bytes()).hexdigest() == EXPECTED_SHA256
data = load_artifacts(root / "data/processed/m01", EXPECTED_SHA256)
engine = make_engine()
try:
    with engine.begin() as connection:
        config = Config(str(root / "backend/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    first = apply_artifacts(engine, data)
    second = apply_artifacts(engine, data)
    assert first["counts"] == second["counts"]
    assert second["status"] == "already_imported"
    batch_id = UUID(first["batch_id"])
    report = data["report"]
    with engine.connect() as connection:
        raw_count = connection.scalar(
            select(func.count()).select_from(RawImportRow).where(RawImportRow.batch_id == batch_id)
        )
        price_count = connection.scalar(
            select(func.count()).select_from(PartPrice).where(PartPrice.batch_id == batch_id)
        )
        issue_count = connection.scalar(
            select(func.count())
            .select_from(DataQualityIssue)
            .where(DataQualityIssue.batch_id == batch_id)
        )
        traced = connection.scalar(
            select(func.count())
            .select_from(PartPrice)
            .join(RawImportRow, PartPrice.source_row_id == RawImportRow.id)
            .where(PartPrice.batch_id == batch_id, RawImportRow.batch_id == batch_id)
        )
        assert raw_count == report["total_rows"] == 66778
        assert price_count == report["accepted_rows"] == traced
        assert issue_count == sum(
            len({(i["field_name"], i["rule_code"]) for i in row["issues"]})
            for row in data["normalized"].values()
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(PartPrice)
                .where(PartPrice.batch_id == batch_id, PartPrice.price_usd.is_(None))
            )
            > 0
        )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == EXPECTED_SHA256
    print(
        f"PASS: {raw_count} raw rows, {price_count} traced price rows, {issue_count} issues; "
        "rerun unchanged and original ZIP unchanged"
    )
finally:
    engine.dispose()
