"""사람이 보는 화면 (HTML): 승인 페이지, 운영 대시보드.

승인 페이지는 승인자가 휴대폰으로 여는 경우가 많으므로 좁은 화면 기준으로 만든다.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape
from typing import Any
from urllib.parse import quote

from .definitions import Procedure
from .render import RUN_STATUS, STEP_ICON, STEP_STATUS, VERIFICATION_LABELS

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2129;--muted:#5f6670;--line:#e3e6ea;--accent:#2b59c3;--ok:#1a7f37;--bad:#c62828;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1f2226;--text:#e7e9ec;--muted:#a3a9b1;--line:#30343a;--accent:#7aa2ff;--ok:#4cc26a;--bad:#ff6b6b;--warn:#e3b341}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:20px;margin:4px 0 16px}h2{font-size:16px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.muted{color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.focus{border:2px solid var(--accent)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:0}dt{color:var(--muted)}dd{margin:0}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:7px 6px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}
td.nw{white-space:nowrap}
img.shot{width:100%;border:1px solid var(--line);border-radius:8px}
form .row{display:flex;gap:10px;margin-top:12px}
input[type=text]{width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font-size:15px}
button{flex:1;padding:12px;font-size:16px;font-weight:600;border:0;border-radius:10px;cursor:pointer;color:#fff}
.approve{background:var(--ok)}.reject{background:var(--bad)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
a{color:var(--accent)}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _card_html(proc: Procedure | None, run: Mapping[str, Any]) -> str:
    rows = [f"<dt>절차</dt><dd>{escape(run['procedure_name'])} <span class='badge'>v{escape(run['procedure_version'])}</span></dd>"]
    if proc is not None:
        rows.append(f"<dt>위험도</dt><dd>{escape(proc.risk)}</dd>")
        for k, v in proc.verification.items():
            rows.append(f"<dt>{escape(VERIFICATION_LABELS.get(k, k))}</dt><dd>{escape(v)}</dd>")
        labels = {i.key: i.label for i in proc.inputs}
    else:
        labels = {}
    for k, v in run["inputs"].items():
        rows.append(f"<dt>{escape(labels.get(k, k))}</dt><dd><b>{escape(v)}</b></dd>")
    rows.append(f"<dt>실행</dt><dd><code>{escape(run['run_id'])}</code> · {escape(run['created_at'][:16].replace('T', ' '))}</dd>")
    return f"<div class='card'><h2>📋 절차 카드</h2><dl>{''.join(rows)}</dl></div>"


def _matrix_html(run: Mapping[str, Any], *, highlight: str | None = None, screen_url: Any = None) -> str:
    rows = []
    for n, s in enumerate(run["steps"], 1):
        approval = s.get("approval") or {}
        note = s.get("evidence") or s.get("error") or ""
        if approval.get("decision"):
            note = f"{approval['role']} {'승인' if approval['decision'] == 'approved' else '거절'}" + (f" · {note}" if note else "")
        elif s["status"] == "awaiting_approval":
            note = f"{approval.get('role')} 승인 대기"
        shot = ""
        if screen_url and s.get("screenshot"):
            shot = f" <a href='{escape(screen_url(s['id']))}'>화면</a>"
        style = " style='background:color-mix(in srgb,var(--accent) 12%,transparent)'" if s["id"] == highlight else ""
        rows.append(
            f"<tr{style}><td>{n}</td><td>{escape(s['name'])}</td><td class='nw'>{escape(s.get('approver') or '자동')}</td>"
            f"<td class='nw'>{escape(STEP_STATUS.get(s['status'], s['status']))}</td><td>{escape(note)}{shot}</td></tr>"
        )
    status = RUN_STATUS.get(run["status"], run["status"])
    return (
        f"<div class='card'><h2>절차 매트릭스 <span class='badge'>{escape(status)}</span></h2><div class='table-wrap'><table>"
        "<tr><th>#</th><th>단계</th><th>승인</th><th>상태</th><th>근거 / 메모</th></tr>"
        + "".join(rows)
        + "</table></div></div>"
    )


def approval_page(
    *, proc: Procedure | None, run: Mapping[str, Any], step: Mapping[str, Any], record: Mapping[str, Any], token: str,
    has_screen: bool,
) -> str:
    n = next(i for i, s in enumerate(run["steps"], 1) if s["id"] == step["id"])
    pending = not record.get("decided_at") and step["status"] == "awaiting_approval" and run["status"] == "awaiting_approval"
    action = f"/approve/{quote(token)}"
    shot = (
        f"<div class='card'><h2>직전 단계 화면</h2><img class='shot' src='{escape(action)}/screen' alt='직전 단계 화면'></div>"
        if has_screen
        else ""
    )
    if pending:
        decide = f"""
<div class='card focus'>
  <h2>✋ {n}단계 · {escape(step['name'])}</h2>
  <p>이 단계를 실행하려면 <b>{escape(record['role'])}</b>의 승인이 필요합니다. 승인하면 AI가 이 단계를 실행할 수 있게 됩니다.</p>
  <p class='muted'>승인 기한: {escape(record['expires_at'][:16].replace('T', ' '))}</p>
  <form method='post' action='{escape(action)}'>
    <input type='text' name='note' placeholder='메모 (선택)' maxlength='200'>
    <div class='row'>
      <button class='reject' name='decision' value='reject'>거절</button>
      <button class='approve' name='decision' value='approve'>승인</button>
    </div>
  </form>
</div>"""
    else:
        decided = record.get("decision")
        label = {"approved": "<span class='ok'>승인됨</span>", "rejected": "<span class='bad'>거절됨</span>"}.get(
            decided or "", "<span class='warn'>더 이상 승인을 기다리지 않음</span>"
        )
        decide = f"<div class='card'><h2>{n}단계 · {escape(step['name'])}</h2><p>{label}</p></div>"
    body = (
        f"<h1>승인 요청 <span class='badge'>{escape(record['role'])}</span></h1>"
        + decide
        + _card_html(proc, run)
        + shot
        + _matrix_html(run, highlight=step["id"])
    )
    return _page("승인 요청", body)


def message_page(title: str, message: str, *, tone: str = "") -> str:
    return _page(title, f"<h1>{escape(title)}</h1><div class='card'><p class='{tone}'>{escape(message)}</p></div>")


def decision_page(run: Mapping[str, Any], step: Mapping[str, Any], approved: bool) -> str:
    head = "👍 승인했습니다" if approved else "⛔ 거절했습니다"
    detail = (
        "AI가 다음 단계를 실행할 수 있습니다. 이 창은 닫아도 됩니다."
        if approved
        else "절차가 중단되었습니다. 이후 단계는 실행되지 않습니다."
    )
    body = f"<h1>{head}</h1><div class='card'><p><b>{escape(step['name'])}</b> · {escape(detail)}</p></div>" + _matrix_html(
        run, highlight=step["id"]
    )
    return _page(head, body)


def dashboard_page(runs: Iterable[Mapping[str, Any]], key: str) -> str:
    runs = list(runs)
    if not runs:
        return _page("절차 현황", "<h1>절차 현황</h1><div class='card'><p class='muted'>아직 실행 기록이 없습니다.</p></div>")
    width = max(len(r["steps"]) for r in runs)
    head = "<tr><th>실행</th><th>절차</th><th>상태</th>" + "".join(f"<th>{i}</th>" for i in range(1, width + 1)) + "</tr>"
    rows = []
    for r in runs:
        cells = "".join(
            f"<td title='{escape(s['name'])}: {escape(STEP_STATUS.get(s['status'], ''))}'>{STEP_ICON.get(s['status'], '?')}</td>"
            for s in r["steps"]
        ) + "<td></td>" * (width - len(r["steps"]))
        link = f"/dashboard/runs/{quote(r['run_id'])}?key={quote(key)}"
        rows.append(
            f"<tr><td><a href='{escape(link)}'><code>{escape(r['run_id'])}</code></a></td><td>{escape(r['procedure_name'])}</td>"
            f"<td>{escape(RUN_STATUS.get(r['status'], r['status']))}</td>{cells}</tr>"
        )
    legend = " ".join(escape(v) for v in STEP_STATUS.values())
    body = (
        "<h1>절차 현황</h1><div class='card'><div class='table-wrap'><table>"
        + head
        + "".join(rows)
        + f"</table></div><p class='muted'>{legend}</p></div>"
    )
    return _page("절차 현황", body)


EVENT_LABELS = {
    "started": "시작",
    "step_done": "단계 완료",
    "step_failed": "단계 실패",
    "approval_requested": "승인 요청",
    "approval_notify_failed": "승인 요청 발송 실패",
    "approved": "승인",
    "rejected": "거절",
    "cancelled": "취소",
    "completed": "절차 완료",
    "interrupted": "중단",
}


def run_detail_page(proc: Procedure | None, run: Mapping[str, Any], key: str) -> str:
    def screen_url(step_id: str) -> str:
        return f"/dashboard/screens/{quote(run['run_id'])}/{quote(step_id)}?key={quote(key)}"

    step_names = {s["id"]: s["name"] for s in run["steps"]}
    events = "".join(
        f"<tr><td class='nw'>{escape(e['at'][11:19])}</td><td class='nw'>{escape(EVENT_LABELS.get(e['type'], e['type']))}</td>"
        f"<td class='nw'>{escape(step_names.get(e.get('step') or '', ''))}</td><td>{escape(e.get('detail') or '')}</td></tr>"
        for e in run["events"]
    )
    body = (
        f"<p><a href='/dashboard?key={escape(quote(key))}'>← 전체 현황</a></p><h1>실행 {escape(run['run_id'])}</h1>"
        + _card_html(proc, run)
        + _matrix_html(run, screen_url=screen_url)
        + "<div class='card'><h2>실행 기록</h2><div class='table-wrap'><table><tr><th>시각</th><th>이벤트</th><th>단계</th><th>내용</th></tr>"
        + events
        + "</table></div></div>"
    )
    return _page(f"실행 {run['run_id']}", body)
