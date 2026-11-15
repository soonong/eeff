"""절차 엔진 MCP 서버 실행.

    python -m procedure_mcp

환경변수
    PROCEDURE_HOST                  기본 127.0.0.1
    PROCEDURE_PORT                  기본 8765
    PROCEDURE_PUBLIC_URL            승인 링크·데모 사이트에 쓰는 바깥 주소 (기본 http://<host>:<port>)
    PROCEDURE_DATA_DIR              실행 기록 저장 위치 (기본 var/procedure_runs)
    PROCEDURE_ADMIN_KEY             대시보드 키 (없으면 실행할 때마다 새로 만듦)
    PROCEDURE_HEADLESS              0이면 브라우저 창을 띄움 (기본 1)
    PLAYWRIGHT_CHROMIUM_EXECUTABLE  미리 설치된 Chromium 경로 (없으면 Playwright 기본 브라우저)
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from .actions import ACTIONS
from .browser import BrowserManager
from .definitions import check_actions, load_procedures
from .engine import Engine
from .notifier import ConsoleNotifier
from .server import build_server
from .store import RunStore

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    host = os.environ.get("PROCEDURE_HOST", "127.0.0.1")
    port = int(os.environ.get("PROCEDURE_PORT", "8765"))
    public_url = os.environ.get("PROCEDURE_PUBLIC_URL", f"http://{host}:{port}").rstrip("/")
    data_dir = Path(os.environ.get("PROCEDURE_DATA_DIR", ROOT / "var" / "procedure_runs"))
    admin_key = os.environ.get("PROCEDURE_ADMIN_KEY") or secrets.token_urlsafe(16)

    procedures = load_procedures()
    check_actions(procedures, ACTIONS)

    store = RunStore(data_dir)
    engine = Engine(
        procedures,
        ACTIONS,
        store,
        ConsoleNotifier(data_dir / "outbox.log"),
        base_url=public_url,
        browser=BrowserManager(
            headless=os.environ.get("PROCEDURE_HEADLESS", "1") != "0",
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
        ),
    )
    server = build_server(engine, admin_key=admin_key)

    print(
        "\n절차 엔진 MCP 서버\n"
        f"  MCP 주소      {public_url}/mcp\n"
        f"  대시보드      {public_url}/dashboard?key={admin_key}\n"
        f"  절차          {', '.join(procedures)}\n"
        f"  실행 기록     {data_dir}\n"
        f"  승인 요청     이 콘솔과 {data_dir / 'outbox.log'} 에 표시됩니다 (실서비스에서는 푸시/문자로 교체)\n",
        file=sys.stderr,
        flush=True,
    )
    server.run("streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
