from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .prompts import build_system_instruction
from .schemas import RawExtraction, Rule

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-1.5-flash-002"
_DEFAULT_TTL_SECONDS = 3600

_state: dict[str, Any] = {
    "client": None,
    "cache_name": None,
    "cache_signature": None,
    "cache_expires_at": None,
    "last_usage": {},
}


def _get_client():
    if _state["client"] is None:
        from google import genai  # type: ignore[import-not-found]

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is not set")
        _state["client"] = genai.Client(api_key=api_key)
    return _state["client"]


def _model_name() -> str:
    return os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)


def _rules_signature(rules: list[Rule]) -> str:
    payload = json.dumps(
        [asdict(r) for r in rules], sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ensure_cache(rules: list[Rule]) -> str:
    from google.genai import types  # type: ignore[import-not-found]

    sig = _rules_signature(rules)
    now = datetime.now(timezone.utc)
    cache_name = _state.get("cache_name")
    cache_signature = _state.get("cache_signature")
    cache_expires_at = _state.get("cache_expires_at")

    if (
        cache_name
        and sig == cache_signature
        and cache_expires_at
        and cache_expires_at > now + timedelta(minutes=5)
    ):
        return cache_name

    client = _get_client()
    system_instruction = build_system_instruction(rules)
    cache = client.caches.create(
        model=_model_name(),
        config=types.CreateCachedContentConfig(
            system_instruction=system_instruction,
            ttl=f"{_DEFAULT_TTL_SECONDS}s",
        ),
    )
    _state["cache_name"] = cache.name
    _state["cache_signature"] = sig
    _state["cache_expires_at"] = now + timedelta(seconds=_DEFAULT_TTL_SECONDS)
    log.info("created Gemini cache name=%s signature=%s", cache.name, sig[:12])
    return cache.name


def extract(markdown: str, rules: list[Rule]) -> RawExtraction:
    from google.genai import types  # type: ignore[import-not-found]

    client = _get_client()
    cache_name = _ensure_cache(rules)
    response = client.models.generate_content(
        model=_model_name(),
        contents=markdown,
        config=types.GenerateContentConfig(
            cached_content=cache_name,
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    usage = getattr(response, "usage_metadata", None)
    _state["last_usage"] = _serialize_usage(usage)
    log.info("gemini usage=%s", _state["last_usage"])

    raw_text = getattr(response, "text", None) or ""
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned non-JSON response: {exc}: {raw_text[:500]}") from exc

    extracted = parsed.get("extracted") if isinstance(parsed, dict) else None
    source = parsed.get("source") if isinstance(parsed, dict) else None
    if not isinstance(extracted, dict):
        raise RuntimeError(f"Gemini response missing 'extracted' object: {raw_text[:500]}")
    return RawExtraction(extracted=extracted, source=source if isinstance(source, dict) else {})


def last_usage() -> dict[str, int]:
    return dict(_state.get("last_usage") or {})


def _serialize_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    keys = (
        "prompt_token_count",
        "candidates_token_count",
        "cached_content_token_count",
        "total_token_count",
    )
    out: dict[str, int] = {}
    for k in keys:
        v = getattr(usage, k, None)
        if isinstance(v, int):
            out[k] = v
    return out


def reset_cache_state() -> None:
    """Test helper: drop in-memory cache handle."""
    _state["cache_name"] = None
    _state["cache_signature"] = None
    _state["cache_expires_at"] = None
    _state["last_usage"] = {}
