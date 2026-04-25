from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from . import jongmok_parser
from .schemas import BidExtraction, RawExtraction, Rule, ValidationIssue

_PERCENT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*%\s*$")
_INT_LIKE = re.compile(r"^[\s\-+]*[\d,]+\s*원?\s*$")
_DECIMAL_LIKE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")

_ENUMS: dict[str, set[str]] = {
    "enum_bid_method": {"일반경쟁", "제한경쟁", "협상에 의한 계약", "수의계약", "지명경쟁"},
    "enum_general_etc_specialty": {"일반", "기타", "전문"},
    "enum_bid_status": {"공고중", "마감", "취소", "정정공고"},
}


def validate(raw: RawExtraction, rules: list[Rule]) -> BidExtraction:
    issues: list[ValidationIssue] = []
    extracted: dict[str, Any] = dict(raw.extracted or {})
    source: dict[str, str] = dict(raw.source or {})
    rules_by_key = {r.key: r for r in rules}

    for rule in rules:
        value = extracted.get(rule.key)

        if rule.required and _is_empty(value):
            issues.append(ValidationIssue(key=rule.key, kind="missing", detail="required value not present"))
            continue

        if _is_empty(value):
            extracted[rule.key] = None
            continue

        coerced, coerce_issue = _coerce(value, rule)
        if coerce_issue:
            issues.append(coerce_issue)
        extracted[rule.key] = coerced

        if rule.validator:
            issue = _apply_validator(rule, coerced, extracted)
            if issue:
                issues.append(issue)

        if not source.get(rule.key) and not _is_empty(coerced):
            issues.append(ValidationIssue(key=rule.key, kind="missing_source", detail="source_text not provided"))

    issues.extend(_arith_checks(extracted, rules_by_key))
    return BidExtraction(extracted=extracted, source=source, issues=issues)


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _coerce(value: Any, rule: Rule) -> tuple[Any, ValidationIssue | None]:
    t = rule.type
    try:
        if t == "int":
            return _to_int(value), None
        if t == "float":
            return _to_float(value), None
        if t == "bool":
            return _to_bool(value), None
        if t == "list[str]":
            return _to_list_str(value), None
        if t == "nested_list[str]":
            return jongmok_parser.normalize_jongmok(value), None
        if t == "dict":
            if isinstance(value, dict):
                return value, None
            return value, ValidationIssue(key=rule.key, kind="bad_type", detail=f"expected dict, got {type(value).__name__}")
        if t == "date":
            return _to_iso_date(value), None
        return str(value).strip(), None
    except (ValueError, TypeError) as exc:
        return value, ValidationIssue(key=rule.key, kind="coerce_failed", detail=str(exc))


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip().replace(",", "").rstrip("원").strip()
    if not s:
        raise ValueError("empty")
    return int(float(s))


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    pct = _PERCENT.match(s)
    if pct:
        return round(float(pct.group(1)) / 100.0, 5)
    s = s.replace(",", "")
    return float(s)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y", "가능", "있음", "예", "허용"}:
        return True
    if s in {"false", "0", "no", "n", "불가", "없음", "아니오", "불허"}:
        return False
    raise ValueError(f"cannot parse bool from {value!r}")


def _to_list_str(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [s.strip() for s in re.split(r"[,\n;]+", str(value)) if s.strip()]


def _to_iso_date(value: Any) -> str:
    s = str(value).strip().replace(".", "-").replace("/", "-")
    return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")


def _apply_validator(rule: Rule, value: Any, extracted: dict[str, Any]) -> ValidationIssue | None:
    vid = rule.validator or ""
    if vid == "ratio_0_1":
        try:
            f = float(value)
        except (TypeError, ValueError):
            return ValidationIssue(key=rule.key, kind="bad_type", detail="not numeric")
        if not (0.0 <= f <= 1.0):
            return ValidationIssue(key=rule.key, kind="out_of_range", detail=f"{f} not in [0,1]")
        return None
    if vid == "positive_int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return ValidationIssue(key=rule.key, kind="bad_type", detail="not integer")
        if n <= 0:
            return ValidationIssue(key=rule.key, kind="out_of_range", detail=f"{n} <= 0")
        return None
    if vid == "zero_or_one":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return ValidationIssue(key=rule.key, kind="bad_type", detail="not integer")
        if n not in (0, 1):
            return ValidationIssue(key=rule.key, kind="out_of_range", detail=f"{n} not in (0,1)")
        return None
    if vid == "iso_datetime":
        if not _looks_like_iso_datetime(value):
            return ValidationIssue(key=rule.key, kind="bad_format", detail=f"{value!r} not ISO 8601 datetime")
        return None
    if vid == "iso_date":
        try:
            datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            return ValidationIssue(key=rule.key, kind="bad_format", detail=f"{value!r} not ISO date")
        return None
    if vid == "jongmok_normalize":
        if not isinstance(value, list) or not all(isinstance(g, list) for g in value):
            return ValidationIssue(key=rule.key, kind="bad_type", detail="not nested list")
        return None
    if vid in _ENUMS:
        if str(value) not in _ENUMS[vid]:
            return ValidationIssue(
                key=rule.key,
                kind="bad_enum",
                detail=f"{value!r} not in {sorted(_ENUMS[vid])}",
            )
        return None
    return None


def _looks_like_iso_datetime(value: Any) -> bool:
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _arith_checks(extracted: dict[str, Any], rules_by_key: dict[str, Rule]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    parts = ("재료비", "노무비", "경비")
    if "순공사원가" in extracted and any(p in extracted for p in parts):
        try:
            total = int(extracted["순공사원가"])
            summed = sum(int(extracted.get(p, 0) or 0) for p in parts)
            if total != summed:
                issues.append(
                    ValidationIssue(
                        key="순공사원가",
                        kind="arith_mismatch",
                        detail=f"순공사원가({total}) != 재료비+노무비+경비({summed})",
                    )
                )
        except (TypeError, ValueError):
            pass
    return issues
