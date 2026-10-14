import csv
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from app.etl.artifacts import load_artifacts
from app.etl.importer import ReviewManifest, apply_artifacts, database_counts
from app.etl.pipeline import run_pipeline, write_artifacts
from app.models import (
    HardwarePart,
    ImportAttempt,
    ImportBatch,
    PartPrice,
    PartSourceMap,
    RawImportRow,
)

pytestmark = pytest.mark.postgres
PART = UUID("8f945b54-e930-4afb-a05f-b55e401f4bcc")


def export_fixture(tmp_path, values=None):
    source = tmp_path / "raw" / "fixture.zip"
    source.parent.mkdir(exist_ok=True)
    values = values or [
        ["Demo RAM", "10", "5,6000", "2,16"],
        ["Demo RAM", "20", "5,6000", "2,16"],
        ["No-price RAM", "", "5,6000", "2,32"],
        ["Bad RAM", "NaN", "5,6000", "2,16"],
    ]
    buffer = io.StringIO()
    csv.writer(buffer).writerows([["name", "price", "speed", "modules"], *values])
    with ZipFile(source, "w") as archive:
        archive.writestr("csv/memory.csv", buffer.getvalue())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "processed"
    write_artifacts(source, output, *run_pipeline(source, digest), dry_run=False)
    return load_artifacts(output, digest)


def review(data, index=1, *, selected=True, part_id=PART):
    return ReviewManifest.model_validate(
        {
            "input_sha256": data["report"]["input_sha256"],
            "entries": [
                {
                    "source_record_key": data["raw"][index]["source_record_key"],
                    "part_id": str(part_id),
                    "reviewed_by": "synthetic-test-operator",
                    "reason": "Explicit review of synthetic fixture identity and source price",
                    "select_price": selected,
                }
            ],
        }
    )


def test_migrations_staging_nulls_and_idempotence(database, tmp_path):
    data = export_fixture(tmp_path)
    first = apply_artifacts(database, data)
    second = apply_artifacts(database, data)
    assert first["counts"] == second["counts"]
    assert second["status"] == "already_imported"
    assert first["counts"]["raw_import_row"] == 4
    assert first["counts"]["part_price"] == 3
    assert first["counts"]["hardware_part"] == 0
    with database.connect() as connection:
        prices = connection.execute(
            select(PartPrice.price_usd, PartPrice.part_id, PartPrice.is_quotable)
        ).all()
        assert any(value is None for value, _, _ in prices)
        assert all(part_id is None and quoted is False for _, part_id, quoted in prices)


def test_reviewed_part_price_and_source_trace(database, tmp_path):
    data = export_fixture(tmp_path)
    result = apply_artifacts(database, data, review(data))
    assert result["counts"]["hardware_part"] == 1
    with database.connect() as connection:
        trace = connection.execute(
            select(
                PartPrice.price_usd,
                RawImportRow.raw_data,
                RawImportRow.line_number,
                RawImportRow.source_file,
            )
            .join(RawImportRow, PartPrice.source_row_id == RawImportRow.id)
            .where(PartPrice.part_id == PART)
        ).one()
        assert str(trace[0]) == "20.00"
        assert trace[1]["price"] == "20"
        assert trace[2:] == (3, "csv/memory.csv")
        assert (
            connection.scalar(select(PartPrice.is_quotable).where(PartPrice.part_id == PART))
            is True
        )


def test_review_after_staging_preserves_raw_and_price_counts(database, tmp_path):
    data = export_fixture(tmp_path)
    before = apply_artifacts(database, data)["counts"]
    after = apply_artifacts(database, data, review(data))["counts"]
    for table in ["raw_import_row", "part_price", "data_quality_issue", "import_batch"]:
        assert before[table] == after[table]
    assert after["hardware_part"] == 1
    assert apply_artifacts(database, data, review(data))["status"] == "already_imported"


def test_failure_rolls_back_data_and_retry_succeeds(database, tmp_path, monkeypatch):
    from app.etl import importer

    data = export_fixture(tmp_path)
    real_write = importer.write_payload

    def fail(*args):
        real_write(*args)
        raise RuntimeError("Injected failure after writes")

    monkeypatch.setattr(importer, "write_payload", fail)
    with pytest.raises(RuntimeError):
        apply_artifacts(database, data, review(data))
    with database.connect() as connection:
        counts = database_counts(connection)
        assert counts["raw_import_row"] == counts["hardware_part"] == counts["part_price"] == 0
        assert connection.scalar(select(ImportAttempt.status)) == "FAILED"
        assert connection.scalar(select(ImportBatch.status)) == "FAILED"
    monkeypatch.setattr(importer, "write_payload", real_write)
    assert apply_artifacts(database, data)["counts"]["raw_import_row"] == 4


def test_concurrent_same_batch_imports_once(database, tmp_path):
    data = export_fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: apply_artifacts(database, data), range(2)))
    assert sorted(result["status"] for result in results) == ["already_imported", "imported"]
    assert results[0]["counts"] == results[1]["counts"]


def test_price_selection_is_audited_and_old_amount_is_immutable(database, tmp_path):
    data = export_fixture(tmp_path)
    apply_artifacts(database, data, review(data, 0))
    apply_artifacts(database, data, review(data, 1))
    with database.connect() as connection:
        assert (
            connection.scalar(select(PartPrice.price_usd).where(PartPrice.is_quotable.is_(True)))
            == 20
        )
        prices = connection.scalars(
            select(PartPrice.price_usd).where(PartPrice.part_id == PART)
        ).all()
        assert sorted(prices) == [10, 20]
        events = connection.scalars(
            select(ImportAttempt.selection_changes).order_by(ImportAttempt.started_at)
        ).all()
        assert events[-1][0]["previous_price_id"] == events[0][0]["selected_price_id"]


def test_same_name_different_specs_cannot_merge(database, tmp_path):
    data = export_fixture(
        tmp_path, [["Demo RAM", "10", "5,6000", "2,16"], ["Demo RAM", "20", "5,6000", "2,32"]]
    )
    entry = review(data, 0).model_dump(mode="json")
    other = review(data, 1, selected=False).model_dump(mode="json")["entries"][0]
    entry["entries"].append(other)
    with pytest.raises(ValueError, match="cannot be collapsed"):
        apply_artifacts(database, data, ReviewManifest.model_validate(entry))
    with database.connect() as connection:
        assert database_counts(connection)["hardware_part"] == 0


def test_reviewed_missing_price_part_exists_but_cannot_quote(database, tmp_path):
    data = export_fixture(tmp_path)
    apply_artifacts(database, data, review(data, 2, selected=False))
    with database.connect() as connection:
        assert connection.scalar(select(HardwarePart.id)) == PART
        price, quotable = connection.execute(
            select(PartPrice.price_usd, PartPrice.is_quotable).where(PartPrice.part_id == PART)
        ).one()
        assert price is None
        assert quotable is False


def test_sql_constraints_reject_zero_and_unreviewed_quote(database, tmp_path):
    data = export_fixture(tmp_path)
    apply_artifacts(database, data)
    with database.connect() as connection:
        price_id = connection.scalar(select(PartPrice.id).where(PartPrice.price_usd.is_not(None)))
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(update(PartPrice).where(PartPrice.id == price_id).values(price_usd=0))
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(
            update(PartPrice).where(PartPrice.id == price_id).values(is_quotable=True)
        )
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(
            update(PartPrice).where(PartPrice.id == price_id).values(price_usd=Decimal("NaN"))
        )


def test_pipeline_upgrade_reuses_reviewed_identity(database, tmp_path):
    from copy import deepcopy

    data = export_fixture(tmp_path)
    apply_artifacts(database, data, review(data))
    upgraded = deepcopy(data)
    upgraded["report"]["pipeline_version"] = "compatible-fixture-v2"
    upgraded["artifact_sha256"] = "a" * 64
    result = apply_artifacts(database, upgraded)
    assert result["counts"]["hardware_part"] == 1
    assert result["counts"]["import_batch"] == 2
    with database.connect() as connection:
        assert set(connection.scalars(select(PartSourceMap.part_id)).all()) == {PART}


def test_new_source_version_does_not_guess_identity(database, tmp_path):
    data = export_fixture(tmp_path)
    apply_artifacts(database, data, review(data))
    next_path = tmp_path / "next"
    next_path.mkdir()
    new_data = export_fixture(
        next_path, [["Demo RAM", "20", "5,6000", "2,16"], ["Demo RAM", "10", "5,6000", "2,16"]]
    )
    apply_artifacts(database, new_data)
    with database.connect() as connection:
        assert connection.scalar(select(HardwarePart.id)) == PART
        new_rows = connection.scalars(
            select(RawImportRow.normalized_part_id).where(
                RawImportRow.source_record_key.in_(
                    [row["source_record_key"] for row in new_data["raw"]]
                )
            )
        ).all()
        assert new_rows == [None, None]
    assert apply_artifacts(database, new_data, review(new_data, 0))["counts"]["hardware_part"] == 1


def test_price_source_cannot_reference_another_batch(database, tmp_path):
    from copy import deepcopy

    data = export_fixture(tmp_path)
    first = apply_artifacts(database, data)
    another = deepcopy(data)
    another["report"]["pipeline_version"] = "compatible-fixture-v2"
    another["artifact_sha256"] = "b" * 64
    result = apply_artifacts(database, another)
    with database.connect() as connection:
        old_price = dict(
            connection.execute(
                select(PartPrice.__table__).where(PartPrice.batch_id == UUID(first["batch_id"]))
            )
            .mappings()
            .first()
        )
    old_price["id"] = uuid4()
    old_price["batch_id"] = UUID(result["batch_id"])
    with pytest.raises(IntegrityError) as failure, database.begin() as connection:
        connection.execute(insert(PartPrice).values(**old_price))
    assert failure.value.orig.sqlstate == "23503"  # Foreign key violation, not duplicate insertion.
