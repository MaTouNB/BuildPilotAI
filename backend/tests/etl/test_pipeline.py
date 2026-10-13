import csv
import hashlib
import io
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.etl.normalizers import parse_pair, parse_price
from app.etl.pipeline import run_pipeline, write_artifacts
from app.etl.source import read_source


def fixture_zip(tmp_path: Path, entries: dict[str, list[list[str]]]) -> tuple[Path, str]:
    path = tmp_path / "raw" / "fixture.zip"
    path.parent.mkdir(exist_ok=True)
    with ZipFile(path, "w") as archive:
        for name, values in entries.items():
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerows(values)
            archive.writestr(name, stream.getvalue())
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def memory_rows():
    return [["name", "price", "speed", "modules"], ["Sample RAM", "129.99", "5,6000", "2,16"]]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" 129.99 ", Decimal("129.99")),
        ("", None),
        ("1.005", Decimal("1.01")),
        ("9999999999.99", Decimal("9999999999.99")),
    ],
)
def test_prices(value, expected):
    assert parse_price(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "abc",
        "-10",
        "0",
        "NaN",
        "Infinity",
        "-Infinity",
        "0.001",
        "10000000000",
        "9999999999.995",
        "1e1000000",
    ],
)
def test_invalid_prices(value):
    with pytest.raises(ValueError):
        parse_price(value)


@pytest.mark.parametrize("value", ["5", "5,6000,1", "0,6000", "5,0", "a,6000", "2,16.5"])
def test_invalid_memory_pairs(value):
    with pytest.raises(ValueError):
        parse_pair(value)


def test_memory_and_price_metadata(tmp_path):
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": memory_rows()})
    _, report, _, rows = run_pipeline(path, digest)
    specs = rows[0]["specifications_json"]
    assert specs["ddr_generation"] == 5
    assert specs["data_rate_mt_s"] == 6000
    assert specs["module_count"] == 2
    assert specs["total_capacity_gb"] == 32
    assert rows[0]["price_quote"]["value"] == "129.99"
    assert rows[0]["price_quote"]["currency"] == "USD"
    assert rows[0]["price_quote"]["dataset_updated_at"] == "2025-07-23"
    assert rows[0]["is_quotable"] is False  # Not a catalog SKU yet.
    assert report["error_rows"] == 0


def test_duplicate_conflict_and_different_specs_are_not_merged(tmp_path):
    entries = memory_rows()
    entries.extend(
        [
            entries[1].copy(),
            ["Sample RAM", "140", "5,6000", "2,16"],
            ["Sample RAM", "150", "5,6000", "2,32"],
        ]
    )
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": entries})
    profile, report, raw, rows = run_pipeline(path, digest)
    assert len(raw) == len(rows) == 4
    assert len({row["source_record_key"] for row in raw}) == 4
    assert raw[0]["source_record_hash"] == raw[1]["source_record_hash"]
    assert profile["categories"][0]["duplicate_source_rows"] == 1
    assert rows[0]["candidate_key"] != rows[3]["candidate_key"]
    assert all(row["quote_status"] == "CONFLICT_REVIEW" for row in rows[:3])
    assert rows[3]["quote_status"] == "VALID_REFERENCE_PRICE"
    assert report["price_conflict_groups"] == 1
    assert report["same_name_variant_groups"] == 1


def test_quarantine_missing_price_and_extensions(tmp_path):
    entries = memory_rows()
    entries.extend(
        [
            ["No price", "", "5,6000", "2,16"],
            ["Bad pair", "10", "5", "2,16"],
            ["Bad price", "NaN", "5,6000", "2,16"],
            ["", "10", "5,6000", "2,16"],
            ["Wrong columns", "10", "5,6000", "2,16", "extra"],
        ]
    )
    path, digest = fixture_zip(
        tmp_path,
        {
            "csv/memory.csv": entries,
            "csv/widget.csv": [["name", "price"], ["Future category", "20"]],
        },
    )
    profile, report, raw, rows = run_pipeline(path, digest)
    assert report["error_rows"] == 4
    assert report["accepted_rows"] + report["error_rows"] == len(raw) == 7
    assert rows[1]["price_usd"] is None and rows[1]["quality_status"] == "REVIEW"
    output = tmp_path / "processed"
    write_artifacts(path, output, profile, report, raw, rows, dry_run=False)
    quarantine = [
        json.loads(line) for line in (output / "quarantine.jsonl").read_text().splitlines()
    ]
    assert len(quarantine) == 4
    assert all(
        row["source_line_number"] and row["raw_data"] and row["issues"] for row in quarantine
    )
    assert len((output / "extensions.jsonl").read_text().splitlines()) == 1
    assert len((output / "raw.jsonl").read_text().splitlines()) == 7
    assert len((output / "cleaned_memory.jsonl").read_text().splitlines()) == 2


def test_unknown_compatibility_fields_and_eight_categories(tmp_path):
    data = {
        "cpu": [
            ["name", "price", "core_count", "core_clock", "boost_clock", "tdp"],
            ["Example CPU", "10", "8", "4.2", "5.2", "120"],
        ],
        "motherboard": [
            ["name", "price", "socket", "max_memory", "memory_slots"],
            ["Example board", "20", "AM5", "128", "4"],
        ],
        "video-card": [["name", "price", "memory", "length"], ["Example GPU", "30", "16", "240"]],
        "memory": memory_rows(),
        "internal-hard-drive": [["name", "price", "capacity"], ["Example SSD", "40", "1000"]],
        "power-supply": [["name", "price", "wattage"], ["Example PSU", "50", "750"]],
        "case": [["name", "price", "internal_35_bays"], ["Example case", "60", "0"]],
        "cpu-cooler": [
            ["name", "price", "rpm", "size"],
            ["Example cooler", "70", "600,3000", "360"],
        ],
    }
    path, digest = fixture_zip(tmp_path, {f"csv/{key}.csv": value for key, value in data.items()})
    profile, report, raw, rows = run_pipeline(path, digest)
    by_category = {row["category"]: row for row in rows}
    assert by_category["CPU"]["specifications_json"]["socket"] is None
    assert by_category["MOTHERBOARD"]["specifications_json"]["memory_generation"] is None
    assert by_category["CASE"]["specifications_json"]["max_gpu_length_mm"] is None
    assert by_category["COOLER"]["specifications_json"]["rpm_source_value"] == "600,3000"
    assert by_category["CPU"]["specifications_json"]["clock_unit"] is None
    output = tmp_path / "processed"
    write_artifacts(path, output, profile, report, raw, rows, dry_run=False)
    assert len(list(output.glob("cleaned_*.jsonl"))) == 8
    assert report["error_rows"] == 0


def test_reproducible_exports_and_report_only_dry_run(tmp_path):
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": memory_rows()})
    result = run_pipeline(path, digest)
    output = tmp_path / "processed"
    write_artifacts(path, output, *result, dry_run=False)
    first = {file.name: file.read_bytes() for file in output.iterdir()}
    write_artifacts(path, output, *run_pipeline(path, digest), dry_run=False)
    assert first == {file.name: file.read_bytes() for file in output.iterdir()}
    with pytest.raises(ValueError, match="separate report directory"):
        write_artifacts(path, output, *result, dry_run=True)
    assert first == {file.name: file.read_bytes() for file in output.iterdir()}
    report_output = tmp_path / "dry-run"
    write_artifacts(path, report_output, *result, dry_run=True)
    assert not list(report_output.glob("*.jsonl"))
    assert (report_output / "quality_report.json").exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_input_hash_and_output_protection(tmp_path):
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": memory_rows()})
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        read_source(path, "0" * 64)
    result = run_pipeline(path, digest)
    for output in [path, path.parent, path.parent / "output"]:
        with pytest.raises(ValueError):
            write_artifacts(path, output, *result, dry_run=False)
    output = tmp_path / "foreign"
    output.mkdir()
    (output / "notes.txt").write_text("Keep this file")
    with pytest.raises(ValueError, match="owned ETL"):
        write_artifacts(path, output, *result, dry_run=False)
    assert (output / "notes.txt").read_text() == "Keep this file"


def test_multiline_physical_provenance(tmp_path):
    path, digest = fixture_zip(
        tmp_path,
        {
            "csv/memory.csv": [
                memory_rows()[0],
                ["Sample\nRAM", "10", "5,6000", "2,16"],
                memory_rows()[1],
            ]
        },
    )
    _, rows, _ = read_source(path, digest)
    assert rows[0]["source_line_number"] == 2
    assert rows[0]["source_end_line_number"] == 3
    assert rows[1]["source_line_number"] == 4
    assert rows[1]["source_row_number"] == 2


def test_cli_hash_failure_and_dry_run(tmp_path):
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": memory_rows()})
    output = tmp_path / "report"
    command = [sys.executable, "-m", "app.etl.clean", "--input", str(path), "--output", str(output)]
    failure = subprocess.run(command, capture_output=True, text=True)
    assert failure.returncode == 2
    assert "SHA-256 mismatch" in failure.stderr
    assert not output.exists()
    success = subprocess.run(
        command + ["--expected-sha256", digest, "--dry-run"], capture_output=True, text=True
    )
    assert success.returncode == 0, success.stderr
    assert json.loads(success.stdout)["dry_run"] is True
    assert not list(output.glob("*.jsonl"))


def test_unknown_columns_rounding_and_numeric_review(tmp_path):
    path, digest = fixture_zip(
        tmp_path,
        {
            "csv/cpu.csv": [
                ["name", "price", "core_count", "tdp", "new_field"],
                ["Example CPU", "1.005", "8", "-10", "retained"],
            ]
        },
    )
    profile, report, raw, rows = run_pipeline(path, digest)
    assert profile["categories"][0]["unknown_fields"] == ["new_field"]
    assert raw[0]["raw_data"]["price"] == "1.005"
    assert rows[0]["price_usd"] == "1.01"
    assert rows[0]["specifications_json"]["source_fields"]["new_field"] == "retained"
    assert rows[0]["specifications_json"]["tdp_w"] is None
    assert report["error_rows"] == 0
    assert {issue["rule_code"] for issue in rows[0]["issues"]} >= {
        "PRICE_ROUNDED",
        "SPEC_OUT_OF_RANGE",
    }


def test_owned_output_with_foreign_file_is_preserved(tmp_path):
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": memory_rows()})
    result = run_pipeline(path, digest)
    output = tmp_path / "processed"
    write_artifacts(path, output, *result, dry_run=False)
    (output / "personal.txt").write_text("Do not delete")
    with pytest.raises(ValueError, match="foreign files"):
        write_artifacts(path, output, *result, dry_run=False)
    assert (output / "personal.txt").read_text() == "Do not delete"


def test_staging_failure_preserves_previous_export(tmp_path, monkeypatch):
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": memory_rows()})
    result = run_pipeline(path, digest)
    output = tmp_path / "processed"
    write_artifacts(path, output, *result, dry_run=False)
    before = {file.name: file.read_bytes() for file in output.iterdir()}

    def fail_write(*args):
        raise OSError("Simulated disk failure")

    monkeypatch.setattr("app.etl.pipeline.write_json", fail_write)
    with pytest.raises(OSError):
        write_artifacts(path, output, *result, dry_run=False)
    assert before == {file.name: file.read_bytes() for file in output.iterdir()}
    assert not list(tmp_path.glob(".buildpilot-etl-*"))


def test_unsafe_zip_header_and_corrupt_archive_fail_clearly(tmp_path):
    path, digest = fixture_zip(tmp_path, {"../memory.csv": memory_rows()})
    with pytest.raises(ValueError, match="Unsafe ZIP path"):
        read_source(path, digest)
    path, digest = fixture_zip(tmp_path, {"csv/memory.csv": [["name", "price", "price"]]})
    with pytest.raises(ValueError, match="Invalid CSV header"):
        read_source(path, digest)
    path.write_bytes(b"not a zip")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    command = [
        sys.executable,
        "-m",
        "app.etl.clean",
        "--input",
        str(path),
        "--expected-sha256",
        digest,
        "--output",
        str(tmp_path / "output"),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert "ETL clean failed" in result.stderr
    assert "Traceback" not in result.stderr
