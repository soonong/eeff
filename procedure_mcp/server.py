"""절차 엔진 MCP 서버.

AI가 쓰는 MCP 도구와, 사람이 쓰는 웹 화면(승인 페이지·대시보드)을 한 서버에서 제공한다.

    AI (MCP 도구)        list_procedures · start_procedure · run_next_step · wait_for_approval
                         get_run_status · list_runs · cancel_run
    사람 (웹)            /approve/<token>  승인자에게 따로 보낸 링크. 여기서만 승인/거절할 수 있다.
                         /dashboard?key=…  운영자용 전체 현황 (절차 매트릭스)

AI 쪽 도구에는 승인 기능이 없고, 승인 링크도 AI 응답에 절대 들어가지 않는다.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import parse_qs

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import render, web
from .engine import Engine, EngineError

DEMO_SITE_DIR = Path(__file__).parent / "demo_site"

INSTRUCTIONS = """\
이 서버는 검증된 절차를 정해진 순서대로, 한 단계씩 실행합니다.

1. list_procedures로 절차를 고르고 start_procedure로 시작하세요.
   돌아온 절차 카드(버전·검수자·단계·승인 주체)를 실행 전에 사용자에게 그대로 보여주세요.
2. run_next_step은 한 번에 한 단계만 실행합니다. 매 단계의 결과(근거)와 절차 매트릭스를 사용자에게 보여주세요.
3. 승인이 필요한 단계에서는 승인 요청이 승인자에게 별도 경로로 발송됩니다.
   당신(AI)은 승인할 수 없고 승인 링크도 받지 않습니다. 사용자에게 누구에게 승인 요청이 갔는지 알리고
   wait_for_approval로 결정을 기다리세요. 승인을 대신하거나 우회하려고 하지 마세요.
4. 단계가 실패하거나 거절되면 절차는 멈춥니다. 다시 시작할지는 사용자에게 물어보세요.
5. 도구가 돌려준 근거와 상태를 바꾸거나 부풀리지 말고 그대로 전달하세요.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def build_server(engine: Engine, *, admin_key: str | None = None) -> MCPServer:
    admin_key = admin_key or secrets.token_urlsafe(16)
    mcp = MCPServer(name="procedure-engine", title="절차 엔진", instructions=INSTRUCTIONS, version="0.1.0")

    # ── AI가 쓰는 도구 ──────────────────────────────────────
    @mcp.tool(annotations=READ_ONLY)
    def list_procedures() -> str:
        """실행할 수 있는 절차 목록과 각 절차의 카드(버전, 검수 정보, 단계, 승인 주체, 필요한 입력값)를 보여준다."""
        if not engine.procedures:
            return "등록된 절차가 없습니다."
        parts = []
        for proc in engine.procedures.values():
            parts.append(f"`{proc.id}`\n{render.procedure_card(proc)}")
        return "\n\n---\n\n".join(parts)

    @mcp.tool(annotations=ToolAnnotations(destructive_hint=False, open_world_hint=False))
    def start_procedure(procedure_id: str, inputs: dict[str, str] | None = None) -> str:
        """절차 실행을 새로 만든다. 아직 아무 단계도 실행하지 않는다.

        procedure_id: list_procedures에 나온 절차 id
        inputs: 절차가 요구하는 입력값 (예: {"upjong": "토목공사업"})

        돌아온 절차 카드를 실행 전에 사용자에게 보여준 뒤 run_next_step으로 진행한다.
        """
        try:
            run = engine.start(procedure_id, inputs or {})
        except EngineError as exc:
            raise ToolError(str(exc)) from None
        proc = engine.procedure(procedure_id)
        return (
            f"실행 `{run['run_id']}`을(를) 만들었습니다. 아직 실행된 단계는 없습니다.\n"
            "아래 절차 카드를 사용자에게 먼저 보여준 뒤 run_next_step으로 1단계부터 진행하세요.\n\n"
            f"{render.procedure_card(proc)}\n\n{render.matrix(run)}"
        )

    @mcp.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=False, open_world_hint=True))
    async def run_next_step(run_id: str) -> str:
        """다음 단계 하나만 실행한다. 순서를 건너뛸 수 없다.

        승인이 필요한 단계면 실행하지 않고 승인자에게 승인 요청을 보낸다.
        이 경우 사용자에게 알리고 wait_for_approval로 결정을 기다린다.
        """
        try:
            result = await engine.run_next(run_id)
        except EngineError as exc:
            raise ToolError(str(exc)) from None
        return _describe_outcome(engine, result)

    @mcp.tool(annotations=READ_ONLY)
    async def wait_for_approval(run_id: str, timeout_seconds: int = 60) -> str:
        """승인자의 결정(승인/거절)을 최대 timeout_seconds(1~120초) 동안 기다린다. AI가 승인할 수는 없다."""
        timeout = max(1, min(int(timeout_seconds), 120))
        try:
            run = await engine.wait_for_decision(run_id, timeout)
        except EngineError as exc:
            raise ToolError(str(exc)) from None
        step = next((s for s in run["steps"] if s.get("approval") and s["status"] in ("approved", "rejected", "awaiting_approval")), None)
        if run["status"] == "awaiting_approval":
            role = (step or {}).get("approval", {}).get("role", "승인자")
            head = f"⏳ {timeout}초 동안 기다렸지만 아직 {role}의 결정이 없습니다. 계속 기다리려면 wait_for_approval을 다시 호출하세요."
        elif run["status"] == "rejected":
            step = next((s for s in run["steps"] if s["status"] == "rejected"), step)
            note = (step or {}).get("approval", {}).get("note")
            head = "⛔ 승인 거절: 절차가 중단되었고 이후 단계는 실행되지 않습니다." + (f" 거절 사유: {note}" if note else "")
        elif step is not None and step["status"] == "approved":
            head = f"👍 '{step['name']}' 단계 승인 완료 ({step['approval']['role']}). run_next_step으로 실행하세요."
        else:
            head = f"현재 상태: {render.RUN_STATUS.get(run['status'], run['status'])}"
        return f"{head}\n\n{render.matrix(run)}"

    @mcp.tool(annotations=READ_ONLY)
    def get_run_status(run_id: str) -> str:
        """실행 하나의 절차 매트릭스(단계별 상태, 근거, 승인 내역)와 최근 기록을 보여준다."""
        try:
            run = engine.get_run(run_id)
        except EngineError as exc:
            raise ToolError(str(exc)) from None
        events = "\n".join(
            f"- {e['at'][11:19]} {e['type']}" + (f" [{e['step']}]" if e.get("step") else "") + (f" {e['detail']}" if e.get("detail") else "")
            for e in run["events"][-10:]
        )
        return f"{render.matrix(run)}\n\n**최근 기록**\n{events}"

    @mcp.tool(annotations=READ_ONLY)
    def list_runs(limit: int = 20) -> str:
        """최근 실행들의 전체 현황(실행 × 단계 매트릭스)을 보여준다."""
        return render.overview(engine.list_runs(max(1, min(int(limit), 100))))

    @mcp.tool(annotations=ToolAnnotations(destructive_hint=True, open_world_hint=False))
    async def cancel_run(run_id: str, reason: str = "") -> str:
        """진행 중인 실행을 취소한다. 기다리던 승인 요청도 무효가 된다."""
        try:
            run = await engine.cancel(run_id, reason)
        except EngineError as exc:
            raise ToolError(str(exc)) from None
        return f"🚫 실행을 취소했습니다.\n\n{render.matrix(run)}"

    # ── 사람이 쓰는 화면 ────────────────────────────────────
    @mcp.custom_route("/approve/{token}", methods=["GET", "POST"], include_in_schema=False)
    async def approve(request: Request) -> Response:
        token = request.path_params["token"]
        if request.method == "POST":
            form = parse_qs((await request.body()).decode("utf-8"))
            decision = (form.get("decision") or [""])[0]
            if decision not in ("approve", "reject"):
                return HTMLResponse(web.message_page("잘못된 요청", "승인 또는 거절을 선택하세요.", tone="bad"), status_code=400)
            try:
                await engine.decide(token, decision == "approve", note=(form.get("note") or [""])[0][:200])
            except EngineError as exc:
                return HTMLResponse(web.message_page("처리할 수 없습니다", str(exc), tone="bad"), status_code=400)
            _, run, step = engine.approval_view(token)
            return HTMLResponse(web.decision_page(run, step, decision == "approve"))
        try:
            record, run, step = engine.approval_view(token)
        except EngineError as exc:
            return HTMLResponse(web.message_page("승인 링크 오류", str(exc), tone="bad"), status_code=404)
        return HTMLResponse(
            web.approval_page(
                proc=engine.procedures.get(run["procedure_id"]),
                run=run,
                step=step,
                record=record,
                token=token,
                has_screen=_latest_screen(engine, run, step) is not None,
            ),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @mcp.custom_route("/approve/{token}/screen", methods=["GET"], include_in_schema=False)
    async def approve_screen(request: Request) -> Response:
        try:
            _, run, step = engine.approval_view(request.path_params["token"])
        except EngineError:
            return Response(status_code=404)
        path = _latest_screen(engine, run, step)
        return FileResponse(path) if path else Response(status_code=404)

    def _authorized(request: Request) -> bool:
        return secrets.compare_digest(request.query_params.get("key", ""), admin_key)

    @mcp.custom_route("/dashboard", methods=["GET"], include_in_schema=False)
    async def dashboard(request: Request) -> Response:
        if not _authorized(request):
            return HTMLResponse(web.message_page("접근 불가", "대시보드 키가 필요합니다."), status_code=401)
        return HTMLResponse(web.dashboard_page(engine.list_runs(100), admin_key))

    @mcp.custom_route("/dashboard/runs/{run_id}", methods=["GET"], include_in_schema=False)
    async def dashboard_run(request: Request) -> Response:
        if not _authorized(request):
            return HTMLResponse(web.message_page("접근 불가", "대시보드 키가 필요합니다."), status_code=401)
        try:
            run = engine.get_run(request.path_params["run_id"])
        except EngineError as exc:
            return HTMLResponse(web.message_page("없음", str(exc)), status_code=404)
        return HTMLResponse(web.run_detail_page(engine.procedures.get(run["procedure_id"]), run, admin_key))

    @mcp.custom_route("/dashboard/screens/{run_id}/{step_id}", methods=["GET"], include_in_schema=False)
    async def dashboard_screen(request: Request) -> Response:
        if not _authorized(request):
            return Response(status_code=401)
        try:
            run = engine.get_run(request.path_params["run_id"])
        except EngineError:
            return Response(status_code=404)
        step = next((s for s in run["steps"] if s["id"] == request.path_params["step_id"] and s.get("screenshot")), None)
        if step is None:
            return Response(status_code=404)
        return FileResponse(engine.store.screen_path(run["run_id"], step["id"]))

    @mcp.custom_route("/demo-site/", methods=["GET"], include_in_schema=False)
    async def demo_site(request: Request) -> Response:
        return FileResponse(DEMO_SITE_DIR / "index.html")

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "procedures": list(engine.procedures)})

    return mcp


def _latest_screen(engine: Engine, run: dict, step: dict) -> Path | None:
    """승인할 단계 직전까지 찍힌 마지막 화면."""
    idx = next(i for i, s in enumerate(run["steps"]) if s["id"] == step["id"])
    for s in reversed(run["steps"][:idx]):
        if s.get("screenshot"):
            path = engine.store.screen_path(run["run_id"], s["id"])
            return path if path.exists() else None
    return None


def _describe_outcome(engine: Engine, result: dict) -> str:
    run, step, outcome = result["run"], result["step"], result["outcome"]
    matrix = render.matrix(run)
    if outcome == "finished":
        return f"이미 끝난 실행입니다 (상태: {render.RUN_STATUS.get(run['status'], run['status'])}).\n\n{matrix}"

    n = next(i for i, s in enumerate(run["steps"], 1) if s["id"] == step["id"])
    if outcome == "executed":
        head = f"✅ {n}단계 '{step['name']}' 완료 — 근거: {step['evidence']}"
        nxt = next((s for s in run["steps"] if s["status"] != "done"), None)
        if run["status"] == "completed" or nxt is None:
            tail = "🎉 모든 단계를 마쳤습니다. 절차 매트릭스를 사용자에게 보여주세요."
        elif nxt.get("approver"):
            tail = (
                f"다음 단계 '{nxt['name']}': {nxt['approver']} 승인이 필요합니다. "
                "run_next_step을 호출하면 승인 요청이 발송됩니다."
            )
        else:
            tail = f"다음 단계는 '{nxt['name']}'입니다. run_next_step으로 계속하세요."
        return f"{head}\n\n{matrix}\n\n{tail}"
    if outcome == "failed":
        return (
            f"❌ {n}단계 '{step['name']}' 실패 — {step['error']}\n"
            "확인에 실패해서 절차를 멈췄습니다. 이후 단계는 실행되지 않습니다. 사용자에게 알리고 다음 행동을 물어보세요.\n\n"
            f"{matrix}"
        )
    role = step["approval"]["role"]
    if outcome == "approval_requested":
        return (
            f"✋ {n}단계 '{step['name']}': {role} 승인이 필요해서 실행하지 않았습니다.\n"
            f"승인 요청을 {role}에게 별도 경로로 보냈습니다. 이 도구는 승인 링크를 돌려주지 않으며, AI는 승인할 수 없습니다.\n"
            f"사용자에게 {role}에게 승인 요청이 갔다고 알리고 wait_for_approval로 결정을 기다리세요.\n\n{matrix}"
        )
    return (
        f"⏳ {n}단계 '{step['name']}': 아직 {role}의 결정을 기다리는 중입니다. "
        f"wait_for_approval로 기다리세요.\n\n{matrix}"
    )
