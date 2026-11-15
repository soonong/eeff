"""AI에게 돌려줄 텍스트(마크다운): 절차 카드, 절차 매트릭스, 전체 현황."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .definitions import Procedure

STEP_STATUS = {
    "pending": "⬜ 대기",
    "awaiting_approval": "✋ 승인 대기",
    "approved": "👍 승인됨",
    "running": "⏳ 실행 중",
    "done": "✅ 완료",
    "failed": "❌ 실패",
    "rejected": "⛔ 거절",
    "cancelled": "🚫 취소",
}
STEP_ICON = {k: v.split()[0] for k, v in STEP_STATUS.items()}

RUN_STATUS = {
    "in_progress": "진행 중",
    "awaiting_approval": "승인 대기",
    "completed": "완료",
    "failed": "실패",
    "rejected": "거절됨",
    "cancelled": "취소됨",
}

VERIFICATION_LABELS = {
    "owner": "담당",
    "reviewed_by": "검수자",
    "reviewed_at": "검수일",
    "accuracy": "정확도",
    "tested": "테스트",
    "notes": "비고",
}


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ") if value not in (None, "") else ""


def approver_label(approver: str | None) -> str:
    return f"{approver} 승인" if approver else "자동"


def procedure_card(proc: Procedure) -> str:
    lines = [f"### 📋 {proc.name} (v{proc.version})"]
    if proc.description:
        lines.append(proc.description)
    lines.append("")
    lines.append(f"- 위험도: {proc.risk}")
    for key, value in proc.verification.items():
        lines.append(f"- {VERIFICATION_LABELS.get(key, key)}: {value}")
    if proc.inputs:
        lines.append("- 입력값: " + ", ".join(f"{i.label}(`{i.key}`)" + ("" if i.required else " 선택") for i in proc.inputs))
    lines.append("- 단계: " + " → ".join(f"{n}. {s.name}[{approver_label(s.approver)}]" for n, s in enumerate(proc.steps, 1)))
    return "\n".join(lines)


def matrix(run: Mapping[str, Any]) -> str:
    done = sum(1 for s in run["steps"] if s["status"] == "done")
    lines = [
        f"**절차 매트릭스** · 실행 `{run['run_id']}` · {run['procedure_name']} v{run['procedure_version']} · "
        f"상태: **{RUN_STATUS.get(run['status'], run['status'])}** ({done}/{len(run['steps'])})",
        "",
        "| # | 단계 | 승인 | 상태 | 근거 / 메모 | 시각 |",
        "|---|---|---|---|---|---|",
    ]
    for n, s in enumerate(run["steps"], 1):
        note = s.get("evidence") or s.get("error") or ""
        approval = s.get("approval") or {}
        when = s.get("finished_at") or approval.get("decided_at") or approval.get("requested_at") or ""
        if approval.get("decision"):
            decision = "승인" if approval["decision"] == "approved" else "거절"
            note = f"{approval['role']} {decision}({approval.get('via', '')})" + (f" · {note}" if note else "")
        elif s["status"] == "awaiting_approval":
            note = f"{approval.get('role')}에게 승인 요청 발송됨"
        lines.append(
            f"| {n} | {_cell(s['name'])} | {approver_label(s.get('approver'))} | {STEP_STATUS.get(s['status'], s['status'])} "
            f"| {_cell(note)} | {_cell(when[11:19] if len(when) >= 19 else when)} |"
        )
    return "\n".join(lines)


def overview(runs: Iterable[Mapping[str, Any]]) -> str:
    runs = list(runs)
    if not runs:
        return "아직 실행 기록이 없습니다."
    width = max(len(r["steps"]) for r in runs)
    head = "| 실행 | 절차 | 상태 | " + " | ".join(str(i) for i in range(1, width + 1)) + " |"
    sep = "|---|---|---|" + "---|" * width
    rows = [head, sep]
    for r in runs:
        icons = [STEP_ICON.get(s["status"], "?") for s in r["steps"]] + [""] * (width - len(r["steps"]))
        rows.append(
            f"| `{r['run_id']}` | {_cell(r['procedure_name'])} | {RUN_STATUS.get(r['status'], r['status'])} | "
            + " | ".join(icons)
            + " |"
        )
    legend = "범례: " + " ".join(STEP_STATUS.values())
    return "\n".join(rows) + "\n\n" + legend
