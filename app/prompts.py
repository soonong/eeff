from __future__ import annotations

from pathlib import Path

from .schemas import Rule

_FEW_SHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "few_shot"

_PERSONA = """당신은 한국 공공입찰 공고 분석 전문가입니다.
업로드된 공고문에서 정해진 항목을 정확히 추출하여 JSON으로 반환합니다.

지켜야 할 원칙
- 원문에 명시된 정보만 추출하고, 명시되지 않은 값은 null 로 두세요.
- 금액은 정수(원 단위), 비율(%)은 0~1 사이 소수점 5자리, 일시는 ISO 8601(yyyy-MM-ddTHH:mm)으로 정규화하세요.
- 자유 추론(`ai컬럼`)을 제외하고는 원문에 없는 값을 임의로 만들지 마세요.
- 각 항목마다 그 값을 발견한 원문 한 줄을 `source` 객체의 동일 키 아래 함께 반환하세요.
- 응답 전체는 반드시 다음 형태의 JSON 한 개여야 합니다:
  {"extracted": {<항목>: <값>, ...}, "source": {<항목>: "<원문 한 줄>", ...}}
"""


def build_system_instruction(rules: list[Rule]) -> str:
    """Compose the Gemini system instruction from the Rule Dictionary."""
    lines: list[str] = [_PERSONA, "", "## 추출 항목 정의"]
    for rule in rules:
        bits = [f"- **{rule.key}** ({rule.type}{', 필수' if rule.required else ''})"]
        if rule.description:
            bits.append(f": {rule.description}")
        if rule.validator:
            bits.append(f" [검증: {rule.validator}]")
        if rule.few_shot:
            bits.append(f"\n  예시: {rule.few_shot}")
        lines.append("".join(bits))

    extras = _load_extra_few_shot()
    if extras:
        lines.append("")
        lines.append("## 보조 예시")
        lines.append(extras)

    lines.append("")
    lines.append("## 출력 형식")
    lines.append('반드시 `{"extracted": {...}, "source": {...}}` 구조의 JSON 한 개만 반환하세요. 다른 텍스트나 설명은 금지합니다.')
    return "\n".join(lines)


def _load_extra_few_shot() -> str:
    if not _FEW_SHOT_DIR.exists():
        return ""
    chunks: list[str] = []
    for path in sorted(_FEW_SHOT_DIR.glob("*.md")):
        try:
            chunks.append(path.read_text(encoding="utf-8").strip())
        except OSError:
            continue
    return "\n\n".join(chunks)
