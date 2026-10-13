"""Optional local full-dataset acceptance; CI uses synthetic fixtures instead."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "data/raw/csv.zip"
output = root / "data/processed/m01"
os.environ.setdefault("UV_CACHE_DIR", "/tmp/buildpilot-uv-cache")
expected = "5c3073dbe3ea2241c8b907b0a119bec6992fa4d3335c958569027c2f066b49ab"
if not source.is_file():
    raise SystemExit("Local source ZIP is missing; it is intentionally excluded from Git")
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected


def run(module: str, destination: Path, *extra: str) -> None:
    subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "python",
            "-m",
            module,
            "--input",
            str(source),
            "--output",
            str(destination),
            *extra,
        ],
        cwd=root / "backend",
        check=True,
    )


run("app.etl.profile", root / "data/processed/m01-profile.json")
run("app.etl.clean", root / "data/processed/m01-dry-run", "--dry-run")
run("app.etl.clean", output)
first = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
run("app.etl.clean", output)
second = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
assert first == second, "Repeated exports differ"
profile = json.loads((output / "profile.json").read_text())
baseline = json.loads((root / "data/reports/source_profile.json").read_text())
for key in ["total_files", "total_rows", "price_nonempty_rows", "price_empty_rows"]:
    assert profile[key] == baseline[key], key
for category in profile["categories"]:
    original = next(c for c in baseline["categories"] if c["file"] == category["source_file"])
    for key in ["rows", "price_nonempty", "price_empty", "fields"]:
        assert category[key] == original[key], (category["category"], key)
counts = {}
for path in output.glob("*.jsonl"):
    with path.open() as stream:
        counts[path.name] = sum(1 for _ in stream)
assert counts["raw.jsonl"] == 66778
assert sum(value for key, value in counts.items() if key != "raw.jsonl") == 66778
report = json.loads((output / "quality_report.json").read_text())
assert report["accepted_rows"] + report["error_rows"] == 66778
assert counts["quarantine.jsonl"] == report["error_rows"]
assert counts["extensions.jsonl"] == report["extension_rows"]
assert (
    sum(value for key, value in counts.items() if key.startswith("cleaned_"))
    == report["normalized_main_rows"]
)
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
print("PASS: all 25 category baselines, complete row partition, identical exports and source hash")
