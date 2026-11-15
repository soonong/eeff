"""실행 기록 저장소.

- 실행(run) 하나 = JSON 파일 하나. 사람이 열어봐도 읽히는 감사 기록이다.
- 승인 토큰은 해시만 저장한다. 원문 토큰은 승인자에게 보내는 링크에만 들어가므로
  기록 파일이나 AI에게 돌려주는 응답 어디에도 남지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RunStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.runs_dir = self.data_dir / "runs"
        self.approvals_dir = self.data_dir / "approvals"
        self.screens_dir = self.data_dir / "screens"
        for d in (self.runs_dir, self.approvals_dir, self.screens_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── 실행 기록 ──────────────────────────────────────────
    def save_run(self, run: dict[str, Any]) -> None:
        _atomic_write_json(self.runs_dir / f"{run['run_id']}.json", run)

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        if not _safe_id(run_id):
            return None
        path = self.runs_dir / f"{run_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in self.runs_dir.glob("*.json")]
        return sorted(runs, key=lambda r: r["created_at"], reverse=True)

    def screen_path(self, run_id: str, step_id: str) -> Path:
        d = self.screens_dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{step_id}.png"

    # ── 승인 요청 ──────────────────────────────────────────
    def save_approval(self, token: str, record: dict[str, Any]) -> None:
        _atomic_write_json(self.approvals_dir / f"{token_hash(token)}.json", record)

    def load_approval(self, token: str) -> dict[str, Any] | None:
        path = self.approvals_dir / f"{token_hash(token)}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update_approval(self, token: str, record: dict[str, Any]) -> None:
        self.save_approval(token, record)


def _safe_id(value: str) -> bool:
    return bool(value) and value.replace("-", "").replace("_", "").isalnum()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
