"""승인 알림 테스트: SOLAPI 클라이언트, 카카오 알림톡 알리미, 발송 실패 시 엔진 동작."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from procedure_mcp.engine import Engine, EngineError
from procedure_mcp.notifier import (
    ApprovalRequest,
    ConsoleNotifier,
    KakaoAlimtalkNotifier,
    NotifyError,
    parse_recipients,
)
from procedure_mcp.solapi import SolapiClient, SolapiError
from procedure_mcp.store import RunStore
from tests.test_procedure_engine import PROC

REQUEST = ApprovalRequest(
    role="회원",
    procedure_name="회원정보 변경 - 등록 업종 추가",
    step_name="업종 추가 입력",
    run_id="20261115-abc123",
    link="https://approve.example.com/approve/TOKEN123",
    expires_at="2026-11-16T11:50:00+09:00",
)


# ── SOLAPI 클라이언트 (가짜 서버로 요청 형식 확인) ──────────────
class FakeSolapi:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body or {"groupInfo": {"groupId": "G1", "count": {"total": 1, "registeredSuccess": 1}}}
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                fake.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "body": json.loads(self.rfile.read(length))}
                )
                payload = json.dumps(fake.body).encode()
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()


def test_solapi_request_format_and_signature():
    fake = FakeSolapi()
    try:
        client = SolapiClient("KEY", "SECRET", base_url=fake.url)
        result = client.send_many([{"to": "01012345678", "type": "ATA"}])
    finally:
        fake.close()
    assert result["groupInfo"]["groupId"] == "G1"
    req = fake.requests[0]
    assert req["path"] == "/messages/v4/send-many/detail"
    assert req["body"] == {"messages": [{"to": "01012345678", "type": "ATA"}], "allowDuplicates": False}
    m = re.fullmatch(
        r"HMAC-SHA256 ApiKey=KEY, Date=(?P<date>\S+), salt=(?P<salt>[0-9a-f]+), signature=(?P<sig>[0-9a-f]{64})",
        req["headers"]["Authorization"],
    )
    assert m, req["headers"]["Authorization"]
    expected = hmac.new(b"SECRET", (m["date"] + m["salt"]).encode(), hashlib.sha256).hexdigest()
    assert m["sig"] == expected


def test_solapi_error_is_reported():
    fake = FakeSolapi(status=400, body={"errorCode": "InvalidApiKey", "errorMessage": "유효하지 않은 API 키"})
    try:
        with pytest.raises(SolapiError, match="InvalidApiKey"):
            SolapiClient("KEY", "SECRET", base_url=fake.url).send_many([{"to": "010"}])
    finally:
        fake.close()


def test_solapi_connection_error():
    with pytest.raises(SolapiError, match="연결 실패"):
        SolapiClient("KEY", "SECRET", base_url="http://127.0.0.1:9", timeout=2).send_many([{"to": "010"}])


# ── 카카오 알림톡 알리미 ────────────────────────────────────
class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response or {"groupInfo": {"count": {"total": 1, "registeredSuccess": 1}}}
        self.error = error
        self.sent: list[list[dict]] = []

    def send_many(self, messages):
        self.sent.append(messages)
        if self.error:
            raise self.error
        return self.response


def make_kakao(client, **kw):
    return KakaoAlimtalkNotifier(
        client,
        pf_id="KA01PF",
        template_id="KA01TP",
        recipients={"회원": ["01012345678"], "관리자": ["01011112222", "01033334444"]},
        **kw,
    )


def test_kakao_message_payload():
    client = FakeClient()
    note = make_kakao(client, sender_number="02-123-4567").send_approval_request(REQUEST)
    [message] = client.sent[0]
    assert message["to"] == "01012345678"
    assert message["type"] == "ATA"
    assert message["from"] == "021234567"
    options = message["kakaoOptions"]
    assert options["pfId"] == "KA01PF" and options["templateId"] == "KA01TP"
    assert options["disableSms"] is False
    assert options["variables"] == {
        "#{승인주체}": "회원",
        "#{절차명}": "회원정보 변경 - 등록 업종 추가",
        "#{단계명}": "업종 추가 입력",
        "#{실행번호}": "20261115-abc123",
        "#{승인기한}": "11/16 11:50",
        "#{승인토큰}": "TOKEN123",
    }
    assert "TOKEN123" not in note and "1/1건 접수" in note


def test_kakao_without_sender_disables_sms_and_sends_to_every_admin():
    client = FakeClient({"groupInfo": {"count": {"total": 2, "registeredSuccess": 2}}})
    admin = ApprovalRequest(**{**REQUEST.__dict__, "role": "관리자"})
    make_kakao(client).send_approval_request(admin)
    messages = client.sent[0]
    assert [m["to"] for m in messages] == ["01011112222", "01033334444"]
    assert all("from" not in m and m["kakaoOptions"]["disableSms"] is True for m in messages)


def test_kakao_failures_raise_notify_error():
    with pytest.raises(NotifyError, match="휴대폰 번호"):
        make_kakao(FakeClient()).send_approval_request(ApprovalRequest(**{**REQUEST.__dict__, "role": "검수자"}))
    rejected = FakeClient(
        {
            "groupInfo": {"count": {"total": 1, "registeredSuccess": 0, "registeredFailed": 1}},
            "failedMessageList": [{"statusMessage": "템플릿 내용이 일치하지 않음"}],
        }
    )
    with pytest.raises(NotifyError, match="템플릿 내용이 일치하지 않음"):
        make_kakao(rejected).send_approval_request(REQUEST)
    with pytest.raises(NotifyError, match="연결 실패"):
        make_kakao(FakeClient(error=SolapiError("SOLAPI 연결 실패: timeout"))).send_approval_request(REQUEST)


def test_kakao_log_never_contains_link(tmp_path):
    log = ConsoleNotifier(tmp_path / "outbox.log", include_link=False)
    make_kakao(FakeClient(), log=log).send_approval_request(REQUEST)
    text = (tmp_path / "outbox.log").read_text(encoding="utf-8")
    assert "알림톡 발송" in text and "TOKEN123" not in text


def test_parse_recipients():
    assert parse_recipients("회원=010-1234-5678; 관리자=01011112222,01033334444") == {
        "회원": ["01012345678"],
        "관리자": ["01011112222", "01033334444"],
    }
    with pytest.raises(ValueError):
        parse_recipients("회원 01012345678")
    with pytest.raises(ValueError):
        parse_recipients("회원=abc")


# ── 발송 실패 시 엔진 ───────────────────────────────────────
class FlakyNotifier:
    """첫 번째 발송은 실패(오류 문구에 토큰을 섞어서), 그다음부터 성공."""

    def __init__(self):
        self.requests: list[ApprovalRequest] = []

    def send_approval_request(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise NotifyError(f"알림톡 발송 실패: 링크 {request.link} 처리 오류")
        return "회원에게 알림톡 발송 (1/1건 접수)"


def test_engine_reports_notify_failure_and_retries(tmp_path):
    async def act(ctx):
        return "ok"

    notifier = FlakyNotifier()
    engine = Engine(
        {"sample": PROC}, {"t.a": act, "t.b": act, "t.c": act}, RunStore(tmp_path), notifier, base_url="https://x"
    )
    rid = engine.start("sample", {"value": "v"})["run_id"]

    async def scenario():
        await engine.run_next(rid)
        failed = await engine.run_next(rid)
        retried = await engine.run_next(rid)
        return failed, retried

    failed, retried = asyncio.run(scenario())
    first, second = notifier.requests
    assert failed["outcome"] == "notify_failed"
    assert first.token not in failed["error"] and "(승인 링크)" in failed["error"]
    assert engine.get_run(rid)["events"][-2]["type"] == "approval_notify_failed"
    assert retried["outcome"] == "approval_requested"
    assert retried["step"]["error"] is None
    assert "알림톡 발송" in engine.get_run(rid)["events"][-1]["detail"]

    with pytest.raises(EngineError):  # 실패한 발송의 링크는 무효
        asyncio.run(engine.decide(first.token, approve=True))
    asyncio.run(engine.decide(second.token, approve=True))
    assert engine.get_run(rid)["steps"][1]["status"] == "approved"


def test_notify_failure_message_for_ai(tmp_path):
    pytest.importorskip("mcp")
    from procedure_mcp.server import build_server

    async def act(ctx):
        return "ok"

    engine = Engine(
        {"sample": PROC}, {"t.a": act, "t.b": act, "t.c": act}, RunStore(tmp_path), FlakyNotifier(), base_url="https://x"
    )
    server = build_server(engine, admin_key="k")
    rid = engine.start("sample", {"value": "v"})["run_id"]

    async def scenario():
        await server.call_tool("run_next_step", {"run_id": rid})
        result = await server.call_tool("run_next_step", {"run_id": rid})
        return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")

    text = asyncio.run(scenario())
    assert "승인 요청을 보내지 못했습니다" in text
    assert "/approve/" not in text
