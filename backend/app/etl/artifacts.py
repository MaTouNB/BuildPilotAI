import hashlib
import json
from pathlib import Path

from app.etl import CATEGORIES, DATASET_UPDATED_AT, SOURCE
from app.etl.normalizers import normalize_row
from app.etl.source import fingerprint


def load_artifacts(directory: Path, expected_sha256: str) -> dict:
    names = [
        ".etl-manifest.json",
        "profile.json",
        "quality_report.json",
        "raw.jsonl",
        "quarantine.jsonl",
        "extensions.jsonl",
    ]
    names += [f"cleaned_{category.lower()}.jsonl" for category in CATEGORIES.values()]
    blobs = {}
    for name in names:
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe artifact: {name}")
        blobs[name] = path.read_bytes()
    manifest = json.loads(blobs[".etl-manifest.json"])
    profile = json.loads(blobs["profile.json"])
    report = json.loads(blobs["quality_report.json"])
    if manifest.get("owner") != "buildpilot-etl" or manifest.get("dry_run") is not False:
        raise ValueError("Database import requires a complete owned M01 export")
    if any(
        document.get("input_sha256") != expected_sha256 for document in [manifest, profile, report]
    ):
        raise ValueError("Artifact input SHA-256 mismatch")
    version = manifest["pipeline_version"]
    if not version or any(d["pipeline_version"] != version for d in [profile, report]):
        raise ValueError("Artifact pipeline versions differ")
    if report["source_dataset"] != SOURCE or report["dataset_updated_at"] != DATASET_UPDATED_AT:
        raise ValueError("Unsupported source/date metadata")
    raw = [json.loads(line) for line in blobs["raw.jsonl"].splitlines()]
    normalized = {}
    for name in names:
        if name.endswith(".jsonl") and name != "raw.jsonl":
            for line in blobs[name].splitlines():
                row = json.loads(line)
                key = row["source_record_key"]
                if key in normalized:
                    raise ValueError("A source record appears in multiple partitions")
                normalized[key] = row
    keys = {row["source_record_key"] for row in raw}
    if len(keys) != len(raw) or keys != set(normalized) or len(raw) != report["total_rows"]:
        raise ValueError("Artifact source partition is incomplete or duplicated")
    errors = 0
    for row in raw:
        key = row["source_record_key"]
        if not isinstance(row["raw_data"], dict) or any(
            not isinstance(key, str) or (value is not None and not isinstance(value, str))
            for key, value in row["raw_data"].items()
        ):
            raise ValueError("Raw CSV fields must remain strings or missing values")
        structural_error = row["extra_columns"] is not None or any(
            value is None for value in row["raw_data"].values()
        )
        if row["structural_error"] is not structural_error:
            raise ValueError("Raw CSV structural error marker disagrees with columns")
        if row["source_dataset_version"] != expected_sha256 or row["pipeline_version"] != version:
            raise ValueError("Source provenance version mismatch")
        if key != fingerprint([expected_sha256, row["source_file"], row["source_row_number"]]):
            raise ValueError("Source key mismatch")
        if row["source_record_hash"] != fingerprint([row["raw_data"], row["extra_columns"]]):
            raise ValueError("Source record hash mismatch")
        expected = normalize_row(row)
        actual = normalized[key]
        for field in [
            "category",
            "name_normalized",
            "specifications_json",
            "candidate_key",
            "price_usd",
            "currency",
            "source_record_hash",
            "source_file",
            "source_line_number",
            "source_end_line_number",
            "source_row_number",
            "source_dataset_version",
            "pipeline_version",
            "dataset_updated_at",
            "source_dataset",
        ]:
            if actual[field] != expected[field]:
                raise ValueError(f"Normalized artifact disagrees with source field: {field}")
        is_error = any(i["severity"] == "ERROR" for i in actual["issues"])
        if is_error != (actual["quality_status"] == "ERROR"):
            raise ValueError("Error status does not match issues")
        if any(i["severity"] == "ERROR" for i in expected["issues"]) and not is_error:
            raise ValueError("Invalid source row was removed from quarantine")
        errors += is_error
    if errors != report["error_rows"] or len(raw) - errors != report["accepted_rows"]:
        raise ValueError("Quality report row counts disagree")
    return {
        "profile": profile,
        "report": report,
        "raw": raw,
        "normalized": normalized,
        "artifact_sha256": fingerprint(
            {name: hashlib.sha256(value).hexdigest() for name, value in sorted(blobs.items())}
        ),
    }
