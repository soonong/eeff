"""절차 엔진 MCP 서버 실행.

    python -m procedure_mcp

설정은 환경변수 또는 저장소 루트의 .env 파일로 한다. 전체 목록은 procedure_mcp/README.md 참고.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

from .actions import ACTIONS
from .browser import BrowserManager
from .definitions import check_actions, load_procedures
from .engine import Engine
from .notifier import ConsoleNotifier, KakaoAlimtalkNotifier, Notifier, parse_recipients
from .server import build_server
from .solapi import DEFAULT_BASE_URL, SolapiClient
from .store import RunStore

ROOT = Path(__file__).resolve().parent.parent

KAKAO_REQUIRED = ("SOLAPI_API_KEY", "SOLAPI_API_SECRET", "KAKAO_PF_ID", "KAKAO_TEMPLATE_ID", "PROCEDURE_APPROVER_PHONES")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def _fail(message: str) -> None:
    print(f"\n설정 오류: {message}\n", file=sys.stderr)
    raise SystemExit(2)


def build_notifier(data_dir: Path, public_url: str) -> tuple[Notifier, str]:
    mode = os.environ.get("PROCEDURE_NOTIFIER") or ("kakao" if os.environ.get("SOLAPI_API_KEY") else "console")
    outbox = data_dir / "outbox.log"
    if mode == "console":
        return ConsoleNotifier(outbox), f"콘솔 (이 창과 {outbox})"
    if mode != "kakao":
        _fail(f"PROCEDURE_NOTIFIER는 console 또는 kakao여야 합니다 (현재: {mode})")

    missing = [k for k in KAKAO_REQUIRED if not os.environ.get(k)]
    if missing:
        _fail("카카오 알림톡을 쓰려면 다음 값이 필요합니다: " + ", ".join(missing))
    try:
        recipients = parse_recipients(os.environ["PROCEDURE_APPROVER_PHONES"])
    except ValueError as exc:
        _fail(str(exc))
    host = urlparse(public_url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1") or not public_url.startswith("https://"):
        print(
            "\n⚠️  PROCEDURE_PUBLIC_URL이 https 공개 주소가 아닙니다. 휴대폰에서 승인 링크를 열 수 없습니다.\n"
            "    알림톡 템플릿 버튼에 등록한 도메인과 같은 https 주소로 설정하세요.\n",
            file=sys.stderr,
        )
    notifier = KakaoAlimtalkNotifier(
        SolapiClient(
            os.environ["SOLAPI_API_KEY"],
            os.environ["SOLAPI_API_SECRET"],
            base_url=os.environ.get("SOLAPI_BASE_URL") or DEFAULT_BASE_URL,
        ),
        pf_id=os.environ["KAKAO_PF_ID"],
        template_id=os.environ["KAKAO_TEMPLATE_ID"],
        recipients=recipients,
        sender_number=os.environ.get("SMS_SENDER_NUMBER"),
        # 알림톡을 쓸 때는 로컬 기록에 링크를 남기지 않는다 (AI가 파일을 읽을 수 있는 환경 대비)
        log=ConsoleNotifier(outbox, include_link=False),
    )
    roles = ", ".join(f"{role} {len(phones)}명" for role, phones in recipients.items())
    sms = "문자 대체발송 켜짐" if os.environ.get("SMS_SENDER_NUMBER") else "문자 대체발송 꺼짐"
    return notifier, f"카카오 알림톡 ({roles}, {sms})"


def main() -> None:
    _load_dotenv()
    host = os.environ.get("PROCEDURE_HOST", "127.0.0.1")
    port = int(os.environ.get("PROCEDURE_PORT", "8765"))
    public_url = os.environ.get("PROCEDURE_PUBLIC_URL", f"http://{host}:{port}").rstrip("/")
    data_dir = Path(os.environ.get("PROCEDURE_DATA_DIR", ROOT / "var" / "procedure_runs"))
    admin_key = os.environ.get("PROCEDURE_ADMIN_KEY") or secrets.token_urlsafe(16)

    procedures = load_procedures()
    check_actions(procedures, ACTIONS)

    store = RunStore(data_dir)
    notifier, notifier_desc = build_notifier(data_dir, public_url)
    engine = Engine(
        procedures,
        ACTIONS,
        store,
        notifier,
        base_url=public_url,
        local_url=f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}",
        browser=BrowserManager(
            headless=os.environ.get("PROCEDURE_HEADLESS", "1") != "0",
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
        ),
    )
    server = build_server(engine, admin_key=admin_key)

    print(
        "\n절차 엔진 MCP 서버\n"
        f"  MCP 주소      http://{host}:{port}/mcp\n"
        f"  대시보드      {public_url}/dashboard?key={admin_key}\n"
        f"  승인 링크     {public_url}/approve/...\n"
        f"  승인 알림     {notifier_desc}\n"
        f"  절차          {', '.join(procedures)}\n"
        f"  실행 기록     {data_dir}\n",
        file=sys.stderr,
        flush=True,
    )
    server.run("streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
