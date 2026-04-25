from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gemini_client, storage
from app.main import app
from app.schemas import RawExtraction


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")
    yield


@pytest.fixture
def stub_gemini(monkeypatch):
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
    monkeypatch.setattr(gemini_client, "last_usage", lambda: {"prompt_token_count": 100, "cached_content_token_count": 4231})


def test_index_renders():
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "AI 입찰 공고 분석 시스템" in r.text


def test_analyze_html_happy_path(stub_gemini):
    sample = (Path(__file__).resolve().parent.parent / "samples" / "sample_g2b.html").read_bytes()
    with TestClient(app) as client:
        r = client.post("/analyze", files={"file": ("sample_g2b.html", sample, "text/html")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_name"] == "sample_g2b.html"
    assert body["extracted"]["종목"] == [["포장공사업", "토목공사업"]]
    assert body["extracted"]["투찰율"] == 0.87745
    assert body["extracted"]["기초금액"] == 3922300000
    assert body["usage"]["cached_content_token_count"] == 4231


def test_analyze_rejects_unsupported_type(stub_gemini):
    with TestClient(app) as client:
        r = client.post("/analyze", files={"file": ("a.docx", b"junk", "application/octet-stream")})
    assert r.status_code == 415


def test_analyze_rejects_oversized(stub_gemini):
    big = b"x" * (10 * 1024 * 1024 + 1)
    with TestClient(app) as client:
        r = client.post("/analyze", files={"file": ("big.html", big, "text/html")})
    assert r.status_code == 413


def test_analyze_requires_file_or_url():
    with TestClient(app) as client:
        r = client.post("/analyze", data={})
    assert r.status_code == 400
