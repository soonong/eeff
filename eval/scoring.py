"""eval/scoring.py — 필드별 점수 계산 + 전체 집계."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .matchers import (
    is_hallucination,
    matcher_for,
    score_source_grounding,
)

# columns.csv 위치
_COLUMNS_CSV = Path(__file__).resolve().parent.parent / "data" / "columns.csv"

# required=true 필드 가중치
_REQUIRED_WEIGHT = 2.0
_DEFAULT_WEIGHT = 1.0


def _load_rules_from_csv(csv_path: Path | None = None) -> list[dict]:
    """columns.csv를 dict 리스트로 로드."""
    path = csv_path or _COLUMNS_CSV
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader if (row.get("key") or "").strip()]


def score_record(
    expected: dict[str, Any],
    actual: dict[str, Any],
    rules: list[dict],
    raw_text: str = "",
    expected_source: dict[str, str] | None = None,
    actual_source: dict[str, str] | None = None,
) -> dict:
    """한 ground_truth 레코드의 필드별 점수 계산.

    반환:
        {
            "fields": {
                <key>: {
                    "score": float,
                    "label": "correct"|"partial"|"mismatch"|"both_null",
                    "reason": str,
                    "weight": float,
                    "required": bool,
                    "category": str,
                    "source_grounding": float,
                    "hallucination": bool,
                }
            },
            "weighted_score": float,
            "total_weight": float,
        }
    """
    fields: dict[str, dict] = {}
    total_weight = 0.0
    weighted_sum = 0.0

    e_src = expected_source or {}
    a_src = actual_source or {}

    for rule in rules:
        key = rule.get("key", "").strip()
        if not key:
            continue

        e_val = expected.get(key)
        a_val = actual.get(key)
        required = (rule.get("required") or "").strip().lower() in {"true", "1", "yes", "y"}
        weight = _REQUIRED_WEIGHT if required else _DEFAULT_WEIGHT
        category = rule.get("category", "").strip() if "category" in rule else ""

        # 양쪽 null → both_null (점수 1.0으로 처리하되 correct 카운트)
        if e_val is None and a_val is None:
            label = "both_null"
            score = 1.0
            reason = "both null"
        else:
            matcher = matcher_for(rule)
            try:
                score, reason = matcher(e_val, a_val)
            except Exception as exc:
                score, reason = 0.0, f"matcher error: {exc}"

            if score >= 1.0:
                label = "correct"
            elif score >= 0.5:
                label = "partial"
            else:
                label = "mismatch"

        # source grounding
        grounding = score_source_grounding(e_src.get(key), a_src.get(key), raw_text) if raw_text else 0.0

        # hallucination
        halluc = is_hallucination(a_val, a_src.get(key), raw_text) if raw_text else False

        fields[key] = {
            "score": score,
            "label": label,
            "reason": reason,
            "weight": weight,
            "required": required,
            "category": category,
            "source_grounding": grounding,
            "hallucination": halluc,
        }

        weighted_sum += score * weight
        total_weight += weight

    weighted_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    return {
        "fields": fields,
        "weighted_score": weighted_score,
        "total_weight": total_weight,
    }


def aggregate(records: list[dict], rules: list[dict]) -> dict:
    """전체/필드별/카테고리별 가중평균 집계.

    records: score_record 반환값 리스트 (각 항목에 notice_id 추가 가능)

    출력 키:
        weighted_avg, per_field, per_category, top10_failures,
        source_grounding_avg, hallucination_rate
    """
    if not records:
        return {
            "weighted_avg": 0.0,
            "per_field": {},
            "per_category": {},
            "top10_failures": [],
            "source_grounding_avg": 0.0,
            "hallucination_rate": 0.0,
        }

    key_list = [r.get("key", "").strip() for r in rules if r.get("key", "").strip()]

    # per_field 집계용
    per_field: dict[str, dict] = {
        k: {
            "weight_sum": 0.0,
            "weighted_score_sum": 0.0,
            "correct": 0,
            "partial": 0,
            "mismatch": 0,
            "both_null": 0,
            "total": 0,
            "category": "",
        }
        for k in key_list
    }

    # 전체 집계
    total_weighted_sum = 0.0
    total_weight_sum = 0.0

    # source grounding / hallucination
    grounding_scores: list[float] = []
    hallucination_flags: list[bool] = []

    for rec in records:
        fields = rec.get("fields", {})
        for key, info in fields.items():
            if key not in per_field:
                per_field[key] = {
                    "weight_sum": 0.0,
                    "weighted_score_sum": 0.0,
                    "correct": 0,
                    "partial": 0,
                    "mismatch": 0,
                    "both_null": 0,
                    "total": 0,
                    "category": "",
                }
            pf = per_field[key]
            score = info["score"]
            weight = info["weight"]
            label = info["label"]

            pf["weight_sum"] += weight
            pf["weighted_score_sum"] += score * weight
            pf["total"] += 1
            if label == "correct":
                pf["correct"] += 1
            elif label == "partial":
                pf["partial"] += 1
            elif label == "both_null":
                pf["both_null"] += 1
            else:
                pf["mismatch"] += 1

            if not pf["category"] and info.get("category"):
                pf["category"] = info["category"]

            total_weighted_sum += score * weight
            total_weight_sum += weight

            # source grounding (raw_text 있을 때만 의미 있음)
            if "source_grounding" in info:
                grounding_scores.append(info["source_grounding"])
            if "hallucination" in info:
                hallucination_flags.append(bool(info["hallucination"]))

    weighted_avg = total_weighted_sum / total_weight_sum if total_weight_sum > 0 else 0.0

    # per_field 정리
    per_field_out: dict[str, dict] = {}
    for key, pf in per_field.items():
        n = pf["total"]
        acc = pf["weighted_score_sum"] / pf["weight_sum"] if pf["weight_sum"] > 0 else 0.0
        per_field_out[key] = {
            "accuracy": round(acc, 4),
            "correct": pf["correct"],
            "partial": pf["partial"],
            "mismatch": pf["mismatch"],
            "both_null": pf["both_null"],
            "total": n,
            "category": pf["category"],
        }

    # per_category 집계
    cat_sums: dict[str, list[float]] = {}
    cat_weights: dict[str, list[float]] = {}
    for key, pf in per_field.items():
        cat = pf["category"] or "uncategorized"
        n = pf["total"]
        if n == 0:
            continue
        ws = pf["weighted_score_sum"]
        wt = pf["weight_sum"]
        cat_sums.setdefault(cat, []).append(ws)
        cat_weights.setdefault(cat, []).append(wt)

    per_category: dict[str, float] = {}
    for cat in cat_sums:
        total_ws = sum(cat_sums[cat])
        total_wt = sum(cat_weights[cat])
        per_category[cat] = round(total_ws / total_wt, 4) if total_wt > 0 else 0.0

    # top10_failures (accuracy 낮은 순)
    sorted_fields = sorted(
        per_field_out.items(),
        key=lambda x: (x[1]["accuracy"], x[1]["total"]),
    )
    top10_failures = [
        {"field": k, "accuracy": v["accuracy"], "mismatch": v["mismatch"], "total": v["total"]}
        for k, v in sorted_fields[:10]
        if v["total"] > 0
    ]

    source_grounding_avg = (
        sum(grounding_scores) / len(grounding_scores) if grounding_scores else 0.0
    )
    hallucination_rate = (
        sum(1 for f in hallucination_flags if f) / len(hallucination_flags)
        if hallucination_flags
        else 0.0
    )

    return {
        "weighted_avg": round(weighted_avg, 4),
        "per_field": per_field_out,
        "per_category": per_category,
        "top10_failures": top10_failures,
        "source_grounding_avg": round(source_grounding_avg, 4),
        "hallucination_rate": round(hallucination_rate, 4),
    }
