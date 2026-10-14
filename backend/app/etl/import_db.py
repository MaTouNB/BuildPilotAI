import argparse
import json
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.db import make_engine
from app.etl import EXPECTED_SHA256
from app.etl.artifacts import load_artifacts
from app.etl.importer import ReviewManifest, apply_artifacts, validate_reviews


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and import M01 artifacts into PostgreSQL"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--expected-sha256", default=EXPECTED_SHA256)
    parser.add_argument("--review-map", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Validate without connecting to PostgreSQL"
    )
    mode.add_argument("--apply", action="store_true", help="Apply after running Alembic migrations")
    args = parser.parse_args()
    engine = None
    try:
        data = load_artifacts(args.input, args.expected_sha256)
        reviews = (
            ReviewManifest.model_validate_json(args.review_map.read_text())
            if args.review_map
            else None
        )
        validate_reviews(data, reviews)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "validated",
                        "row_count": len(data["raw"]),
                        "error_count": data["report"]["error_rows"],
                        "artifact_sha256": data["artifact_sha256"],
                    }
                )
            )
        else:
            engine = make_engine()
            print(json.dumps(apply_artifacts(engine, data, reviews)))
    except SQLAlchemyError:
        parser.exit(
            2,
            "Database import failed; check migrations/connectivity. "
            "Data transaction rolled back.\n",
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"Import validation failed: {type(exc).__name__}: {exc}\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
