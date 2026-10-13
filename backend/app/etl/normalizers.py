import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.etl import CATEGORIES, DATASET_UPDATED_AT, SOURCE
from app.etl.source import fingerprint


def normalize_text(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def parse_price(value: str | None) -> Decimal | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > 64:
        raise ValueError("Price exceeds supported length")
    try:
        amount = Decimal(text)
        if not amount.is_finite() or not Decimal(0) < amount < Decimal("10000000000"):
            raise ValueError("Price must be finite, positive and fit NUMERIC(12,2)")
        rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal price") from exc
    if not Decimal(0) < rounded < Decimal("10000000000"):
        raise ValueError("Rounded price must be positive and fit NUMERIC(12,2)")
    return rounded


def positive_integer(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value.strip()):
        raise ValueError("Expected a positive integer")
    result = int(value)
    if result <= 0:
        raise ValueError("Expected a positive integer")
    return result


def parse_pair(value: str) -> tuple[int, int]:
    pieces = value.split(",")
    if len(pieces) != 2:
        raise ValueError("Expected exactly two comma-separated positive integers")
    return positive_integer(pieces[0]), positive_integer(pieces[1])


def normalize_row(row: dict) -> dict:
    raw = row["raw_data"]
    category = CATEGORIES.get(row["source_category"], "EXTENSION")
    issues: list[dict] = []

    def issue(severity: str, field: str, code: str, message: str) -> None:
        issues.append(
            {"severity": severity, "field_name": field, "rule_code": code, "message": message}
        )

    name = normalize_text(raw.get("name"))
    if row["structural_error"]:
        issue("ERROR", "row", "COLUMN_STRUCTURE", "Missing or extra CSV columns")
    if not name:
        issue("ERROR", "name", "NAME_REQUIRED", "Name is empty")
    price = None
    try:
        price = parse_price(raw.get("price"))
    except ValueError as exc:
        issue("ERROR", "price", "INVALID_PRICE", str(exc))
    if not (raw.get("price") or "").strip():
        issue(
            "WARN", "price", "PRICE_MISSING", "Historical price is missing; never filled with zero"
        )
    elif price is not None and Decimal(raw["price"].strip()) != price:
        issue("WARN", "price", "PRICE_ROUNDED", "ROUND_HALF_UP applied to two decimal places")

    source_specs = {
        k: normalize_text(v) or None
        for k, v in raw.items()
        if k not in {"name", "price", "price_per_gb"}
    }
    specifications: dict = {"source_fields": source_specs}

    def number(field: str, target: str, *, integer: bool = False, allow_zero: bool = False) -> None:
        text = (raw.get(field) or "").strip()
        specifications[target] = None
        if not text:
            issue("WARN", field, "SPEC_MISSING", "Specification is missing")
            return
        try:
            amount = Decimal(text)
            if not amount.is_finite():
                raise ValueError("Expected a finite value")
            if amount < 0 or (not allow_zero and amount == 0) or amount >= Decimal("1e12"):
                issue("WARN", field, "SPEC_OUT_OF_RANGE", "Review unsupported numeric magnitude")
                return
            if integer and amount != amount.to_integral_value():
                raise ValueError("Expected an integer")
            specifications[target] = int(amount) if integer else format(amount, "f")
        except (InvalidOperation, ValueError):
            issue("ERROR", field, "INVALID_SPEC", "Invalid numeric specification")

    def unknown(field: str) -> None:
        specifications[field] = None
        issue("WARN", field, "COMPATIBILITY_UNKNOWN", "Source lacks a verified compatibility field")

    if category == "CPU":
        number("core_count", "core_count", integer=True)
        number("tdp", "tdp_w")
        number("core_clock", "core_clock_source_value")
        number("boost_clock", "boost_clock_source_value")
        specifications["clock_unit"] = None
        unknown("socket")
    elif category == "MOTHERBOARD":
        number("max_memory", "max_memory_gb", integer=True)
        number("memory_slots", "memory_slots", integer=True)
        specifications["socket"] = source_specs.get("socket")
        specifications["form_factor"] = source_specs.get("form_factor")
        if not specifications["socket"]:
            issue("WARN", "socket", "SPEC_MISSING", "Socket is missing")
        unknown("memory_generation")
    elif category == "GPU":
        number("memory", "memory_gb")
        number("length", "length_mm")
        number("core_clock", "core_clock_source_value")
        number("boost_clock", "boost_clock_source_value")
        specifications["clock_unit"] = None
        specifications["chipset"] = source_specs.get("chipset")
    elif category == "MEMORY":
        for field in ["speed", "modules"]:
            text = (raw.get(field) or "").strip()
            if not text:
                issue("WARN", field, "SPEC_MISSING", "Memory specification is missing")
                values = None
            else:
                try:
                    values = parse_pair(text)
                except ValueError as exc:
                    issue("ERROR", field, "INVALID_MEMORY_PAIR", str(exc))
                    values = None
            if field == "speed":
                specifications["ddr_generation"] = values[0] if values else None
                specifications["data_rate_mt_s"] = values[1] if values else None
            else:
                specifications["module_count"] = values[0] if values else None
                specifications["module_capacity_gb"] = values[1] if values else None
                specifications["total_capacity_gb"] = values[0] * values[1] if values else None
    elif category == "STORAGE":
        number("capacity", "capacity_gb")
        for field in ["type", "interface", "form_factor"]:
            specifications[field] = source_specs.get(field)
    elif category == "PSU":
        number("wattage", "wattage_w", integer=True)
        for field in ["type", "efficiency", "modular"]:
            specifications[field] = source_specs.get(field)
    elif category == "CASE":
        number("internal_35_bays", "internal_35_bays", integer=True, allow_zero=True)
        specifications["external_volume_source_value"] = source_specs.get("external_volume")
        unknown("max_gpu_length_mm")
    elif category == "COOLER":
        # RPM/noise/size may encode ranges or different kinds of dimensions; retain verbatim.
        for field in ["rpm", "noise_level", "size"]:
            specifications[field + "_source_value"] = source_specs.get(field)
        unknown("supported_sockets")
        unknown("height_mm")
    else:
        issue("WARN", "category", "EXTENSION_CATEGORY", "Category retained for a future normalizer")

    candidate_key = fingerprint([category, row["source_category"], name.casefold(), source_specs])
    result = {
        key: value
        for key, value in row.items()
        if key not in {"raw_data", "extra_columns", "structural_error"}
    }
    result.update(
        {
            "category": category,
            "name_raw": raw.get("name"),
            "name_normalized": name,
            "specifications_json": specifications,
            "candidate_key": candidate_key,
            "identity_status": "UNRESOLVED_SOURCE_CANDIDATE",
            "price_usd": format(price, ".2f") if price is not None else None,
            "currency": "USD",
            "quote_status": "VALID_REFERENCE_PRICE" if price is not None else "MISSING",
            "is_quotable": False,
            "price_quote": {
                "value": format(price, ".2f") if price is not None else None,
                "currency": "USD",
                "price_kind": "historical_reference",
                "dataset_updated_at": DATASET_UPDATED_AT,
                "source_dataset": SOURCE,
                "is_quotable": False,
            },
            "issues": issues,
        }
    )
    return result
