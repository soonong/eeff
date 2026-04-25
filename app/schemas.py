from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class Rule:
    key: str
    description: str
    type: str
    required: bool
    validator: str | None
    few_shot: str | None


class RawExtraction(BaseModel):
    """LLM이 직접 채우는 1차 결과. 모든 키는 동적이라 dict로 받는다."""

    extracted: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, str] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    key: str
    kind: str
    detail: str = ""


class BidExtraction(BaseModel):
    """검증·정규화 후 최종 결과."""

    extracted: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, str] = Field(default_factory=dict)
    issues: list[ValidationIssue] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    file_name: str
    char_count: int
    extracted: dict[str, Any]
    source: dict[str, str]
    issues: list[ValidationIssue]
    usage: dict[str, int] = Field(default_factory=dict)
