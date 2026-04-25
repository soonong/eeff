from __future__ import annotations

import re
from typing import Any

_OR_TOKENS = re.compile(r"\s*(?:또는|혹은|이나|하거나|/)\s*")
_AND_TOKENS = re.compile(r"\s*(?:및|겸유|모두|그리고|와|과|\+|,)\s*")
_JURYEOK = re.compile(r"\(\s*주력분야\s*[:：]\s*([^)]+)\)")
_LAW_PAREN = re.compile(r"\([^()]*법[^()]*\)")
_TRAILING_PHRASE = re.compile(r"(?:을|를)?\s*보유한\s*자\s*$")
_QUALIFIER_TAIL = re.compile(r"(?:으로|로)\s*등록된?\s*자\s*$")


def normalize_jongmok(raw: Any) -> list[list[str]]:
    """Normalize a 종목 (qualifications) value into nested AND/OR lists."""
    groups: list[list[str]] = []

    if isinstance(raw, list):
        if raw and all(isinstance(g, list) for g in raw):
            for group in raw:
                items = [_normalize_item(str(x)) for x in group if str(x).strip()]
                items = [it for it in items if it]
                if items:
                    groups.append(items)
            return groups
        joined = " 및 ".join(str(x) for x in raw if str(x).strip())
        return normalize_jongmok(joined)

    if not isinstance(raw, str):
        return []

    text = raw.strip()
    if not text:
        return []

    or_parts = _split_keep_paren(text, _OR_TOKENS)
    for part in or_parts:
        part = part.strip()
        if not part:
            continue
        and_parts = _split_keep_paren(part, _AND_TOKENS)
        items = [_normalize_item(p) for p in and_parts]
        items = [it for it in items if it]
        if items:
            groups.append(items)
    return groups


def _split_keep_paren(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Split text by pattern but never inside parentheses."""
    result: list[str] = []
    depth = 0
    last = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            m = pattern.match(text, i)
            if m and m.end() > i:
                result.append(text[last : m.start()])
                last = m.end()
                i = m.end()
                continue
        i += 1
    result.append(text[last:])
    return result


def _normalize_item(item: str) -> str:
    s = item.strip()
    if not s:
        return ""
    juryeok = _JURYEOK.search(s)
    if juryeok:
        s = juryeok.group(1).strip()
    else:
        s = _LAW_PAREN.sub("", s).strip()
    s = _TRAILING_PHRASE.sub("", s).strip()
    s = _QUALIFIER_TAIL.sub("", s).strip()
    s = s.strip("·• \t,;:.\"'")
    return s
