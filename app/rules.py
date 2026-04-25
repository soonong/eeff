from __future__ import annotations

import csv
from pathlib import Path

from .schemas import Rule

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "columns.csv"

REQUIRED_COLUMNS = {"key", "description", "type", "required", "validator", "few_shot"}


def load_rules(path: Path | None = None) -> list[Rule]:
    csv_path = path or DEFAULT_PATH
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"columns.csv missing required columns: {sorted(missing)}")
        rules: list[Rule] = []
        for row in reader:
            key = (row.get("key") or "").strip()
            if not key:
                continue
            rules.append(
                Rule(
                    key=key,
                    description=(row.get("description") or "").strip(),
                    type=(row.get("type") or "str").strip(),
                    required=_parse_bool(row.get("required")),
                    validator=(row.get("validator") or "").strip() or None,
                    few_shot=(row.get("few_shot") or "").strip() or None,
                )
            )
    if not rules:
        raise ValueError(f"No rules loaded from {csv_path}")
    return rules


def _parse_bool(v: str | None) -> bool:
    return (v or "").strip().lower() in {"true", "1", "yes", "y"}
