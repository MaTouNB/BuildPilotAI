import argparse
import csv
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from zipfile import BadZipFile

from app.etl import EXPECTED_SHA256
from app.etl.pipeline import run_pipeline, write_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="File-only historical USD ETL; never imports a database"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256", default=EXPECTED_SHA256)
    parser.add_argument(
        "--dry-run", action="store_true", help="Write reports only, no raw/cleaned JSONL"
    )
    args = parser.parse_args()
    started = time.monotonic()
    try:
        profile, report, raw, normalized = run_pipeline(args.input, args.expected_sha256)
        write_artifacts(
            args.input, args.output, profile, report, raw, normalized, dry_run=args.dry_run
        )
    except (ValueError, OSError, BadZipFile, csv.Error) as exc:
        parser.exit(2, f"ETL clean failed: {exc}\n")
    print(
        json.dumps(
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "dry_run": args.dry_run,
                "import_batch_id": report["import_batch_id"],
                "source_rows": profile["total_rows"],
                "error_rows": report["error_rows"],
                "warning_rows": report["warning_rows"],
                "price_conflict_groups": report["price_conflict_groups"],
            }
        )
    )


if __name__ == "__main__":
    main()
