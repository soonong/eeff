from __future__ import annotations

from app.jongmok_parser import normalize_jongmok


def test_single_qualification():
    assert normalize_jongmok("토목공사업") == [["토목공사업"]]


def test_juryeok_extraction():
    assert normalize_jongmok("지반조성·포장공사업(주력분야: 포장공사업)") == [["포장공사업"]]


def test_law_paren_removed():
    assert normalize_jongmok("토목공사업(건설산업기본법 제9조에 따라 등록한 자)") == [["토목공사업"]]


def test_and_condition():
    assert normalize_jongmok("토목공사업 및 조경식재공사업") == [["토목공사업", "조경식재공사업"]]


def test_or_condition():
    assert normalize_jongmok("토목공사업 또는 조경식재공사업") == [["토목공사업"], ["조경식재공사업"]]


def test_mixed_and_or():
    assert normalize_jongmok("A 및 B 또는 C") == [["A", "B"], ["C"]]


def test_juryeok_inside_and():
    result = normalize_jongmok("조경식재·시설물공사업(주력분야: 조경식재) 및 토공사업")
    assert result == [["조경식재", "토공사업"]]


def test_already_nested_list_passes_through():
    assert normalize_jongmok([["A", "B"], ["C"]]) == [["A", "B"], ["C"]]


def test_flat_list_treated_as_and():
    assert normalize_jongmok(["A", "B"]) == [["A", "B"]]


def test_trailing_phrase_removed():
    assert normalize_jongmok("토목공사업을 보유한 자") == [["토목공사업"]]


def test_or_inside_paren_not_split():
    result = normalize_jongmok("A공사업(주력분야: 포장 또는 토공) 및 B공사업")
    assert result == [["포장 또는 토공", "B공사업"]]


def test_empty_input():
    assert normalize_jongmok("") == []
    assert normalize_jongmok(None) == []
    assert normalize_jongmok([]) == []
