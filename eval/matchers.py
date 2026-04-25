"""eval/matchers.py — 8종 매처 + validator→matcher 자동 매핑.

각 매처 시그니처:
    def match_xxx(expected, actual, *, rule=None) -> tuple[float, str]
    반환: (score 0~1.0, 설명 문자열)
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _norm_datetime(s: str) -> str:
    """ISO 8601 datetime 문자열을 분 단위로 정규화 (초 이하 제거)."""
    s = s.strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s[:16], fmt[:16])
            return dt.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    # 날짜만 있는 경우
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return s


def _normalize_text(s: str) -> str:
    """공백·구두점·괄호 제거 후 소문자 변환."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s　 ]+", "", s)           # 공백 제거
    s = re.sub(r"[.,;:!?·、。，！？''\"\"()（）【】\[\]{}]", "", s)  # 구두점·괄호
    return s.lower()


def _keyword_overlap(expected: str, actual: str) -> float:
    """키워드 집합 기준 overlap 비율 (actual이 expected 키워드의 몇 %를 포함하는지)."""
    # 2글자 이상 토큰만 사용
    e_tokens = set(t for t in re.split(r"\W+", expected) if len(t) >= 2)
    a_tokens = set(t for t in re.split(r"\W+", actual) if len(t) >= 2)
    if not e_tokens:
        return 1.0
    return len(e_tokens & a_tokens) / len(e_tokens)


# ---------------------------------------------------------------------------
# 8종 매처
# ---------------------------------------------------------------------------

def match_exact(
    expected: Any,
    actual: Any,
    *,
    rule: dict | None = None,
) -> tuple[float, str]:
    """값 동일 여부 (타입 무시, str 변환 후 비교)."""
    if expected == actual:
        return 1.0, "exact match"
    # str 변환 후 재시도
    if str(expected).strip() == str(actual).strip():
        return 1.0, "exact match (str)"
    return 0.0, f"mismatch: expected={expected!r}, actual={actual!r}"


def match_numeric_tolerance(
    expected: Any,
    actual: Any,
    *,
    tol: float = 1e-5,
    rule: dict | None = None,
) -> tuple[float, str]:
    """float/int 절대 오차 허용."""
    try:
        e_f = float(expected)
        a_f = float(actual)
    except (TypeError, ValueError):
        return 0.0, f"non-numeric: expected={expected!r}, actual={actual!r}"
    diff = abs(e_f - a_f)
    if diff <= tol:
        return 1.0, f"within tol={tol} (diff={diff:.2e})"
    return 0.0, f"diff={diff:.2e} > tol={tol}"


def match_iso_datetime_eq(
    expected: Any,
    actual: Any,
    *,
    rule: dict | None = None,
) -> tuple[float, str]:
    """분 단위 동일 여부 (ISO datetime 정규화)."""
    if expected is None and actual is None:
        return 1.0, "both null"
    if expected is None or actual is None:
        return 0.0, f"null mismatch: expected={expected!r}, actual={actual!r}"
    try:
        e_norm = _norm_datetime(str(expected))
        a_norm = _norm_datetime(str(actual))
    except Exception:
        return 0.0, "parse error"
    if e_norm == a_norm:
        return 1.0, f"datetime match: {e_norm}"
    return 0.0, f"mismatch: {e_norm!r} != {a_norm!r}"


def match_set_eq(
    expected: Any,
    actual: Any,
    *,
    rule: dict | None = None,
) -> tuple[float, str]:
    """list → set 동일 여부."""
    def _to_set(v: Any) -> set:
        if v is None:
            return set()
        if isinstance(v, list):
            return {str(x).strip() for x in v}
        return {str(v).strip()}

    e_set = _to_set(expected)
    a_set = _to_set(actual)
    if e_set == a_set:
        return 1.0, "set match"
    extra = a_set - e_set
    missing = e_set - a_set
    parts = []
    if missing:
        parts.append(f"missing={missing}")
    if extra:
        parts.append(f"extra={extra}")
    return 0.0, "; ".join(parts)


def match_nested_set_eq(
    expected: Any,
    actual: Any,
    *,
    rule: dict | None = None,
) -> tuple[float, str]:
    """nested_list[str] — OR 그룹 집합 + 각 AND 묶음 frozenset 비교.

    예: [['A','B'],['C']] == [['B','A'],['C']] → 1.0
    """
    def _to_frozensets(v: Any) -> set[frozenset]:
        if v is None:
            return set()
        if isinstance(v, list):
            result: set[frozenset] = set()
            for item in v:
                if isinstance(item, list):
                    result.add(frozenset(str(x).strip() for x in item))
                else:
                    result.add(frozenset([str(item).strip()]))
            return result
        return {frozenset([str(v).strip()])}

    e_set = _to_frozensets(expected)
    a_set = _to_frozensets(actual)
    if e_set == a_set:
        return 1.0, "nested set match"
    missing = e_set - a_set
    extra = a_set - e_set
    parts = []
    if missing:
        parts.append(f"missing={[sorted(s) for s in missing]}")
    if extra:
        parts.append(f"extra={[sorted(s) for s in extra]}")
    return 0.0, "; ".join(parts)


def match_dict_keys_subset_eq(
    expected: Any,
    actual: Any,
    *,
    value_tol: float = 1,
    rule: dict | None = None,
) -> tuple[float, str]:
    """dict keys 동일 + 각 값 ±value_tol 허용."""
    if expected is None and actual is None:
        return 1.0, "both null"
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return 0.0, f"not dict: expected={type(expected).__name__}, actual={type(actual).__name__}"
    if set(expected.keys()) != set(actual.keys()):
        missing = set(expected.keys()) - set(actual.keys())
        extra = set(actual.keys()) - set(expected.keys())
        return 0.0, f"key mismatch missing={missing} extra={extra}"
    failures: list[str] = []
    for k in expected:
        try:
            e_v = float(expected[k])
            a_v = float(actual[k])
            if abs(e_v - a_v) > value_tol:
                failures.append(f"{k}: |{e_v}-{a_v}|>{value_tol}")
        except (TypeError, ValueError):
            if str(expected[k]).strip() != str(actual[k]).strip():
                failures.append(f"{k}: {expected[k]!r}!={actual[k]!r}")
    if not failures:
        return 1.0, f"dict match (tol={value_tol})"
    return 0.0, "; ".join(failures)


def match_text_normalized_eq(
    expected: Any,
    actual: Any,
    *,
    rule: dict | None = None,
) -> tuple[float, str]:
    """공백·구두점·괄호 제거 후 동일 여부."""
    if expected is None and actual is None:
        return 1.0, "both null"
    if expected is None or actual is None:
        return 0.0, "null mismatch"
    e_norm = _normalize_text(str(expected))
    a_norm = _normalize_text(str(actual))
    if e_norm == a_norm:
        return 1.0, "normalized text match"
    return 0.0, f"normalized mismatch: len(e)={len(e_norm)}, len(a)={len(a_norm)}"


def match_text_semantic(
    expected: Any,
    actual: Any,
    *,
    rule: dict | None = None,
) -> tuple[float, str]:
    """3단계: 정확(1.0) / 부분-키워드 70% 이상(0.5) / 임베딩 cosine≥0.85(0.5) / mismatch(0).

    EMBEDDING_PROVIDER 환경변수 비어있으면 임베딩 단계 skip.
    """
    if expected is None and actual is None:
        return 1.0, "both null"
    if expected is None or actual is None:
        return 0.0, "null mismatch"

    e_str = str(expected)
    a_str = str(actual)

    # 1단계: 정확 일치 (정규화 포함)
    if _normalize_text(e_str) == _normalize_text(a_str):
        return 1.0, "exact text match"

    # 2단계: 키워드 70% 이상 포함
    overlap = _keyword_overlap(e_str, a_str)
    if overlap >= 0.70:
        return 0.5, f"keyword overlap={overlap:.2f} ≥ 0.70"

    # 3단계: 임베딩 (EMBEDDING_PROVIDER가 설정된 경우만)
    provider = os.environ.get("EMBEDDING_PROVIDER", "").strip()
    if provider:
        try:
            score = _embedding_cosine(e_str, a_str, provider)
            if score >= 0.85:
                return 0.5, f"embedding cosine={score:.3f} ≥ 0.85"
        except Exception as exc:
            # 임베딩 실패 시 무시하고 mismatch 처리
            return 0.0, f"embedding error: {exc}; keyword overlap={overlap:.2f}"

    return 0.0, f"mismatch: keyword overlap={overlap:.2f}"


def _embedding_cosine(text1: str, text2: str, provider: str) -> float:
    """임베딩 cosine 유사도 (provider별 구현 placeholder)."""
    # D3 범위에서는 외부 의존 추가 안 함. provider가 설정된 경우의 확장 포인트.
    raise NotImplementedError(f"embedding provider={provider!r} not implemented in D3")


# ---------------------------------------------------------------------------
# 추가 메트릭
# ---------------------------------------------------------------------------

def score_source_grounding(
    expected_source: str | None,
    actual_source: str | None,
    raw_text: str,
) -> float:
    """actual_source가 raw_text의 substring인지 확인 (정확 추출 점수)."""
    if not actual_source:
        return 0.0
    # 정규화 후 substring 검사
    norm_raw = _normalize_text(raw_text)
    norm_src = _normalize_text(actual_source)
    if not norm_src:
        return 0.0
    return 1.0 if norm_src in norm_raw else 0.0


def is_hallucination(
    extracted_value: Any,
    source_text: str | None,
    raw_text: str,
) -> bool:
    """값이 채워졌는데 source가 raw_text에 없으면 True (hallucination 의심)."""
    # 값이 비어있으면 hallucination 아님
    if extracted_value is None or extracted_value == "" or extracted_value == [] or extracted_value == {}:
        return False
    # source 없으면 일단 True
    if not source_text:
        return True
    # source가 raw_text에 없으면 True
    norm_raw = _normalize_text(raw_text)
    norm_src = _normalize_text(str(source_text))
    if not norm_src:
        return True
    return norm_src not in norm_raw


# ---------------------------------------------------------------------------
# validator → matcher 매핑 (dict 기반, 하드코딩 금지)
# ---------------------------------------------------------------------------

# validator 값 → (matcher_fn, kwargs) 매핑
_VALIDATOR_MAP: dict[str, tuple[Callable, dict]] = {
    "ratio_0_1":               (match_numeric_tolerance, {"tol": 1e-5}),
    "positive_int":            (match_numeric_tolerance, {"tol": 0}),
    "iso_datetime":            (match_iso_datetime_eq,   {}),
    "iso_date":                (match_iso_datetime_eq,   {}),
    "zero_or_one":             (match_exact,             {}),
    "jongmok_normalize":       (match_nested_set_eq,     {}),
}

# validator prefix 패턴 → matcher (startswith로 매칭)
_VALIDATOR_PREFIX_MAP: list[tuple[str, Callable, dict]] = [
    ("enum_", match_exact, {}),
]

# type → matcher (validator 없을 때 fallback)
_TYPE_MAP: dict[str, tuple[Callable, dict]] = {
    "list[str]":         (match_set_eq,             {}),
    "nested_list[str]":  (match_nested_set_eq,      {}),
    "dict":              (match_dict_keys_subset_eq, {}),
    "float":             (match_numeric_tolerance,   {"tol": 1e-5}),
    "int":               (match_numeric_tolerance,   {"tol": 0}),
    "bool":              (match_exact,               {}),
}

# 자유 텍스트 판별 키워드 (description에 포함 시 semantic 사용)
_SEMANTIC_DESC_KEYWORDS = ("원문", "전문", "텍스트", "세부")


def matcher_for(rule: dict) -> Callable:
    """rule dict (key, type, validator, description)로 매처 함수 결정.

    반환: partial이 적용된 callable (expected, actual, *, rule=None) → (float, str)
    """
    import functools

    validator: str = (rule.get("validator") or "").strip()
    type_: str = (rule.get("type") or "str").strip()
    description: str = (rule.get("description") or "").strip()

    # 1) validator 정확 매핑
    if validator in _VALIDATOR_MAP:
        fn, kw = _VALIDATOR_MAP[validator]
        return functools.partial(fn, **kw)

    # 2) validator prefix 매핑 (enum_*)
    for prefix, fn, kw in _VALIDATOR_PREFIX_MAP:
        if validator.startswith(prefix):
            return functools.partial(fn, **kw)

    # 3) type 기반 fallback
    if type_ in _TYPE_MAP:
        fn, kw = _TYPE_MAP[type_]
        return functools.partial(fn, **kw)

    # 4) str 자유텍스트: description에 semantic 키워드 포함 시 semantic
    if type_ == "str":
        if any(kw in description for kw in _SEMANTIC_DESC_KEYWORDS):
            return match_text_semantic
        return match_text_normalized_eq

    # 5) 안전 기본값
    return match_exact
