"""단계 action 모음.

절차 정의(YAML)의 `action: <이름>`이 여기 등록된 함수를 가리킨다.
새 절차를 붙이려면 모듈을 하나 만들고 그 모듈의 ACTIONS를 아래에 합치면 된다.

action 규칙
- `async def fn(ctx: StepContext) -> str` : 근거(사람이 읽을 한 줄)를 돌려준다.
- 실행만 하지 말고 결과를 확인한다. 확인에 실패하면 예외를 던진다 → 엔진이 절차를 멈춘다.
"""
from __future__ import annotations

from . import member_demo

ACTIONS = {
    **member_demo.ACTIONS,
}
