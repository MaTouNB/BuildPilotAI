from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.compatibility import evaluate
from app.spec_reviews import Review


def proof(part, field, value, **changes):
    return dict(
        id=str(uuid4()),
        part_id=part,
        field_name=field,
        value=value,
        unit="mm" if field.endswith("_mm") else None,
        source_url="https://example.test/spec",
        source_locator="synthetic fixture",
        retrieved_at="2026-01-01T00:00:00Z",
        review_status="APPROVED",
        reviewer="synthetic-reviewer",
        version=1,
        supersedes_id=None,
        **changes,
    )


def fixture():
    parts = {slot: slot for slot in ("CPU", "MOTHERBOARD", "MEMORY", "GPU", "CASE")}
    rows = [
        proof("CPU", "socket", "AM5"),
        proof("MOTHERBOARD", "socket", "AM5"),
        proof("MEMORY", "memory_type", "DDR5"),
        proof("MOTHERBOARD", "memory_type", "DDR5"),
        proof("GPU", "length_mm", "235"),
        proof("CASE", "max_gpu_length_mm", "235"),
    ]
    return parts, rows


@pytest.mark.parametrize(
    "index,value,expected",
    [
        (0, "AM4", "FAIL"),
        (1, "LGA1700", "FAIL"),
        (2, "DDR4", "FAIL"),
        (3, "DDR4", "FAIL"),
        (4, "236", "FAIL"),
        (5, "234.99", "FAIL"),
        (4, "234.99", "PASS"),
        (4, "NaN", "UNKNOWN"),
        (4, "Infinity", "UNKNOWN"),
        (4, "0", "UNKNOWN"),
        (4, "-1", "UNKNOWN"),
        (2, "LPDDR5", "UNKNOWN"),
    ],
)
def test_rule_outcomes(index, value, expected):
    parts, rows = fixture()
    rows[index]["value"] = value
    assert evaluate(parts, rows)["checked_rules_status"] == expected


def test_equal_clearance_pass_is_narrow_and_evidence_preserved():
    parts, rows = fixture()
    result = evaluate(parts, rows)
    assert result["checked_rules_status"] == "PASS"
    assert result["uncovered_checks"] and "BIOS" in result["uncovered_checks"][0]
    assert result["rules"][0]["evidence"][0]["source_locator"] == "synthetic fixture"


@pytest.mark.parametrize("status", ["PENDING", "REJECTED"])
def test_unapproved_never_participates(status):
    parts, rows = fixture()
    rows[0]["review_status"] = status
    assert evaluate(parts, rows)["rules"][0]["missing_fields"] == ["CPU.socket"]


def test_conflicts_and_explicit_supersession():
    parts, rows = fixture()
    conflict = proof("CPU", "socket", "AM4")
    conflict["version"] = 2
    rows.append(conflict)
    assert evaluate(parts, rows)["checked_rules_status"] == "UNKNOWN"
    # A pending replacement does not hide the conflict.
    replacement = proof("CPU", "socket", "AM5")
    replacement.update(version=3, supersedes_id=conflict["id"], review_status="PENDING")
    rows.append(replacement)
    assert evaluate(parts, rows)["checked_rules_status"] == "UNKNOWN"
    replacement["review_status"] = "APPROVED"
    assert evaluate(parts, rows)["checked_rules_status"] == "PASS"


def test_missing_parts_and_fail_dominates_unknown():
    parts, rows = fixture()
    del parts["CASE"]
    rows[0]["value"] = "AM4"
    result = evaluate(parts, rows)
    assert result["checked_rules_status"] == "FAIL"
    assert result["rules"][2]["status"] == "UNKNOWN"
    assert "CASE.part_id" in result["rules"][2]["missing_fields"]


def test_raw_values_without_review_and_wrong_units_never_pass():
    parts, rows = fixture()
    assert evaluate(parts, [])["checked_rules_status"] == "UNKNOWN"
    rows[4]["unit"] = "cm"
    assert evaluate(parts, rows)["rules"][2]["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "changes",
    [
        {"source_url": "javascript:alert(1)"},
        {"source_url": "https://u:p@example.test"},
        {"reviewer": " "},
        {"source_locator": " "},
        {"value": "guess"},
        {"retrieved_at": datetime.now(UTC) + timedelta(days=1)},
        {"version": True},
    ],
)
def test_review_rejects_invalid_input(changes):
    row = proof(str(uuid4()), "socket", "AM5")
    row.update(changes)
    with pytest.raises(ValidationError):
        Review.model_validate(row)


def test_decimal_context_cannot_round_an_oversize_gpu_into_pass():
    parts, rows = fixture()
    rows[4]["value"] = "235.000001"
    assert evaluate(parts, rows)["checked_rules_status"] == "FAIL"
    rows[4]["value"] = "235.0000000000000000000000000000000001"
    assert evaluate(parts, rows)["checked_rules_status"] == "UNKNOWN"
    rows[4]["value"] = "1e-999999999"
    assert evaluate(parts, rows)["checked_rules_status"] == "UNKNOWN"
