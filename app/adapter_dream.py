"""꿈자동화 F02 ai_columns 스키마로 변환하는 얇은 어댑터."""
from __future__ import annotations

from typing import Any

from app.schemas import BidExtraction, ValidationIssue

# issue.kind → confidence 감산 기준 (낮을수록 더 불확실)
CONFIDENCE_FROM_ISSUE: dict[str, float] = {
    "missing_source": 0.7,
    "coerce_failed": 0.4,
    "bad_format": 0.4,
    "out_of_range": 0.3,
    "bad_enum": 0.3,
}


def to_dream_format(result: BidExtraction, *, required_keys: set[str]) -> list[dict]:
    """v1.0 분석 결과 → 꿈자동화 F02 Notice_AI 행 리스트.

    F02-1 ai_columns 스키마:
        column_name, value, confidence, source, source_text, page, mismatch
    """
    rows: list[dict] = []
    issues_by_key = _issues_by_key(result.issues)

    for key, value in result.extracted.items():
        confidence = _derive_confidence(
            key, value, issues_by_key.get(key, []), required_keys
        )
        rows.append(
            {
                "column_name": key,
                "value": value,
                "confidence": confidence,
                "source": "dingpago_v1",
                "source_text": result.source.get(key),
                "page": None,
                "mismatch": False,
            }
        )

    return rows


def _issues_by_key(issues: list[ValidationIssue]) -> dict[str, list[str]]:
    """issue 리스트 → {key: [kind, ...]} 인덱스.

    ValidationIssue 객체와 dict 모두 허용 (테스트 편의).
    """
    result: dict[str, list[str]] = {}
    for issue in issues:
        if isinstance(issue, dict):
            key = issue.get("key", "")
            kind = issue.get("kind", "")
        else:
            key = issue.key
            kind = issue.kind
        if key:
            result.setdefault(key, []).append(kind)
    return result


def _derive_confidence(
    key: str,
    value: Any,
    issue_codes: list[str],
    required: set[str],
) -> float:
    """필드 하나의 confidence 도출.

    규칙:
    - value not None + issues 없음  → 0.95
    - value None + key in required  → 0.0  (필수 필드 누락 = 심각)
    - value None + key not in required → 0.5  (선택 필드 비어있음 = 정상)
    - issue 있으면 CONFIDENCE_FROM_ISSUE 중 최솟값 적용 (여러 개면 min)
    """
    if value is None:
        return 0.0 if key in required else 0.5

    if not issue_codes:
        return 0.95

    # issue 중 가장 낮은 confidence 적용
    known = [CONFIDENCE_FROM_ISSUE[c] for c in issue_codes if c in CONFIDENCE_FROM_ISSUE]
    # 알 수 없는 issue 종류는 0.6 기본값
    unknown = [0.6 for c in issue_codes if c not in CONFIDENCE_FROM_ISSUE]
    all_scores = known + unknown
    return min(all_scores) if all_scores else 0.95
