"""tests/test_regression.py — 회귀 테스트: 가중평균 정확도 임계값 이상 유지.

VCR replay 모드 기본. cassette 없으면 skip (CI 첫 실행 보호).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_GT_DIR = _ROOT / "data" / "ground_truth"
_CASSETTES_DIR = _ROOT / "tests" / "fixtures" / "gemini_cassettes"
_DEFAULT_THRESHOLD = 0.70


def _get_threshold() -> float:
    """REGRESSION_THRESHOLD 환경변수 또는 기본값 0.70."""
    try:
        return float(os.environ.get("REGRESSION_THRESHOLD", _DEFAULT_THRESHOLD))
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
