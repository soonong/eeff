"""eval — 정확도 검증 파이프라인."""
from .matchers import (
    match_dict_keys_subset_eq,
    match_exact,
    match_iso_datetime_eq,
    match_nested_set_eq,
    match_numeric_tolerance,
    match_set_eq,
    match_text_normalized_eq,
    match_text_semantic,
    matcher_for,
    score_source_grounding,
    is_hallucination,
)
from .scoring import aggregate, score_record
from .report import write_report

__all__ = [
    "match_exact",
    "match_numeric_tolerance",
    "match_iso_datetime_eq",
    "match_set_eq",
    "match_nested_set_eq",
    "match_dict_keys_subset_eq",
    "match_text_normalized_eq",
    "match_text_semantic",
    "matcher_for",
    "score_source_grounding",
    "is_hallucination",
    "score_record",
    "aggregate",
    "write_report",
]
