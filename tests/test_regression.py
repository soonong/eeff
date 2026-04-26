"""tests/test_regression.py — 회귀 테스트: 가중평균 정확도 임계값 이상 유지.

VCR replay 모드 기본. cassette 없으면 skip (CI 첫 실행 보호).

정확도 측정 보류 — D7 활성 예정 (사용자 정책).
  - D6 현재: MIN_ACCURACY 기본값 0.0 → 임계값 검증 비활성 (골드셋 검수 미완료).
  - D7 활성: MIN_ACCURACY=0.70 환경변수 설정 또는 하단 _RAMP 상수 참고.
  - 임계값 램프: D7=70% / 2주차=85% / 1개월=95%
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_GT_DIR = _ROOT / "data" / "ground_truth"
_CASSETTES_DIR = _ROOT / "tests" / "fixtures" / "gemini_cassettes"

# 임계값 램프 참고용 상수 (D7 단계별 활성 시 MIN_ACCURACY 환경변수로 전달)
_RAMP = {
    "D7":      0.70,
    "2주차":   0.85,
    "1개월":   0.95,
}

# 기본값 0.0 = 측정 비활성 (D6 현재 단계).
# D7 활성: MIN_ACCURACY=0.70 으로 환경변수 설정.
_DEFAULT_THRESHOLD = 0.0


def _get_threshold() -> float:
    """MIN_ACCURACY 환경변수 또는 기본값 0.0 (비활성).

    0.0 반환 시 임계값 검증을 건너뜀 (골드셋 검수 완료 전 단계).
    """
    try:
        return float(os.environ.get("MIN_ACCURACY", _DEFAULT_THRESHOLD))
    except ValueError:
        return _DEFAULT_THRESHOLD


def _has_any_cassette() -> bool:
    """평가 가능한 cassette가 하나라도 있는지 확인."""
    if not _CASSETTES_DIR.exists():
        return False
    gt_dirs = [
        d for d in _GT_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    ] if _GT_DIR.exists() else []
    for gt_dir in gt_dirs:
        cassette = _CASSETTES_DIR / f"{gt_dir.name}.json"
        if cassette.exists():
            return True
    return False


def test_min_accuracy_threshold(tmp_path: Path) -> None:
    """VCR replay 모드로 평가 → 가중평균 ≥ THRESHOLD 검증."""
    threshold = _get_threshold()

    # MIN_ACCURACY=0.0 (기본) 이면 정확도 측정 비활성 — D7 활성 예정
    if threshold == 0.0:
        pytest.skip(
            "정확도 측정 보류 — 골드셋 검수 완료 후 MIN_ACCURACY 환경변수로 활성화 예정 (D7)."
        )

    # cassette 없으면 skip (CI 첫 실행 보호)
    if not _has_any_cassette():
        pytest.skip(
            "No cassettes found in tests/fixtures/gemini_cassettes/. "
            "Run `python -m eval.run_eval --vcr record` first to generate cassettes."
        )

    from eval.run_eval import run_eval

    result = run_eval(
        dataset_dir=_GT_DIR,
        out_dir=tmp_path,
        api_base="http://localhost:8000",  # replay 모드에서는 미사용
        limit=None,
        vcr_mode="replay",
    )

    assert result, "평가 결과가 비어있습니다. ground_truth 폴더를 확인하세요."

    weighted_avg = result.get("weighted_avg", 0.0)
    assert weighted_avg >= threshold, (
        f"가중평균 정확도 {weighted_avg:.4f} < 임계값 {threshold:.2f}\n"
        f"Top10 실패 필드:\n"
        + "\n".join(
            f"  {item['field']}: {item['accuracy']:.4f}"
            for item in result.get("top10_failures", [])
        )
    )
