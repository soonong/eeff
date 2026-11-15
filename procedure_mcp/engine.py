"""절차 엔진.

하는 일
- 순서 강제: 한 번에 한 단계씩, 정의된 순서대로만 실행한다. 건너뛰기 없음.
- 승인 게이트: 승인 주체(approver)가 있는 단계는 그 주체의 승인 없이는 실행되지 않는다.
  승인은 승인자에게 따로 보낸 링크(`/approve/<token>`)에서만 가능하고, AI가 쓰는 도구에는 승인 기능이 없다.
- 검증: 각 단계 action은 실행 후 화면/결과를 확인하고 근거(evidence)를 돌려준다. 확인에 실패하면 절차가 멈춘다.
- 기록: 단계마다 상태·근거·스크린샷·승인 내역을 실행 기록(JSON)에 남긴다.
"""
from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from .definitions import Procedure, StepDef
from .notifier import Notifier
from .store import RunStore

TERMINAL = frozenset({"completed", "failed", "rejected", "cancelled"})


class EngineError(Exception):
    """AI나 사람에게 그대로 보여줘도 되는 오류."""


class BrowserProvider(Protocol):
    async def page_for(self, run_id: str) -> Any: ...
    async def screenshot(self, run_id: str, path: Any) -> bool: ...
    async def release(self, run_id: str) -> None: ...


@dataclass
class StepContext:
    """action 함수가 받는 실행 환경."""

    run_id: str
    inputs: Mapping[str, str]
    state: dict[str, Any]  # 단계 사이에 넘길 값. 실행 기록에 함께 저장된다.
    base_url: str
    _browser: BrowserProvider | None = field(default=None, repr=False)

    async def page(self) -> Any:
        if self._browser is None:
            raise EngineError("이 서버에는 브라우저가 설정돼 있지 않습니다")
        return await self._browser.page_for(self.run_id)


ActionFn = Callable[[StepContext], Awaitable[str | None]]


def _now() -> datetime:
    return datetime.now().astimezone()


def _ts(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


class Engine:
    def __init__(
        self,
        procedures: Mapping[str, Procedure],
        actions: Mapping[str, ActionFn],
        store: RunStore,
        notifier: Notifier,
        *,
        base_url: str,
        browser: BrowserProvider | None = None,
        approval_ttl: timedelta = timedelta(hours=24),
    ):
        self.procedures = dict(procedures)
        self.actions = dict(actions)
        self.store = store
        self.notifier = notifier
        self.base_url = base_url.rstrip("/")
        self.browser = browser
        self.approval_ttl = approval_ttl
        self._locks: dict[str, asyncio.Lock] = {}
        self._decided: dict[str, asyncio.Event] = {}
        self._recover_interrupted()

    # ── 조회 ────────────────────────────────────────────────
    def procedure(self, procedure_id: str) -> Procedure:
        try:
            return self.procedures[procedure_id]
        except KeyError:
            raise EngineError(
                f"'{procedure_id}' 절차가 없습니다. 사용할 수 있는 절차: {', '.join(self.procedures) or '(없음)'}"
            ) from None

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.load_run(run_id)
        if run is None:
            raise EngineError(f"실행 '{run_id}'을(를) 찾을 수 없습니다")
        return run

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.list_runs()[:limit]

    # ── AI가 쓰는 동작 ──────────────────────────────────────
    def start(self, procedure_id: str, inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        proc = self.procedure(procedure_id)
        inputs = {str(k): str(v).strip() for k, v in (inputs or {}).items()}
        known = {i.key for i in proc.inputs}
        unknown = sorted(set(inputs) - known)
        if unknown:
            raise EngineError(f"이 절차에 없는 입력값입니다: {', '.join(unknown)} (받는 입력값: {', '.join(known) or '없음'})")
        missing = [f"{i.key}({i.label})" for i in proc.inputs if i.required and not inputs.get(i.key)]
        if missing:
            raise EngineError(f"필수 입력값이 없습니다: {', '.join(missing)}")

        run_id = _now().strftime("%Y%m%d") + "-" + secrets.token_hex(3)
        run = {
            "run_id": run_id,
            "procedure_id": proc.id,
            "procedure_name": proc.name,
            "procedure_version": proc.version,
            "inputs": inputs,
            "status": "in_progress",
            "created_at": _ts(),
            "updated_at": _ts(),
            "state": {},
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "approver": s.approver,
                    "status": "pending",
                    "evidence": None,
                    "error": None,
                    "screenshot": None,
                    "approval": None,
                    "started_at": None,
                    "finished_at": None,
                }
                for s in proc.steps
            ],
            "events": [],
        }
        self._event(run, "started", detail=f"{proc.name} v{proc.version}")
        self.store.save_run(run)
        return run

    async def run_next(self, run_id: str) -> dict[str, Any]:
        """다음 단계 하나만 처리한다.

        돌려주는 outcome
        - executed: 단계를 실행했고 확인까지 통과
        - failed: 단계 실행 또는 확인 실패 → 절차 중단
        - approval_requested: 승인 단계라서 승인자에게 요청을 보냄 (실행 안 함)
        - awaiting_approval: 이미 요청했고 아직 결정 전 (실행 안 함)
        - finished: 이미 끝난 실행
        """
        async with self._lock(run_id):
            run = self.get_run(run_id)
            if run["status"] in TERMINAL:
                return {"outcome": "finished", "run": run, "step": None}

            proc = self.procedure(run["procedure_id"])
            step = next(s for s in run["steps"] if s["status"] != "done")
            step_def = proc.step(step["id"])

            if step_def.approver:
                if step["status"] == "pending" or (
                    step["status"] == "awaiting_approval" and self._approval_expired(step)
                ):
                    self._request_approval(run, proc, step_def, step)
                    return {"outcome": "approval_requested", "run": run, "step": step}
                if step["status"] == "awaiting_approval":
                    return {"outcome": "awaiting_approval", "run": run, "step": step}
                if step["status"] != "approved":  # 이론상 오지 않음 (rejected면 run이 이미 끝남)
                    raise EngineError(f"단계 상태가 올바르지 않습니다: {step['status']}")

            ok = await self._execute(run, proc, step_def, step)
            return {"outcome": "executed" if ok else "failed", "run": run, "step": step}

    async def wait_for_decision(self, run_id: str, timeout: float) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] != "awaiting_approval":
            return run
        event = self._decided.setdefault(run_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            pass
        return self.get_run(run_id)

    async def cancel(self, run_id: str, reason: str = "") -> dict[str, Any]:
        async with self._lock(run_id):
            run = self.get_run(run_id)
            if run["status"] in TERMINAL:
                raise EngineError(f"이미 끝난 실행입니다 (상태: {run['status']})")
            for s in run["steps"]:
                if s["status"] in ("awaiting_approval", "approved"):
                    s["status"] = "cancelled"
            run["status"] = "cancelled"
            self._event(run, "cancelled", detail=reason or None)
            self.store.save_run(run)
        await self._release(run_id)
        self._notify_decided(run_id)
        return run

    # ── 사람만 쓰는 동작 (승인 링크) ─────────────────────────
    def approval_view(self, token: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """승인 페이지에 보여줄 (승인요청, 실행, 단계)."""
        record = self.store.load_approval(token)
        if record is None:
            raise EngineError("유효하지 않은 승인 링크입니다")
        run = self.get_run(record["run_id"])
        step = next(s for s in run["steps"] if s["id"] == record["step_id"])
        return record, run, step

    async def decide(self, token: str, approve: bool, note: str = "") -> dict[str, Any]:
        record = self.store.load_approval(token)
        if record is None:
            raise EngineError("유효하지 않은 승인 링크입니다")
        run_id = record["run_id"]
        async with self._lock(run_id):
            record = self.store.load_approval(token)
            if record.get("decided_at"):
                raise EngineError("이미 처리된 승인 요청입니다")
            if _now() > datetime.fromisoformat(record["expires_at"]):
                raise EngineError("승인 기한이 지났습니다. AI에게 다음 단계를 다시 요청하면 새 승인 요청이 발송됩니다")
            run = self.get_run(run_id)
            step = next(s for s in run["steps"] if s["id"] == record["step_id"])
            if run["status"] in TERMINAL or step["status"] != "awaiting_approval":
                raise EngineError("이 단계는 더 이상 승인을 기다리지 않습니다")
            if step["approval"]["request_id"] != record["request_id"]:
                raise EngineError("새 승인 요청이 발송되어 이 링크는 더 이상 쓸 수 없습니다")

            decided_at = _ts()
            record.update(decided_at=decided_at, decision="approved" if approve else "rejected")
            self.store.update_approval(token, record)

            step["approval"].update(
                decision="approved" if approve else "rejected",
                decided_at=decided_at,
                via="승인 링크",
                note=note.strip() or None,
            )
            if approve:
                step["status"] = "approved"
                run["status"] = "in_progress"
                self._event(run, "approved", step=step["id"], detail=f"{record['role']} 승인")
            else:
                step["status"] = "rejected"
                run["status"] = "rejected"
                self._event(run, "rejected", step=step["id"], detail=f"{record['role']} 거절" + (f": {note}" if note else ""))
            self.store.save_run(run)

        if not approve:
            await self._release(run_id)
        self._notify_decided(run_id)
        return run

    # ── 내부 ────────────────────────────────────────────────
    def _request_approval(self, run: dict, proc: Procedure, step_def: StepDef, step: dict) -> None:
        token = secrets.token_urlsafe(24)
        request_id = secrets.token_hex(4)
        expires = _now() + self.approval_ttl
        self.store.save_approval(
            token,
            {
                "request_id": request_id,
                "run_id": run["run_id"],
                "step_id": step["id"],
                "role": step_def.approver,
                "created_at": _ts(),
                "expires_at": _ts(expires),
                "decided_at": None,
                "decision": None,
            },
        )
        step["status"] = "awaiting_approval"
        step["approval"] = {
            "request_id": request_id,
            "role": step_def.approver,
            "requested_at": _ts(),
            "expires_at": _ts(expires),
            "decision": None,
            "decided_at": None,
        }
        run["status"] = "awaiting_approval"
        self._event(run, "approval_requested", step=step["id"], detail=f"{step_def.approver}에게 발송")
        self.store.save_run(run)
        self._decided[run["run_id"]] = asyncio.Event()
        # 원문 토큰이 담긴 링크는 승인자에게만 간다. 실행 기록과 AI 응답에는 남기지 않는다.
        self.notifier.send_approval_request(
            role=step_def.approver,
            procedure_name=proc.name,
            step_name=step_def.name,
            link=f"{self.base_url}/approve/{token}",
            run_id=run["run_id"],
        )

    async def _execute(self, run: dict, proc: Procedure, step_def: StepDef, step: dict) -> bool:
        step["status"] = "running"
        step["started_at"] = _ts()
        run["status"] = "in_progress"
        self.store.save_run(run)

        ctx = StepContext(
            run_id=run["run_id"],
            inputs=run["inputs"],
            state=run["state"],
            base_url=self.base_url,
            _browser=self.browser,
        )
        try:
            evidence = await asyncio.wait_for(self.actions[step_def.action](ctx), timeout=step_def.timeout_seconds)
        except Exception as exc:  # 실행 또는 확인 실패 → 절차 중단
            step["status"] = "failed"
            step["error"] = _describe_error(exc)
            step["finished_at"] = _ts()
            step["screenshot"] = await self._screenshot(run["run_id"], step["id"])
            run["status"] = "failed"
            self._event(run, "step_failed", step=step["id"], detail=step["error"])
            self.store.save_run(run)
            await self._release(run["run_id"])
            return False

        step["status"] = "done"
        step["evidence"] = evidence or "완료"
        step["finished_at"] = _ts()
        step["screenshot"] = await self._screenshot(run["run_id"], step["id"])
        self._event(run, "step_done", step=step["id"], detail=step["evidence"])
        if all(s["status"] == "done" for s in run["steps"]):
            run["status"] = "completed"
            self._event(run, "completed")
        self.store.save_run(run)
        if run["status"] == "completed":
            await self._release(run["run_id"])
        return True

    async def _screenshot(self, run_id: str, step_id: str) -> str | None:
        if self.browser is None:
            return None
        path = self.store.screen_path(run_id, step_id)
        try:
            ok = await self.browser.screenshot(run_id, path)
        except Exception:
            return None
        return path.name if ok else None

    async def _release(self, run_id: str) -> None:
        if self.browser is not None:
            try:
                await self.browser.release(run_id)
            except Exception:
                pass

    def _approval_expired(self, step: dict) -> bool:
        approval = step.get("approval") or {}
        expires = approval.get("expires_at")
        return bool(expires) and _now() > datetime.fromisoformat(expires)

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    def _notify_decided(self, run_id: str) -> None:
        event = self._decided.get(run_id)
        if event is not None:
            event.set()

    def _event(self, run: dict, kind: str, *, step: str | None = None, detail: str | None = None) -> None:
        run["events"].append({"at": _ts(), "type": kind, "step": step, "detail": detail})
        run["updated_at"] = _ts()

    def _recover_interrupted(self) -> None:
        """서버가 단계 실행 중에 꺼졌던 실행은 실패로 정리한다 (브라우저 상태가 사라졌으므로)."""
        for run in self.store.list_runs():
            running = [s for s in run["steps"] if s["status"] == "running"]
            if not running:
                continue
            for s in running:
                s["status"] = "failed"
                s["error"] = "서버 재시작으로 실행이 중단됨"
            run["status"] = "failed"
            self._event(run, "interrupted", step=running[0]["id"], detail="서버 재시작")
            self.store.save_run(run)


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "시간 초과"
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {message}"[:300]
