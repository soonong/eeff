"""절차 정의 로더.

규칙은 코드 밖: 절차는 `procedures/*.yaml` 파일로 관리한다.
파일 하나가 절차 하나이며, 단계·승인 주체·검증 정보(절차 카드)를 담는다.
단계가 실제로 하는 일(action)은 `actions/` 아래 파이썬 함수로 구현하고, YAML에서는 이름으로만 참조한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROCEDURES_DIR = Path(__file__).parent / "procedures"


class DefinitionError(ValueError):
    """절차 정의 파일이 잘못되었을 때."""


@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    required: bool = True
    example: str | None = None


@dataclass(frozen=True)
class StepDef:
    id: str
    name: str
    action: str
    approver: str | None = None  # None이면 자동 실행, 값이 있으면 그 주체의 승인이 있어야 실행
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class Procedure:
    id: str
    name: str
    version: str
    description: str
    risk: str
    verification: Mapping[str, str]
    inputs: tuple[InputSpec, ...]
    steps: tuple[StepDef, ...]

    def step(self, step_id: str) -> StepDef:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)


def _require(data: Mapping[str, Any], key: str, source: str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise DefinitionError(f"{source}: '{key}' 항목이 필요합니다")
    return data[key]


def parse_procedure(data: Mapping[str, Any], *, source: str = "<memory>") -> Procedure:
    if not isinstance(data, Mapping):
        raise DefinitionError(f"{source}: 최상위는 매핑이어야 합니다")

    raw_inputs = data.get("inputs") or {}
    if not isinstance(raw_inputs, Mapping):
        raise DefinitionError(f"{source}: 'inputs'는 매핑이어야 합니다")
    inputs = tuple(
        InputSpec(
            key=str(key),
            label=str((spec or {}).get("label", key)),
            required=bool((spec or {}).get("required", True)),
            example=(spec or {}).get("example"),
        )
        for key, spec in raw_inputs.items()
    )

    raw_steps = _require(data, "steps", source)
    if not isinstance(raw_steps, list) or not raw_steps:
        raise DefinitionError(f"{source}: 'steps'는 비어 있지 않은 목록이어야 합니다")
    steps: list[StepDef] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_steps, 1):
        where = f"{source} steps[{i}]"
        step_id = str(_require(raw, "id", where))
        if step_id in seen:
            raise DefinitionError(f"{where}: 단계 id '{step_id}'가 중복됩니다")
        seen.add(step_id)
        approver = raw.get("approver")
        steps.append(
            StepDef(
                id=step_id,
                name=str(_require(raw, "name", where)),
                action=str(_require(raw, "action", where)),
                approver=str(approver) if approver else None,
                timeout_seconds=float(raw.get("timeout_seconds", 60)),
            )
        )

    verification = data.get("verification") or {}
    if not isinstance(verification, Mapping):
        raise DefinitionError(f"{source}: 'verification'은 매핑이어야 합니다")

    return Procedure(
        id=str(_require(data, "id", source)),
        name=str(_require(data, "name", source)),
        version=str(_require(data, "version", source)),
        description=str(data.get("description", "")).strip(),
        risk=str(data.get("risk", "normal")),
        verification={str(k): str(v) for k, v in verification.items()},
        inputs=inputs,
        steps=tuple(steps),
    )


def load_procedures(directory: Path = PROCEDURES_DIR) -> dict[str, Procedure]:
    procedures: dict[str, Procedure] = {}
    for path in sorted(directory.glob("*.yaml")):
        proc = parse_procedure(yaml.safe_load(path.read_text(encoding="utf-8")), source=path.name)
        if proc.id in procedures:
            raise DefinitionError(f"{path.name}: 절차 id '{proc.id}'가 중복됩니다")
        procedures[proc.id] = proc
    return procedures


def check_actions(procedures: Mapping[str, Procedure], actions: Mapping[str, object]) -> None:
    """정의 파일이 참조하는 action이 모두 구현돼 있는지 서버 시작 시점에 확인한다."""
    missing = [
        f"{p.id}/{s.id} → {s.action}" for p in procedures.values() for s in p.steps if s.action not in actions
    ]
    if missing:
        raise DefinitionError("구현되지 않은 action: " + ", ".join(missing))
