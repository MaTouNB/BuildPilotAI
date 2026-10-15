"""Deterministic, evidence-only rules. No model names or price data imply specs."""

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models import BuildItem, HardwarePart, PartSpecOverride

RULESET = "m08.1"
FIELDS = {
    "CPU": {"socket": None},
    "MOTHERBOARD": {"socket": None, "memory_type": None},
    "MEMORY": {"memory_type": None},
    "GPU": {"length_mm": "mm"},
    "CASE": {"max_gpu_length_mm": "mm"},
}
UNCOVERED = [
    "BIOS 版本与 CPU 支持列表",
    "内存 QVL、容量、插槽与频率",
    "电源功率、供电接口与线材",
    "主板尺寸、散热器及显卡宽度/高度",
    "安装风扇/冷排后的空间变化、其他组件和整机可运行性",
]


def normalized(field, value, unit):
    if field.endswith("_mm"):
        if (
            unit != "mm"
            or not isinstance(value, str)
            or not re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,6})?", value)
        ):
            raise ValueError("尺寸必须为带 mm 单位的十进制字符串")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("尺寸无效") from exc
        if not number.is_finite() or not 0 < number <= 3000:
            raise ValueError("尺寸必须在 (0, 3000] mm 内")
        canonical = format(number, "f")
        return canonical.rstrip("0").rstrip(".") if "." in canonical else canonical
    if unit is not None or not isinstance(value, str):
        raise ValueError("代际/插槽必须是无单位字符串")
    value = value.strip().upper()
    if field == "memory_type":
        if value not in {"DDR", "DDR2", "DDR3", "DDR4", "DDR5"}:
            raise ValueError("未支持的 DDR 代际")
    elif not re.fullmatch(
        r"(?:AM[2-5]|FM[12]|LGA[0-9]{3,4}|TR[0-9A-Z]{1,8}|STR[0-9A-Z]{1,8})", value
    ):
        raise ValueError("未支持的 socket 标识；需要扩充审核词表")
    return value


def resolve_field(rows, field):
    # Pending/rejected successors never suppress approved evidence.
    approved = [row for row in rows if row["review_status"] == "APPROVED"]
    superseded = {row["supersedes_id"] for row in approved if row["supersedes_id"]}
    current = [row for row in approved if row["id"] not in superseded]
    evidence = [
        {
            key: row[key]
            for key in (
                "id",
                "part_id",
                "field_name",
                "value",
                "unit",
                "source_url",
                "source_locator",
                "retrieved_at",
                "version",
            )
        }
        for row in current
    ]
    values = set()
    for row in current:
        try:
            values.add(normalized(field, row["value"], row["unit"]))
        except ValueError:
            return None, evidence, "INVALID_EVIDENCE"
    if len(values) > 1:
        return None, evidence, "CONFLICT_REVIEW"
    if not values:
        return None, evidence, "MISSING_APPROVED_EVIDENCE"
    return values.pop(), evidence, None


def evaluate(parts, rows):
    """parts maps slot to active part id; rows are immutable review records."""
    rules = []
    definitions = [
        ("CPU_SOCKET", "CPU 与主板插槽", ("CPU", "socket"), ("MOTHERBOARD", "socket")),
        (
            "MEMORY_DDR",
            "内存与主板 DDR 代际",
            ("MEMORY", "memory_type"),
            ("MOTHERBOARD", "memory_type"),
        ),
        (
            "GPU_LENGTH",
            "显卡长度与机箱标称空间",
            ("GPU", "length_mm"),
            ("CASE", "max_gpu_length_mm"),
        ),
    ]
    for rule_id, title, left, right in definitions:
        values, evidence, missing, issues = [], [], [], []
        for slot, field in (left, right):
            part_id = parts.get(slot)
            if part_id is None:
                values.append(None)
                missing.append(f"{slot}.part_id")
                issues.append(f"{slot}: MISSING_ACTIVE_PART")
                continue
            selected = [
                row for row in rows if row["part_id"] == part_id and row["field_name"] == field
            ]
            value, proof, problem = resolve_field(selected, field)
            values.append(value)
            evidence.extend(proof)
            if problem:
                missing.append(f"{slot}.{field}")
                issues.append(f"{slot}.{field}: {problem}")
        status = "UNKNOWN"
        if not missing:
            matches = (
                Decimal(values[0]) <= Decimal(values[1])
                if rule_id == "GPU_LENGTH"
                else values[0] == values[1]
            )
            status = "PASS" if matches else "FAIL"
        rules.append(
            dict(
                rule_id=rule_id,
                title=title,
                status=status,
                evidence=evidence,
                missing_fields=missing,
                issues=issues,
                compared_values=values,
                explanation="证据不足或冲突，需补充审核"
                if missing
                else "仅本条条件满足"
                if status == "PASS"
                else "本条条件不满足",
            )
        )
    statuses = {rule["status"] for rule in rules}
    return dict(
        ruleset_version=RULESET,
        checked_rules_status="FAIL"
        if "FAIL" in statuses
        else "UNKNOWN"
        if "UNKNOWN" in statuses
        else "PASS",
        rules=rules,
        uncovered_checks=UNCOVERED,
    )


def report(db, build):
    # One statement gives a consistent snapshot of items, part activity and evidence.
    records = db.execute(
        select(BuildItem.slot, HardwarePart.id, PartSpecOverride)
        .join(HardwarePart, HardwarePart.id == BuildItem.part_id)
        .outerjoin(PartSpecOverride, PartSpecOverride.part_id == HardwarePart.id)
        .where(BuildItem.build_id == build.id, HardwarePart.is_active.is_(True))
        .order_by(BuildItem.slot, PartSpecOverride.version)
    ).all()
    parts, rows = {}, []
    for slot, part_id, row in records:
        parts[slot] = str(part_id)
        if row is not None:
            rows.append(
                {
                    "id": str(row.id),
                    "part_id": str(row.part_id),
                    "field_name": row.field_name,
                    "value": row.value,
                    "unit": row.unit,
                    "source_url": row.source_url,
                    "source_locator": row.source_locator,
                    "retrieved_at": row.retrieved_at.isoformat(),
                    "review_status": row.review_status,
                    "version": row.version,
                    "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
                }
            )
    return dict(
        build_id=str(build.id),
        build_version=build.version,
        checked_at=datetime.now(UTC).isoformat(),
        **evaluate(parts, rows),
    )
