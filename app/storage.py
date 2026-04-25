from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "bid_analyses.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    extracted TEXT NOT NULL,
    source TEXT NOT NULL,
    issues TEXT NOT NULL,
    usage TEXT NOT NULL
);
"""


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    conn = sqlite3.connect(target)
    conn.execute(_SCHEMA)
    return conn


def save_analysis(
    file_name: str,
    extracted: dict[str, Any],
    source: dict[str, str],
    issues: list[dict[str, Any]],
    usage: dict[str, int],
    path: Path | None = None,
) -> int:
    with _connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO analyses (file_name, created_at, extracted, source, issues, usage) VALUES (?, ?, ?, ?, ?, ?)",
            (
                file_name,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(extracted, ensure_ascii=False),
                json.dumps(source, ensure_ascii=False),
                json.dumps(issues, ensure_ascii=False),
                json.dumps(usage, ensure_ascii=False),
            ),
        )
        return int(cur.lastrowid)
