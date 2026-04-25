from __future__ import annotations

from pathlib import Path

import pytest

from app.rules import load_rules


def test_load_rules_default_loads_all_seed_columns():
    rules = load_rules()
    assert len(rules) >= 45
    keys = {r.key for r in rules}
    for required in ("종목", "투찰율", "기초금액발표전", "공고상태", "ai컬럼", "참가자격"):
        assert required in keys


def test_load_rules_marks_required_fields():
    rules = {r.key: r for r in load_rules()}
    assert rules["종목"].required is True
    assert rules["투찰율"].required is True
    assert rules["발주처코드"].required is False


def test_load_rules_missing_columns(tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("name,desc\nX,Y\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        load_rules(bad)


def test_load_rules_empty_file(tmp_path: Path):
    empty = tmp_path / "empty.csv"
    empty.write_text("key,description,type,required,validator,few_shot\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No rules loaded"):
        load_rules(empty)
