"""승인 요청 전달.

승인 링크는 AI가 볼 수 없는 경로로만 사람에게 가야 한다.
이 프로토타입은 서버 콘솔과 `outbox.log` 파일에 남기는 것으로 "회원/관리자의 휴대폰"을 대신한다.
실서비스에서는 이 클래스를 앱 푸시, 문자, 카카오 알림톡 등으로 바꾼다.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol


class Notifier(Protocol):
    def send_approval_request(
        self, *, role: str, procedure_name: str, step_name: str, link: str, run_id: str
    ) -> None: ...


class ConsoleNotifier:
    def __init__(self, outbox: Path):
        self.outbox = Path(outbox)

    def send_approval_request(
        self, *, role: str, procedure_name: str, step_name: str, link: str, run_id: str
    ) -> None:
        line = (
            f"[{datetime.now().isoformat(timespec='seconds')}] [{role}에게 발송] "
            f"승인 요청: {procedure_name} / {step_name} (실행 {run_id}) → {link}"
        )
        print(f"\n📨 {line}\n", file=sys.stderr, flush=True)
        with self.outbox.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
