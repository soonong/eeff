"""tests/test_adapter_dream.py — adapter_dream 단위 + 통합 테스트."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gemini_client, storage
from app.adapter_dream import CONFIDENCE_FROM_ISSUE, _derive_confidence, to_dream_format
from app.schemas import BidExtraction, RawExtraction, ValidationIssue


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

_REQUIRED = {"종목", "투찰마감일", "입찰일", "공사현장", "입찰방식"}


def _make_result(extracted: dict, source: dict | None = None, issues: list[ValidationIssue] | None = None) -> BidExtraction:
    return BidExtraction(
        extracted=extracted,
        source=source or {},
        issues=issues or [],
    )


# ---------------------------------------------------------------------------
# 단위 테스트 1: 모든 필드 정상 → confidence 0.95
# ---------------------------------------------------------------------------

def test_all_fields_normal_confidence():
    result = _make_result(
        extracted={
            "종목": [["포장공사업"]],
            "투찰마감일": "2026-05-22T17:00",
            "입찰방식": "일반경쟁",
        },
        source={
            "종목": "포장공사업 및 토목공사업",
            "투찰마감일": "투찰마감 2026-05-22 17:00",
            "입찰방식": "일반경쟁",
        },
        issues=[],
    )
    rows = to_dream_format(result, required_keys=_REQUIRED)

    assert len(rows) == 3
    by_key = {r["column_name"]: r for r in rows}

    assert by_key["종목"]["confidence"] == 0.95
    assert by_key["투찰마감일"]["confidence"] == 0.95
    assert by_key["입찰방식"]["confidence"] == 0.95
    # source 필드는 source_text에 담김
    assert by_key["종목"]["source"] == "dingpago_v1"
    assert by_key["종목"]["source_text"] == "포장공사업 및 토목공사업"
    assert by_key["종목"]["page"] is None
    assert by_key["종목"]["mismatch"] is False


# ---------------------------------------------------------------------------
# 단위 테스트 2: required 필드 비어있음 → confidence 0.0
# ---------------------------------------------------------------------------

def test_required_field_missing_confidence_zero():
    result = _make_result(
        extracted={
            "종목": None,          # required=True, 비어있음
            "기초금액": None,      # required=False, 비어있음
        },
        source={},
        issues=[],
    )
    rows = to_dream_format(result, required_keys=_REQUIRED)
    by_key = {r["column_name"]: r for r in rows}

    # required 필드 null → 0.0
    assert by_key["종목"]["confidence"] == 0.0
    # optional 필드 null → 0.5
    assert by_key["기초금액"]["confidence"] == 0.5


# ---------------------------------------------------------------------------
# 단위 테스트 3: issue 다수 → 가장 낮은 confidence
# ---------------------------------------------------------------------------

def test_multiple_issues_min_confidence():
    issues = [
        ValidationIssue(key="투찰율", kind="out_of_range", detail="1.5 not in [0,1]"),
        ValidationIssue(key="투찰율", kind="bad_format", detail="format error"),
    ]
    result = _make_result(
        extracted={"투찰율": 1.5},
        source={},
        issues=issues,
    )
    rows = to_dream_format(result, required_keys=_REQUIRED)
    by_key = {r["column_name"]: r for r in rows}

    # out_of_range=0.3, bad_format=0.4 → min=0.3
    assert by_key["투찰율"]["confidence"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# 단위 테스트 4: missing_source issue → confidence 0.7
# ---------------------------------------------------------------------------

def test_missing_source_issue_confidence():
    issues = [ValidationIssue(key="공사현장", kind="missing_source", detail="source not provided")]
    result = _make_result(
        extracted={"공사현장": "서울특별시 강남구"},
        source={},
        issues=issues,
    )
    rows = to_dream_format(result, required_keys=_REQUIRED)
    by_key = {r["column_name"]: r for r in rows}

    assert by_key["공사현장"]["confidence"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# 통합 테스트: ?format=dream 라우트
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")
    yield


@pytest.fixture
def stub_gemini_dream(monkeypatch):
    def fake_extract(markdown, rules):
        return RawExtraction(
            extracted={
                "종목": "지반조성·포장공사업(주력분야: 포장공사업) 및 토목공사업",
                "투찰율": "87.745%",
                "기초금액": "3,922,300,000",
                "공사현장": "서울특별시 강남구 영동대로",
                "입찰방식": "일반경쟁",
                "입찰일": "2026-05-23T11:00",
                "투찰마감일": "2026-05-22T17:00",
            },
            source={
                "종목": "4-가. 지반조성·포장공사업(주력분야: 포장공사업) 및 토목공사업",
                "투찰율": "낙찰하한율 87.745%",
                "기초금액": "기초금액 3,922,300,000원",
                "공사현장": "서울특별시 강남구 영동대로 일대",
                "입찰방식": "일반경쟁",
                "입찰일": "개찰일시 2026-05-23 11:00",
                "투찰마감일": "전자입찰 투찰 마감 2026-05-22 17:00",
            },
        )

    monkeypatch.setattr(gemini_client, "extract", fake_extract)
    monkeypatch.setattr(gemini_client, "last_usage", lambda: {})


def test_format_dream_returns_list(stub_gemini_dream):
    """?format=dream → list[dict] JSON array 반환 확인."""
    from app.main import app
    from app.routes import _load_required_keys

    # lru_cache 초기화 (다른 테스트와 격리)
    _load_required_keys.cache_clear()

    sample = (Path(__file__).resolve().parent.parent / "samples" / "sample_g2b.html").read_bytes()
    with TestClient(app) as client:
        r = client.post(
            "/analyze?format=dream",
            files={"file": ("sample_g2b.html", sample, "text/html")},
        )

    assert r.status_code == 200, r.text
    body = r.json()

    # list[dict] 형태
    assert isinstance(body, list)
    assert len(body) > 0

    # 각 행은 F02 ai_columns 스키마 키 보유
    required_schema_keys = {"column_name", "value", "confidence", "source", "source_text", "page", "mismatch"}
    for row in body:
        assert required_schema_keys.issubset(row.keys()), f"Missing keys in row: {row}"
        assert row["source"] == "dingpago_v1"
        assert isinstance(row["confidence"], float)
        assert 0.0 <= row["confidence"] <= 1.0
