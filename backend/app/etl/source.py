import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from app.etl import DATASET_UPDATED_AT, PIPELINE_VERSION, SOURCE


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_source(path: Path, expected_sha256: str) -> tuple[str, list[dict], list[dict]]:
    """Read in memory without extracting archive paths or modifying the source."""
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"Input SHA-256 mismatch: expected {expected_sha256}, got {digest}")
    rows: list[dict] = []
    files = []
    batch_id = fingerprint([digest, PIPELINE_VERSION])
    with ZipFile(io.BytesIO(content)) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len({entry.filename for entry in entries}) != len(entries):
            raise ValueError("Duplicate ZIP member paths are ambiguous")
        if sum(entry.file_size for entry in entries) > 128 * 1024 * 1024:
            raise ValueError("Archive exceeds 128 MiB expanded-size limit")
        for entry in sorted(entries, key=lambda item: item.filename):
            member = PurePosixPath(entry.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in entry.filename:
                raise ValueError(f"Unsafe ZIP path: {entry.filename}")
            if member.suffix.lower() != ".csv":
                continue
            if entry.file_size > 32 * 1024 * 1024:
                raise ValueError(f"CSV exceeds 32 MiB limit: {entry.filename}")
            with archive.open(entry) as binary:
                reader = csv.DictReader(
                    io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""), strict=True
                )
                fields = reader.fieldnames
                if not fields or len(set(fields)) != len(fields) or "" in fields:
                    raise ValueError(f"Invalid CSV header: {entry.filename}")
                if not {"name", "price"}.issubset(fields):
                    raise ValueError(f"Missing name/price header: {entry.filename}")
                files.append(
                    {"source_file": entry.filename, "category": member.stem, "fields": fields}
                )
                previous_line = reader.line_num
                for ordinal, raw in enumerate(reader, start=1):
                    start_line = previous_line + 1
                    previous_line = reader.line_num
                    extra = raw.pop(None, None)
                    row = {
                        "source_dataset": SOURCE,
                        "dataset_updated_at": DATASET_UPDATED_AT,
                        "source_dataset_version": digest,
                        "pipeline_version": PIPELINE_VERSION,
                        "import_batch_id": batch_id,
                        "source_file": entry.filename,
                        "source_category": member.stem,
                        "source_line_number": start_line,
                        "source_end_line_number": previous_line,
                        "source_row_number": ordinal,
                        "raw_data": raw,
                        "extra_columns": extra,
                        "structural_error": extra is not None
                        or any(v is None for v in raw.values()),
                    }
                    row["source_record_hash"] = fingerprint([raw, extra])
                    row["source_record_key"] = fingerprint([digest, entry.filename, ordinal])
                    rows.append(row)
    if not files:
        raise ValueError("Archive contains no CSV files")
    return digest, rows, files
