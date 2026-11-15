"""SOLAPI(솔라피) 메시지 발송 클라이언트. 표준 라이브러리만 사용한다.

요청 형식은 SOLAPI 공식 Python SDK(solapi 5.0.3)의 소스와 같다.
- POST https://api.solapi.com/messages/v4/send-many/detail
- Authorization: HMAC-SHA256 ApiKey=<키>, Date=<ISO 8601>, salt=<임의 문자열>, signature=<hex(HMAC-SHA256(시크릿, Date+salt))>
- 4xx 응답 본문: {"errorCode": ..., "errorMessage": ...}
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

DEFAULT_BASE_URL = "https://api.solapi.com"


class SolapiError(Exception):
    """발송 요청이 받아들여지지 않았을 때."""


def auth_header(api_key: str, api_secret: str, *, date: str | None = None, salt: str | None = None) -> str:
    date = date or datetime.now().astimezone().isoformat()
    salt = salt or secrets.token_hex(16)
    signature = hmac.new(api_secret.encode(), (date + salt).encode(), hashlib.sha256).hexdigest()
    return f"HMAC-SHA256 ApiKey={api_key}, Date={date}, salt={salt}, signature={signature}"


class SolapiClient:
    def __init__(self, api_key: str, api_secret: str, *, base_url: str = DEFAULT_BASE_URL, timeout: float = 10.0):
        if not api_key or not api_secret:
            raise ValueError("SOLAPI API 키와 시크릿이 필요합니다")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def send_many(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps({"messages": messages, "allowDuplicates": False}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/messages/v4/send-many/detail",
            data=body,
            method="POST",
            headers={
                "Authorization": auth_header(self.api_key, self.api_secret),
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(detail)
                detail = f"{data.get('errorCode', 'UnknownError')}: {data.get('errorMessage', '')}".strip()
            except ValueError:
                detail = detail[:200]
            raise SolapiError(f"SOLAPI 요청 거부 (HTTP {exc.code}) {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SolapiError(f"SOLAPI 연결 실패: {getattr(exc, 'reason', exc)}") from None
        except ValueError:
            raise SolapiError("SOLAPI 응답을 해석할 수 없습니다") from None
