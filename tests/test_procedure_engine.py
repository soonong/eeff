"""절차 엔진 테스트.

- 엔진 규칙(순서 강제, 승인 게이트, 거절·실패 시 중단, 기록)은 브라우저 없이 가짜 action으로 검사한다.
- MCP 도구와 승인 페이지는 mcp 패키지가 있을 때만 검사한다.
- 실제 브라우저로 데모 절차 전체를 돌리는 테스트는 Playwright와 Chromium이 있을 때만 돈다.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from procedure_mcp.definitions import DefinitionError, check_actions, load_procedures, parse_procedure
from procedure_mcp.engine import Engine, EngineError
from procedure_mcp.store import RunStore

PROC = parse_procedure(
    {
        "id": "sample",
        "name": "샘플 절차",
        "version": "1.0",
        "verification": {"reviewed_by": "테스트"},
        "inputs": {"value": {"label": "값"}},
        "steps": [
            {"id": "a", "name": "준비", "action": "t.a"},
            {"id": "b", "name": "변경", "action": "t.b", "approver": "회원"},
            {"id": "c", "name": "확인", "action": "t.c"},
        ],
    }
)


class FakeNotifier:
    def __init__(self):
        self.sent: list[dict] = []

    def send_approval_request(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last_token(self) -> str:
        return self.sent[-1]["link"].rsplit("/", 1)[-1]


def make_engine(tmp_path: Path, *, fail_on: str | None = None):
    calls: list[str] = []

    def make(step_id):
        async def act(ctx):
            calls.append(step_id)
            if step_id == fail_on:
                raise AssertionError("화면 확인 실패")
            ctx.state[step_id] = True
            return f"{step_id} 완료 ({ctx.inputs['value']})"

        return act

    notifier = FakeNotifier()
    engine = Engine(
        {"sample": PROC},
        {"t.a": make("a"), "t.b": make("b"), "t.c": make("c")},
        RunStore(tmp_path),
        notifier,
        base_url="http://test",
    )
    return engine, notifier, calls


def run(coro):
    return asyncio.run(coro)


# ── 정의 파일 ─────────────────────────────────────────────
def test_repo_procedures_load_and_reference_existing_actions():
    pytest.importorskip("playwright")
    from procedure_mcp.actions import ACTIONS

    procedures = load_procedures()
    assert "member_upjong_add" in procedures
    check_actions(procedures, ACTIONS)
    approvers = [s.approver for s in procedures["member_upjong_add"].steps]
    assert approvers == [None, None, None, "회원", "관리자", None]


def test_definition_errors():
    with pytest.raises(DefinitionError):
        parse_procedure({"id": "x", "name": "x", "version": "1", "steps": []})
    with pytest.raises(DefinitionError):
        parse_procedure(
            {"id": "x", "name": "x", "version": "1", "steps": [{"id": "a", "name": "a", "action": "f"}] * 2}
        )
    with pytest.raises(DefinitionError):
        check_actions({"sample": PROC}, {"t.a": object()})


# ── 엔진 규칙 ─────────────────────────────────────────────
def test_start_validates_inputs(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    with pytest.raises(EngineError, match="필수 입력값"):
        engine.start("sample", {})
    with pytest.raises(EngineError, match="없는 입력값"):
        engine.start("sample", {"value": "1", "other": "2"})
    with pytest.raises(EngineError, match="절차가 없습니다"):
        engine.start("nope", {})


def test_steps_run_in_order_and_approval_gate_blocks(tmp_path):
    engine, notifier, calls = make_engine(tmp_path)
    r = engine.start("sample", {"value": "토목공사업"})
    rid = r["run_id"]
    assert [s["status"] for s in r["steps"]] == ["pending"] * 3

    assert run(engine.run_next(rid))["outcome"] == "executed"
    assert calls == ["a"]

    out = run(engine.run_next(rid))
    assert out["outcome"] == "approval_requested"
    assert out["run"]["status"] == "awaiting_approval"
    assert len(notifier.sent) == 1 and notifier.sent[0]["role"] == "회원"

    # AI가 재촉해도 승인 전에는 실행되지 않고, 요청도 중복 발송되지 않는다
    for _ in range(3):
        assert run(engine.run_next(rid))["outcome"] == "awaiting_approval"
    assert calls == ["a"]
    assert len(notifier.sent) == 1

    run(engine.decide(notifier.last_token, approve=True))
    assert run(engine.run_next(rid))["outcome"] == "executed"
    final = run(engine.run_next(rid))
    assert final["outcome"] == "executed"
    assert calls == ["a", "b", "c"]
    assert final["run"]["status"] == "completed"
    assert final["run"]["state"] == {"a": True, "b": True, "c": True}
    approval = final["run"]["steps"][1]["approval"]
    assert approval["decision"] == "approved" and approval["via"] == "승인 링크"

    # 이미 끝난 실행은 다시 돌지 않는다
    assert run(engine.run_next(rid))["outcome"] == "finished"


def test_token_never_stored_in_run_record(tmp_path):
    engine, notifier, _ = make_engine(tmp_path)
    rid = engine.start("sample", {"value": "v"})["run_id"]
    run(engine.run_next(rid))
    run(engine.run_next(rid))
    token = notifier.last_token
    for path in tmp_path.rglob("*.json"):
        assert token not in path.read_text(encoding="utf-8")
    assert token not in json.dumps(engine.get_run(rid), ensure_ascii=False)


def test_reject_stops_procedure(tmp_path):
    engine, notifier, calls = make_engine(tmp_path)
    rid = engine.start("sample", {"value": "v"})["run_id"]
    run(engine.run_next(rid))
    run(engine.run_next(rid))
    r = run(engine.decide(notifier.last_token, approve=False, note="업종명이 틀림"))
    assert r["status"] == "rejected"
    assert run(engine.run_next(rid))["outcome"] == "finished"
    assert calls == ["a"]
    with pytest.raises(EngineError, match="이미 처리된"):
        run(engine.decide(notifier.last_token, approve=True))


def test_invalid_token_and_cancel(tmp_path):
    engine, notifier, calls = make_engine(tmp_path)
    with pytest.raises(EngineError):
        run(engine.decide("not-a-real-token", approve=True))

    rid = engine.start("sample", {"value": "v"})["run_id"]
    run(engine.run_next(rid))
    run(engine.run_next(rid))
    run(engine.cancel(rid, "사용자 요청"))
    with pytest.raises(EngineError):
        run(engine.decide(notifier.last_token, approve=True))
    assert engine.get_run(rid)["status"] == "cancelled"
    assert calls == ["a"]


def test_failed_check_stops_procedure(tmp_path):
    engine, _, calls = make_engine(tmp_path, fail_on="a")
    rid = engine.start("sample", {"value": "v"})["run_id"]
    out = run(engine.run_next(rid))
    assert out["outcome"] == "failed"
    assert out["run"]["status"] == "failed"
    assert "화면 확인 실패" in out["step"]["error"]
    assert run(engine.run_next(rid))["outcome"] == "finished"
    assert calls == ["a"]


def test_wait_for_decision_wakes_up_on_approval(tmp_path):
    engine, notifier, _ = make_engine(tmp_path)
    rid = engine.start("sample", {"value": "v"})["run_id"]

    async def scenario():
        await engine.run_next(rid)
        await engine.run_next(rid)

        async def approver():
            await asyncio.sleep(0.05)
            await engine.decide(notifier.last_token, approve=True)

        waited, _ = await asyncio.gather(engine.wait_for_decision(rid, timeout=5), approver())
        return waited

    assert run(scenario())["steps"][1]["status"] == "approved"


# ── MCP 도구와 승인 페이지 ─────────────────────────────────
def _text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def test_mcp_tools_have_no_approval_and_never_leak_token(tmp_path):
    pytest.importorskip("mcp")
    from procedure_mcp.server import build_server

    engine, notifier, _ = make_engine(tmp_path)
    server = build_server(engine, admin_key="k")

    async def scenario():
        names = {t.name for t in await server.list_tools()}
        assert names == {
            "list_procedures", "start_procedure", "run_next_step", "wait_for_approval",
            "get_run_status", "list_runs", "cancel_run",
        }
        outputs = [_text(await server.call_tool("list_procedures", {}))]
        started = _text(await server.call_tool("start_procedure", {"procedure_id": "sample", "inputs": {"value": "v"}}))
        outputs.append(started)
        rid = engine.list_runs()[0]["run_id"]
        for _ in range(2):
            outputs.append(_text(await server.call_tool("run_next_step", {"run_id": rid})))
        outputs.append(_text(await server.call_tool("wait_for_approval", {"run_id": rid, "timeout_seconds": 1})))
        outputs.append(_text(await server.call_tool("get_run_status", {"run_id": rid})))
        outputs.append(_text(await server.call_tool("list_runs", {})))
        return outputs

    outputs = run(scenario())
    token = notifier.last_token
    assert all(token not in o for o in outputs)
    assert "📋 샘플 절차" in outputs[1]
    assert "승인 요청을 회원에게" in outputs[3]
    assert "아직 회원의 결정이 없습니다" in outputs[4]


def test_approval_page_flow(tmp_path):
    pytest.importorskip("mcp")
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from procedure_mcp.server import build_server

    engine, notifier, calls = make_engine(tmp_path)
    rid = engine.start("sample", {"value": "토목공사업"})["run_id"]
    run(engine.run_next(rid))
    run(engine.run_next(rid))

    client = TestClient(build_server(engine, admin_key="k").streamable_http_app())
    page = client.get(f"/approve/{notifier.last_token}")
    assert page.status_code == 200 and "변경" in page.text and "토목공사업" in page.text

    assert client.get("/dashboard").status_code == 401
    assert rid in client.get("/dashboard?key=k").text

    done = client.post(f"/approve/{notifier.last_token}", data={"decision": "approve"})
    assert done.status_code == 200 and "승인했습니다" in done.text
    assert engine.get_run(rid)["steps"][1]["status"] == "approved"
    assert client.post(f"/approve/{notifier.last_token}", data={"decision": "approve"}).status_code == 400
    assert client.get("/approve/wrong-token").status_code == 404


# ── 실제 브라우저로 데모 절차 전체 실행 ─────────────────────
def _chromium_path() -> str | None:
    explicit = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if explicit:
        return explicit
    return "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None


def test_demo_procedure_end_to_end_with_browser(tmp_path):
    pytest.importorskip("playwright")
    from procedure_mcp.actions import ACTIONS
    from procedure_mcp.browser import BrowserManager
    from procedure_mcp.server import DEMO_SITE_DIR

    html = (DEMO_SITE_DIR / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

    notifier = FakeNotifier()
    browser = BrowserManager(executable_path=_chromium_path())
    engine = Engine(load_procedures(), ACTIONS, RunStore(tmp_path), notifier, base_url=base_url, browser=browser)

    async def scenario():
        try:
            rid = engine.start("member_upjong_add", {"upjong": "토목공사업"})["run_id"]
            outcomes = []
            for _ in range(20):
                out = await engine.run_next(rid)
                outcomes.append(out["outcome"])
                if out["outcome"] == "approval_requested":
                    await engine.decide(notifier.last_token, approve=True)
                if out["run"]["status"] in ("completed", "failed"):
                    return out["run"], outcomes
            raise AssertionError(outcomes)
        finally:
            await browser.close()

    try:
        final, outcomes = run(scenario())
    except Exception as exc:  # 브라우저를 띄울 수 없는 환경
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip("Chromium이 설치돼 있지 않음")
        raise
    finally:
        httpd.shutdown()

    assert final["status"] == "completed", final
    assert outcomes.count("approval_requested") == 2
    assert [n["role"] for n in notifier.sent] == ["회원", "관리자"]
    assert "포장공사업 → 포장공사업, 토목공사업" in final["steps"][-1]["evidence"]
    assert all(s["screenshot"] for s in final["steps"])
