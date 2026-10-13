import shutil
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from app.etl import CATEGORIES, DATASET_UPDATED_AT, PIPELINE_VERSION, SOURCE, SOURCE_FIELDS
from app.etl.normalizers import normalize_row, normalize_text
from app.etl.source import canonical, read_source


def profile_source(digest: str, rows: list[dict], files: list[dict]) -> dict:
    by_file: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_file[row["source_file"]].append(row)
    categories = []
    for file in files:
        records = by_file[file["source_file"]]
        nonempty = sum(bool((row["raw_data"].get("price") or "").strip()) for row in records)
        names = Counter(normalize_text(row["raw_data"].get("name")).casefold() for row in records)
        categories.append(
            {
                **file,
                "rows": len(records),
                "price_nonempty": nonempty,
                "price_empty": len(records) - nonempty,
                "price_coverage_percent": (
                    format(Decimal(nonempty) * 100 / len(records), ".2f") if records else "0.00"
                ),
                "unknown_fields": sorted(
                    set(file["fields"])
                    - set(SOURCE_FIELDS.get(file["category"], "name price").split())
                ),
                "empty_fields": {
                    field: sum(not (row["raw_data"].get(field) or "").strip() for row in records)
                    for field in file["fields"]
                },
                "duplicate_source_rows": len(records)
                - len({r["source_record_hash"] for r in records}),
                "duplicate_name_groups": sum(count > 1 for count in names.values()),
                "structural_error_rows": sum(row["structural_error"] for row in records),
                "normalizer_supported": file["category"] in CATEGORIES,
            }
        )
    nonempty = sum(category["price_nonempty"] for category in categories)
    return {
        "source_dataset": SOURCE,
        "dataset_updated_at": DATASET_UPDATED_AT,
        "date_semantics": "Dataset-level last update; per-item observation time unknown",
        "currency": "USD",
        "input_sha256": digest,
        "pipeline_version": PIPELINE_VERSION,
        "total_files": len(files),
        "total_rows": len(rows),
        "price_nonempty_rows": nonempty,
        "price_empty_rows": len(rows) - nonempty,
        "categories": categories,
    }


def run_pipeline(path: Path, expected_sha256: str) -> tuple[dict, dict, list[dict], list[dict]]:
    digest, raw, files = read_source(path, expected_sha256)
    profile = profile_source(digest, raw, files)
    normalized = [normalize_row(row) for row in raw]
    groups: dict[str, list[dict]] = defaultdict(list)
    names: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in normalized:
        if not any(issue["severity"] == "ERROR" for issue in row["issues"]):
            groups[row["candidate_key"]].append(row)
            names[row["source_category"], row["name_normalized"].casefold()].add(
                row["candidate_key"]
            )
    conflict_groups = 0
    for group in groups.values():
        prices = {row["price_usd"] for row in group if row["price_usd"] is not None}
        conflict = len(prices) > 1
        conflict_groups += conflict
        for row in group:
            if len(group) > 1:
                row["issues"].append(
                    {
                        "severity": "WARN",
                        "field_name": "identity",
                        "rule_code": "DUPLICATE_CANDIDATE",
                        "message": "Possible duplicate; source rows retained, not merged",
                    }
                )
            if conflict:
                row["quote_status"] = "CONFLICT_REVIEW"
                row["issues"].append(
                    {
                        "severity": "WARN",
                        "field_name": "price",
                        "rule_code": "PRICE_CONFLICT",
                        "message": "Conflicting reference prices; no automatic selection",
                    }
                )
            if len(names[row["source_category"], row["name_normalized"].casefold()]) > 1:
                row["issues"].append(
                    {
                        "severity": "WARN",
                        "field_name": "identity",
                        "rule_code": "SAME_NAME_VARIANTS",
                        "message": "Same name has different specifications; not merged",
                    }
                )
    rules: Counter = Counter()
    severities: Counter = Counter()
    count_keys = [
        "source_rows",
        "error_rows",
        "accepted_rows",
        "warning_rows",
        "valid_price_rows",
        "conflict_rows",
    ]
    by_category: dict[str, Counter] = defaultdict(lambda: Counter(dict.fromkeys(count_keys, 0)))
    for row in normalized:
        errors = any(issue["severity"] == "ERROR" for issue in row["issues"])
        row["quality_status"] = "ERROR" if errors else "REVIEW" if row["issues"] else "VALID"
        if errors:
            row["quote_status"] = "INVALID"
        counts = by_category[row["source_category"]]
        counts["source_rows"] += 1
        counts["error_rows" if errors else "accepted_rows"] += 1
        counts["warning_rows"] += any(issue["severity"] == "WARN" for issue in row["issues"])
        counts["valid_price_rows"] += row["price_usd"] is not None and not errors
        counts["conflict_rows"] += row["quote_status"] == "CONFLICT_REVIEW"
        for issue in row["issues"]:
            rules[issue["rule_code"]] += 1
            severities[issue["severity"]] += 1
    errors = sum(c["error_rows"] for c in by_category.values())
    extensions = sum(row["category"] == "EXTENSION" for row in normalized)
    report = {
        "source_dataset": SOURCE,
        "dataset_updated_at": DATASET_UPDATED_AT,
        "currency": "USD",
        "price_kind": "historical_reference",
        "input_sha256": digest,
        "pipeline_version": PIPELINE_VERSION,
        "import_batch_id": raw[0]["import_batch_id"] if raw else None,
        "total_rows": len(raw),
        "accepted_rows": len(raw) - errors,
        "error_rows": errors,
        "warning_rows": sum(c["warning_rows"] for c in by_category.values()),
        "extension_rows": extensions,
        "normalized_main_rows": sum(
            row["category"] != "EXTENSION" and row["quality_status"] != "ERROR"
            for row in normalized
        ),
        "price_conflict_groups": conflict_groups,
        "duplicate_candidate_groups": sum(len(group) > 1 for group in groups.values()),
        "same_name_variant_groups": sum(len(keys) > 1 for keys in names.values()),
        "severity_counts": dict(severities),
        "rule_counts": dict(rules),
        "categories": {key: dict(value) for key, value in sorted(by_category.items())},
        "identity_note": (
            "Source candidates are not canonical SKUs; "
            "is_quotable remains false until catalog review"
        ),
        "unit_policy": "Clock/source-only dimensions retain source values without guessed units",
    }
    return profile, report, raw, normalized


def safe_destination(source: Path, destination: Path) -> None:
    resolved_source, resolved_destination = source.resolve(), destination.resolve()
    if destination.is_symlink() or resolved_source.is_relative_to(resolved_destination):
        raise ValueError("Output must not replace or contain the input")
    if source.parent.name == "raw" and resolved_destination.is_relative_to(source.parent.resolve()):
        raise ValueError("Writing inside the raw input directory is forbidden")


def write_json(path: Path, value: object) -> None:
    path.write_text(canonical(value) + "\n", encoding="utf-8")


def write_artifacts(
    source: Path,
    output: Path,
    profile: dict,
    report: dict,
    raw: list[dict],
    normalized: list[dict],
    *,
    dry_run: bool,
) -> None:
    """Replace only a recognized ETL output directory; stage all files before publishing."""
    safe_destination(source, output)
    allowed = {
        "profile.json",
        "quality_report.json",
        ".etl-manifest.json",
        "raw.jsonl",
        "quarantine.jsonl",
        "extensions.jsonl",
    }
    allowed.update(f"cleaned_{category.lower()}.jsonl" for category in CATEGORIES.values())
    if output.exists():
        import json

        marker = output / ".etl-manifest.json"
        if not marker.is_file() or marker.is_symlink():
            raise ValueError("Existing output is not an owned ETL directory")
        manifest = json.loads(marker.read_text())
        if (
            manifest.get("owner") != "buildpilot-etl"
            or manifest.get("input_sha256") != profile["input_sha256"]
        ):
            raise ValueError("Existing output belongs to a different input or owner")
        if dry_run and manifest.get("dry_run") is False:
            raise ValueError("Dry-run requires a separate report directory; exports are preserved")
        if any(
            item.name not in allowed or item.is_symlink() or not item.is_file()
            for item in output.iterdir()
        ):
            raise ValueError("Output contains foreign files; refusing to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".buildpilot-etl-", dir=output.parent))
    backup: Path | None = None
    try:
        write_json(stage / "profile.json", profile)
        write_json(stage / "quality_report.json", report)
        if not dry_run:

            def jsonlines(name: str, rows: list[dict]) -> None:
                with (stage / name).open("w", encoding="utf-8") as stream:
                    for row in rows:
                        stream.write(canonical(row) + "\n")

            jsonlines("raw.jsonl", raw)
            quarantined = []
            accepted = []
            for source_row, row in zip(raw, normalized, strict=True):
                if row["quality_status"] == "ERROR":
                    quarantined.append(
                        {
                            **row,
                            "raw_data": source_row["raw_data"],
                            "extra_columns": source_row["extra_columns"],
                        }
                    )
                else:
                    accepted.append(row)
            jsonlines("quarantine.jsonl", quarantined)
            jsonlines(
                "extensions.jsonl", [row for row in accepted if row["category"] == "EXTENSION"]
            )
            for category in CATEGORIES.values():
                jsonlines(
                    f"cleaned_{category.lower()}.jsonl",
                    [row for row in accepted if row["category"] == category],
                )
        write_json(
            stage / ".etl-manifest.json",
            {
                "owner": "buildpilot-etl",
                "input_sha256": profile["input_sha256"],
                "pipeline_version": PIPELINE_VERSION,
                "dry_run": dry_run,
            },
        )
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=".buildpilot-backup-", dir=output.parent))
            backup.rmdir()
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup is not None:
                backup.rename(output)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
