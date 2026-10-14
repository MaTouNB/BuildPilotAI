import csv
import hashlib
import io
import json
import sys
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.etl.artifacts import load_artifacts
from app.etl.import_db import main
from app.etl.importer import ReviewManifest, validate_reviews
from app.etl.pipeline import run_pipeline, write_artifacts


@pytest.fixture
def exported(tmp_path):
    source = tmp_path / "raw" / "fixture.zip"
    source.parent.mkdir()
    stream = io.StringIO()
    csv.writer(stream).writerows(
        [
            ["name", "price", "speed", "modules"],
            ["Synthetic RAM", "10.05", "5,6000", "2,16"],
        ]
    )
    with ZipFile(source, "w") as archive:
        archive.writestr("csv/memory.csv", stream.getvalue())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "processed"
    write_artifacts(source, output, *run_pipeline(source, digest), dry_run=False)
    return output, digest


def test_tampered_money_is_rejected(exported):
    output, digest = exported
    path = output / "cleaned_memory.jsonl"
    row = json.loads(path.read_text())
    row["price_usd"] = "999.99"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="price_usd"):
        load_artifacts(output, digest)


def test_missing_partition_is_rejected(exported):
    output, digest = exported
    (output / "cleaned_memory.jsonl").write_text("")
    with pytest.raises(ValueError, match="incomplete"):
        load_artifacts(output, digest)


def test_corrupted_source_hash_is_rejected(exported):
    output, digest = exported
    path = output / "raw.jsonl"
    row = json.loads(path.read_text())
    row["raw_data"]["price"] = "99"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="record hash"):
        load_artifacts(output, digest)


def test_import_dry_run_never_connects_to_database(exported, monkeypatch, capsys):
    output, digest = exported
    monkeypatch.setattr(
        sys, "argv", ["import_db", "--input", str(output), "--expected-sha256", digest, "--dry-run"]
    )

    def forbidden():
        raise AssertionError("Dry-run must not connect")

    monkeypatch.setattr("app.etl.import_db.make_engine", forbidden)
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "validated"


def test_review_scope_and_duplicate_selections_are_rejected(exported):
    output, digest = exported
    data = load_artifacts(output, digest)
    item = {
        "source_record_key": data["raw"][0]["source_record_key"],
        "part_id": str(uuid4()),
        "reviewed_by": "fixture-operator",
        "reason": "fixture review",
    }
    wrong = ReviewManifest.model_validate({"input_sha256": "0" * 64, "entries": [item]})
    with pytest.raises(ValueError, match="Review input"):
        validate_reviews(data, wrong)
    duplicates = ReviewManifest.model_validate({"input_sha256": digest, "entries": [item, item]})
    with pytest.raises(ValueError, match="duplicate source"):
        validate_reviews(data, duplicates)
