import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from app.db import make_engine
from app.main import create_app
from app.models import HardwarePart, ImportBatch, PartPrice, PartSourceMap, RawImportRow

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def database():
    if os.environ.get("BUILDPILOT_TEST_POSTGRES") != "1":
        pytest.skip("Real PostgreSQL tests require BUILDPILOT_TEST_POSTGRES=1")
    schema = "bptest_" + uuid4().hex
    admin = make_engine()
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = make_engine(schema=schema)
    try:
        with engine.begin() as connection:
            config = Config(str(BACKEND / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def catalog(database):
    # Independent synthetic source; never inserted into the real public catalog schema.
    batch = uuid4()
    ids = [UUID(int=index + 1) for index in range(5)]
    with database.begin() as db:
        db.execute(
            insert(ImportBatch),
            dict(
                id=batch,
                source_name="synthetic",
                source_url="https://example.test/fixture",
                dataset_updated_at=date(2025, 7, 23),
                input_sha256="a" * 64,
                etl_version="fixture",
                artifact_sha256="b" * 64,
                status="SUCCEEDED",
                row_count=5,
                error_count=0,
                warnings_count=0,
            ),
        )
        for index, part_id in enumerate(ids):
            db.execute(
                insert(HardwarePart),
                dict(
                    id=part_id,
                    category="GPU" if index == 1 else "CPU",
                    display_name="Same CPU"
                    if index in (0, 2)
                    else "GPU 100%_literal"
                    if index == 1
                    else "Missing CPU"
                    if index == 3
                    else "Inactive",
                    normalized_name="Same CPU"
                    if index in (0, 2)
                    else "GPU 100%_literal"
                    if index == 1
                    else "Missing CPU"
                    if index == 3
                    else "Inactive",
                    specifications={"socket": None, "source_fields": {"name": "original"}},
                    is_active=index != 4,
                    quality_status="REVIEW",
                    reviewed_by="private-operator",
                    review_reason="private-reason",
                ),
            )
            row = uuid4()
            db.execute(
                insert(RawImportRow),
                dict(
                    id=row,
                    batch_id=batch,
                    source_file="fixture/cpu.csv",
                    row_number=index + 1,
                    line_number=index + 2,
                    end_line_number=index + 2,
                    source_record_key=str(index) * 64,
                    row_hash="c" * 64,
                    raw_data={"name": "original", "price": "" if index == 3 else "10"},
                    normalized_data={},
                    normalization_status="VALID",
                    normalized_part_id=part_id,
                ),
            )
            db.execute(
                insert(PartSourceMap),
                dict(
                    id=uuid4(),
                    part_id=part_id,
                    raw_import_row_id=row,
                    source_record_key=str(index) * 64,
                    dedupe_rule="EXPLICIT",
                    review_status="REVIEWED",
                    reviewed_by="private-operator",
                    review_reason="private-reason",
                ),
            )
            db.execute(
                insert(PartPrice),
                dict(
                    id=uuid4(),
                    part_id=part_id,
                    batch_id=batch,
                    source_row_id=row,
                    price_usd=None
                    if index == 3
                    else Decimal("10.25")
                    if index == 0
                    else Decimal("20.50"),
                    currency="USD",
                    price_kind="historical_reference",
                    dataset_updated_at=date(2025, 7, 23),
                    source_name="synthetic",
                    quote_status="REVIEWED_SELECTION" if index < 2 else "VALID_REFERENCE_PRICE",
                    is_quotable=index < 2,
                    selected_by="private-operator" if index < 2 else None,
                    selection_reason="private-reason" if index < 2 else None,
                ),
            )
    with TestClient(create_app(engine=database)) as client:
        yield client, ids
