"""eval/report.py — Markdown + CSV 리포트 출력."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# 자유 텍스트 마스킹 기준 (글자수)
_TEXT_TRUNCATE_LEN = 100

# 금액·날짜는 마스킹 제외 (숫자, 날짜 패턴)
_AMOUNT_PATTERN = re.compile(r"^\d[\d,\s]*원?$")
_DATE_PATTERN = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")


def _mask_value(value: Any) -> str:
    """값을 안전하게 표현. 100자 초과 텍스트는 마스킹."""
    if value is None:
        return "null"
    s = str(value)
    # 금액·날짜 패턴이면 그대로
    if _AMOUNT_PATTERN.match(s.strip()) or _DATE_PATTERN.search(s):
        return s if len(s) <= _TEXT_TRUNCATE_LEN else s[:_TEXT_TRUNCATE_LEN] + " [TRUNCATED]"
    # 100자 초과 자유텍스트 마스킹
    if len(s) > _TEXT_TRUNCATE_LEN:
        return s[:_TEXT_TRUNCATE_LEN] + " [TRUNCATED]"
    return s


def write_report(
    aggregated: dict,
    records: list[dict],
    out_dir: str | Path,
) -> None:
    """리포트 3종 출력.

    - summary.md
    - per_field.csv
    - failures/{notice_id}_{field}.md
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    _write_summary(aggregated, out)
    _write_per_field_csv(aggregated, out)
    _write_failures(records, out)


def _write_summary(aggregated: dict, out: Path) -> None:
    lines: list[str] = [
        "# 평가 리포트 요약",
        "",
        f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 전체 점수",
        "",
        f"| 항목 | 값 |",
        f"|---|---|",
        f"| 가중평균 정확도 | **{aggregated.get('weighted_avg', 0.0):.4f}** |",
        f"| Source Grounding 평균 | {aggregated.get('source_grounding_avg', 0.0):.4f} |",
        f"| Hallucination Rate | {aggregated.get('hallucination_rate', 0.0):.4f} |",
        f"| 평가 건수 | {len([r for r in (aggregated.get('per_field') or {}).values() if r.get('total', 0) > 0])} 필드 |",
        "",
    ]

    # per_category 표
    per_cat = aggregated.get("per_category", {})
    if per_cat:
        lines += [
            "## 카테고리별 가중평균",
            "",
            "| 카테고리 | 가중평균 |",
            "|---|---|",
        ]
        for cat, acc in sorted(per_cat.items(), key=lambda x: -x[1]):
            lines.append(f"| {cat} | {acc:.4f} |")
        lines.append("")

    # top10 failures 표
    top10 = aggregated.get("top10_failures", [])
    if top10:
        lines += [
            "## Top10 실패 필드",
            "",
            "| 순위 | 필드 | 정확도 | mismatch | total |",
            "|---|---|---|---|---|",
        ]
        for i, item in enumerate(top10, 1):
            lines.append(
                f"| {i} | {item['field']} | {item['accuracy']:.4f} "
                f"| {item['mismatch']} | {item['total']} |"
            )
        lines.append("")

    summary_path = out / "summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def _write_per_field_csv(aggregated: dict, out: Path) -> None:
    per_field = aggregated.get("per_field", {})
    csv_path = out / "per_field.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["필드", "정확도", "correct", "partial", "mismatch", "both_null", "total", "category"],
        )
        writer.writeheader()
        for key, info in per_field.items():
            writer.writerow({
                "필드": key,
                "정확도": f"{info.get('accuracy', 0.0):.4f}",
                "correct": info.get("correct", 0),
                "partial": info.get("partial", 0),
                "mismatch": info.get("mismatch", 0),
                "both_null": info.get("both_null", 0),
                "total": info.get("total", 0),
                "category": info.get("category", ""),
            })


def _write_failures(records: list[dict], out: Path) -> None:
    failures_dir = out / "failures"
    failures_dir.mkdir(exist_ok=True)

    for rec in records:
        notice_id = rec.get("notice_id", "unknown")
        fields = rec.get("fields", {})
        expected_data = rec.get("expected", {})
        actual_data = rec.get("actual", {})

        for field, info in fields.items():
            label = info.get("label", "")
            if label in ("correct", "both_null"):
                continue  # 정답은 failure 파일 불필요

            e_val = expected_data.get(field)
            a_val = actual_data.get(field)
            reason = info.get("reason", "")

            lines: list[str] = [
                f"# {notice_id} — {field}",
                "",
                f"**레이블**: {label}  ",
                f"**점수**: {info.get('score', 0.0):.2f}  ",
                f"**매처 사유**: {reason}",
                "",
                "## Expected",
                f"```",
                _mask_value(e_val),
                f"```",
                "",
                "## Actual",
                f"```",
                _mask_value(a_val),
                f"```",
            ]

            # source 정보 (있으면)
            e_src = rec.get("expected_source", {}).get(field)
            a_src = rec.get("actual_source", {}).get(field)
            if e_src or a_src:
                lines += [
                    "",
                    "## Source",
                    f"- expected_source: `{_mask_value(e_src)}`",
                    f"- actual_source: `{_mask_value(a_src)}`",
                ]

            # 파일명: notice_id_field.md (특수문자 대체)
            safe_field = re.sub(r"[^\w가-힣]", "_", field)
            safe_id = re.sub(r"[^\w]", "_", str(notice_id))
            path = failures_dir / f"{safe_id}_{safe_field}.md"
            path.write_text("\n".join(lines), encoding="utf-8")
