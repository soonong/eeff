from __future__ import annotations

from app.schemas import RawExtraction, Rule
from app.validator import validate

RULES = [
    Rule("종목", "qualifications", "nested_list[str]", True, "jongmok_normalize", None),
    Rule("투찰율", "낙찰하한율 0~1", "float", True, "ratio_0_1", None),
    Rule("기초금액", "원 단위 정수", "int", False, None, None),
    Rule("입찰일", "ISO datetime", "str", True, "iso_datetime", None),
    Rule("입찰방식", "enum", "str", True, "enum_bid_method", None),
    Rule("상호진출여부", "0 or 1", "int", False, "zero_or_one", None),
    Rule("협정업체수", "양의 정수", "int", False, "positive_int", None),
]


def _raw(extracted, source=None):
    return RawExtraction(extracted=extracted, source=source or {k: f"src:{k}" for k in extracted})


def test_required_missing_reports_issue():
    raw = _raw({"투찰율": 0.88})
    out = validate(raw, RULES)
    kinds = {(i.key, i.kind) for i in out.issues}
    assert ("종목", "missing") in kinds
    assert ("입찰일", "missing") in kinds
    assert ("입찰방식", "missing") in kinds


def test_percent_string_to_float():
    raw = _raw({"투찰율": "87.745%", "종목": "토목공사업", "입찰일": "2026-05-23T11:00", "입찰방식": "일반경쟁"})
    out = validate(raw, RULES)
    assert out.extracted["투찰율"] == 0.87745


def test_ratio_out_of_range():
    raw = _raw({"투찰율": 1.5, "종목": "토목공사업", "입찰일": "2026-05-23T11:00", "입찰방식": "일반경쟁"})
    out = validate(raw, RULES)
    assert any(i.key == "투찰율" and i.kind == "out_of_range" for i in out.issues)


def test_int_with_comma_and_unit():
    raw = _raw({"기초금액": "3,922,300,000원", "투찰율": 0.88, "종목": "토목공사업",
                "입찰일": "2026-05-23T11:00", "입찰방식": "일반경쟁"})
    out = validate(raw, RULES)
    assert out.extracted["기초금액"] == 3922300000


def test_jongmok_string_coerced_to_nested_list():
    raw = _raw({"종목": "지반조성·포장공사업(주력분야: 포장공사업) 및 토목공사업",
                "투찰율": 0.88, "입찰일": "2026-05-23T11:00", "입찰방식": "일반경쟁"})
    out = validate(raw, RULES)
    assert out.extracted["종목"] == [["포장공사업", "토목공사업"]]


def test_iso_datetime_bad_format():
    raw = _raw({"종목": "토목", "투찰율": 0.88, "입찰일": "내일 11시", "입찰방식": "일반경쟁"})
    out = validate(raw, RULES)
    assert any(i.key == "입찰일" and i.kind == "bad_format" for i in out.issues)


def test_enum_validation():
    raw = _raw({"종목": "토목", "투찰율": 0.88, "입찰일": "2026-05-23T11:00", "입찰방식": "특수경쟁"})
    out = validate(raw, RULES)
    assert any(i.key == "입찰방식" and i.kind == "bad_enum" for i in out.issues)


def test_zero_or_one_validator():
    raw = _raw({"종목": "토목", "투찰율": 0.88, "입찰일": "2026-05-23T11:00",
                "입찰방식": "일반경쟁", "상호진출여부": 2})
    out = validate(raw, RULES)
    assert any(i.key == "상호진출여부" and i.kind == "out_of_range" for i in out.issues)


def test_positive_int_validator():
    raw = _raw({"종목": "토목", "투찰율": 0.88, "입찰일": "2026-05-23T11:00",
                "입찰방식": "일반경쟁", "협정업체수": 0})
    out = validate(raw, RULES)
    assert any(i.key == "협정업체수" and i.kind == "out_of_range" for i in out.issues)


def test_missing_source_reports_issue():
    raw = RawExtraction(extracted={"종목": "토목", "투찰율": 0.88, "입찰일": "2026-05-23T11:00", "입찰방식": "일반경쟁"},
                        source={})
    out = validate(raw, RULES)
    assert any(i.kind == "missing_source" for i in out.issues)


def test_arith_check_construction_cost():
    rules = RULES + [
        Rule("순공사원가", "", "int", False, None, None),
        Rule("재료비", "", "int", False, None, None),
        Rule("노무비", "", "int", False, None, None),
        Rule("경비", "", "int", False, None, None),
    ]
    raw = _raw({
        "종목": "토목", "투찰율": 0.88, "입찰일": "2026-05-23T11:00", "입찰방식": "일반경쟁",
        "순공사원가": 1000, "재료비": 400, "노무비": 300, "경비": 200,
    })
    out = validate(raw, rules)
    assert any(i.key == "순공사원가" and i.kind == "arith_mismatch" for i in out.issues)
