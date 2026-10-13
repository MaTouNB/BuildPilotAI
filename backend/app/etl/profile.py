import argparse
import csv
import json
import tempfile
from pathlib import Path
from zipfile import BadZipFile

from app.etl import EXPECTED_SHA256
from app.etl.pipeline import profile_source, safe_destination
from app.etl.source import canonical, read_source


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile historical USD hardware CSVs without writes to raw"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256", default=EXPECTED_SHA256)
    args = parser.parse_args()
    try:
        safe_destination(args.input, args.output)
        digest, rows, files = read_source(args.input, args.expected_sha256)
        result = profile_source(digest, rows, files)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical(result) + "\n")
        try:
            temporary.replace(args.output)
        finally:
            temporary.unlink(missing_ok=True)
    except (ValueError, OSError, BadZipFile, csv.Error) as exc:
        parser.exit(2, f"ETL profile failed: {exc}\n")
    print(
        json.dumps(
            {key: result[key] for key in ["total_files", "total_rows", "price_nonempty_rows"]}
        )
    )


if __name__ == "__main__":
    main()
