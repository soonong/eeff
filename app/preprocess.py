from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "iframe")
_BLANK_RUN = re.compile(r"\n[ \t]*\n[ \t]*\n+")
_TRAILING_WS = re.compile(r"[ \t]+\n")


def html_to_markdown(html: str) -> str:
    """Strip HTML chrome and convert tables to Markdown for AI consumption."""
    soup = BeautifulSoup(html, "lxml")
    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for table in list(soup.find_all("table")):
        table.replace_with(NavigableString("\n" + _table_to_markdown(table) + "\n"))
    text = soup.get_text("\n")
    return _collapse_whitespace(text)


def _table_to_markdown(table: Tag) -> str:
    """Render <table> as a Markdown table, expanding colspan/rowspan into a flat grid."""
    rows = table.find_all("tr")
    if not rows:
        return ""

    grid: list[list[str]] = []
    rowspan_carry: dict[int, tuple[str, int]] = {}

    for tr in rows:
        cells = tr.find_all(["th", "td"])
        out_row: list[str] = []
        col = 0
        cell_iter = iter(cells)
        while True:
            while col in rowspan_carry:
                value, remaining = rowspan_carry[col]
                out_row.append(value)
                if remaining - 1 <= 0:
                    del rowspan_carry[col]
                else:
                    rowspan_carry[col] = (value, remaining - 1)
                col += 1
            try:
                cell = next(cell_iter)
            except StopIteration:
                break
            text = _clean_cell_text(cell.get_text(" ", strip=True))
            colspan = _safe_int(cell.get("colspan"), 1)
            rowspan = _safe_int(cell.get("rowspan"), 1)
            for _ in range(colspan):
                out_row.append(text)
                if rowspan > 1:
                    rowspan_carry[col] = (text, rowspan - 1)
                col += 1
        if out_row:
            grid.append(out_row)

    if not grid:
        return ""

    width = max(len(r) for r in grid)
    for r in grid:
        while len(r) < width:
            r.append("")

    header = grid[0]
    body = grid[1:] if len(grid) > 1 else []
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _clean_cell_text(s: str) -> str:
    s = s.replace("|", "\\|").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def _safe_int(v: object, default: int) -> int:
    try:
        n = int(str(v))
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _collapse_whitespace(s: str) -> str:
    s = _TRAILING_WS.sub("\n", s)
    s = _BLANK_RUN.sub("\n\n", s)
    return s.strip() + "\n"
