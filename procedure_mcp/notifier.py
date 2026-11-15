"""승인 요청 전달.

승인 링크는 AI가 볼 수 없는 경로로만 사람에게 가야 한다.

- ConsoleNotifier: 서버 콘솔과 outbox.log에 남긴다. 내 컴퓨터에서 시험할 때 쓴다.
- KakaoAlimtalkNotifier: 승인자 휴대폰으로 카카오 알림톡을 보낸다 (SOLAPI 경유).
  알림톡 템플릿은 카카오 검수를 받아야 하며, 템플릿 문구는 README의 "카카오 알림톡" 절에 있다.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .solapi import SolapiClient, SolapiError


@dataclass(frozen=True)
class ApprovalRequest:
    role: str
    procedure_name: str
    step_name: str
    run_id: str
    link: str
    expires_at: str  # ISO 8601

    @property
    def token(self) -> str:
        return self.link.rsplit("/", 1)[-1]


class NotifyError(Exception):
    """승인 요청을 보내지 못했을 때. 메시지는 AI와 사용자에게 그대로 보여줄 수 있어야 한다 (링크 금지)."""


class Notifier(Protocol):
    def send_approval_request(self, request: ApprovalRequest) -> str:
        """발송하고 기록용 한 줄 설명을 돌려준다 (링크·토큰을 넣지 말 것). 실패하면 NotifyError."""
        ...


class ConsoleNotifier:
    def __init__(self, outbox: Path, *, include_link: bool = True):
        self.outbox = Path(outbox)
        self.include_link = include_link

    def send_approval_request(self, request: ApprovalRequest) -> str:
        target = request.link if self.include_link else "(링크는 승인자에게만 전달)"
        self._write(
            f"[{request.role}에게 발송] 승인 요청: {request.procedure_name} / {request.step_name} "
            f"(실행 {request.run_id}) → {target}"
        )
        return f"{request.role}에게 발송 (콘솔)"

    def log(self, text: str) -> None:
        self._write(text)

    def _write(self, text: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {text}"
        print(f"\n📨 {line}\n", file=sys.stderr, flush=True)
        with self.outbox.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def parse_recipients(spec: str) -> dict[str, list[str]]:
    """'회원=010-1234-5678;관리자=01011112222,01033334444' → {'회원': [...], '관리자': [...]}"""
    recipients: dict[str, list[str]] = {}
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        if "=" not in part:
            raise ValueError(f"승인자 번호 형식이 잘못되었습니다: '{part}' (예: 회원=01012345678;관리자=01098765432)")
        role, numbers = part.split("=", 1)
        phones = [n.strip().replace("-", "") for n in numbers.split(",") if n.strip()]
        bad = [p for p in phones if not p.isdigit()]
        if bad or not phones:
            raise ValueError(f"'{role.strip()}'의 휴대폰 번호가 잘못되었습니다: {numbers}")
        recipients[role.strip()] = phones
    return recipients


def _short_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%m/%d %H:%M")
    except ValueError:
        return iso


class KakaoAlimtalkNotifier:
    """승인 요청을 카카오 알림톡으로 보낸다.

    템플릿 변수 (README의 템플릿과 같아야 한다)
        #{승인주체} #{절차명} #{단계명} #{실행번호} #{승인기한}  본문
        #{승인토큰}                                          버튼 링크: https://<공개 도메인>/approve/#{승인토큰}
    """

    def __init__(
        self,
        client: SolapiClient,
        *,
        pf_id: str,
        template_id: str,
        recipients: Mapping[str, Sequence[str]],
        sender_number: str | None = None,
        log: ConsoleNotifier | None = None,
    ):
        if not pf_id or not template_id:
            raise ValueError("카카오 채널 ID(pfId)와 템플릿 ID가 필요합니다")
        self.client = client
        self.pf_id = pf_id
        self.template_id = template_id
        self.recipients = {role: list(phones) for role, phones in recipients.items()}
        self.sender_number = (sender_number or "").replace("-", "") or None
        self.log = log

    def variables(self, request: ApprovalRequest) -> dict[str, str]:
        return {
            "#{승인주체}": request.role,
            "#{절차명}": request.procedure_name,
            "#{단계명}": request.step_name,
            "#{실행번호}": request.run_id,
            "#{승인기한}": _short_time(request.expires_at),
            "#{승인토큰}": request.token,
        }

    def build_messages(self, request: ApprovalRequest) -> list[dict]:
        phones = self.recipients.get(request.role)
        if not phones:
            raise NotifyError(f"'{request.role}' 승인자의 휴대폰 번호가 설정돼 있지 않습니다 (PROCEDURE_APPROVER_PHONES)")
        kakao_options = {
            "pfId": self.pf_id,
            "templateId": self.template_id,
            "variables": self.variables(request),
            # 알림톡이 실패하면 문자로 대신 보낸다. 문자는 등록된 발신번호가 있어야 하므로 없으면 끈다.
            "disableSms": self.sender_number is None,
        }
        messages = []
        for phone in phones:
            message = {"to": phone, "type": "ATA", "country": "82", "kakaoOptions": kakao_options}
            if self.sender_number:
                message["from"] = self.sender_number
            messages.append(message)
        return messages

    def send_approval_request(self, request: ApprovalRequest) -> str:
        messages = self.build_messages(request)
        try:
            response = self.client.send_many(messages)
        except SolapiError as exc:
            raise NotifyError(f"알림톡 발송 실패: {exc}") from None

        count = (response.get("groupInfo") or {}).get("count") or {}
        failed = response.get("failedMessageList") or []
        accepted = count.get("registeredSuccess", len(messages) - len(failed))
        if accepted <= 0:
            reasons = ", ".join(sorted({f.get("statusMessage", "알 수 없음") for f in failed})) or "접수된 메시지 없음"
            raise NotifyError(f"알림톡 발송 실패: {reasons}")

        note = f"{request.role}에게 알림톡 발송 ({accepted}/{len(messages)}건 접수)"
        if failed:
            note += f", 실패 {len(failed)}건: " + ", ".join(sorted({f.get('statusMessage', '') for f in failed}))
        if self.log is not None:
            self.log.log(f"{note} · {request.procedure_name} / {request.step_name} (실행 {request.run_id})")
        return note
