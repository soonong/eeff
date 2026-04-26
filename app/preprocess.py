from __future__ import annotations

import io
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "iframe")
_BLANK_RUN = re.compile(r"\n[ \t]*\n[ \t]*\n+")
_TRAILING_WS = re.compile(r"[ \t]+\n")

# PDF 앞 10페이지까지 추출 (공고명/발주처/공고번호가 뒤 페이지에 있는 케이스 대응)
_PDF_MAX_PAGES = 10


def pdf_to_text(pdf_bytes: bytes, max_pages: int = _PDF_MAX_PAGES) -> str:
    """PDF 바이트에서 텍스트 추출 (앞 N페이지만).

    pdfplumber 사용. 표 구조·다단 레이아웃 추출 강화.
    - page.extract_text()로 본문 추출
    - page.extract_tables()로 표 구조를 Markdown 형태로 추가
    추출 텍스트가 비어있으면 경고 출력 (이미지 PDF 가능성).
    """
    try:
        import pdfplumber  # type: ignore[import]
    except ImportError:
        raise ImportError("pdfplumber 패키지가 필요합니다: pip install pdfplumber>=0.11")

    try:
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = pdf.pages[:max_pages]
            for page in pages:
                # 본문 텍스트 추출
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                if text.strip():
                    parts.append(text)

                # 표 구조 추출 → Markdown 변환
                try:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        md_rows: list[str] = []
                        for i, row in enumerate(table):
                            cells = [str(c or "").replace("|", "\\|").replace("\n", " ") for c in row]
                            md_rows.append("| " + " | ".join(cells) + " |")
                            if i == 0:
                                md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
                        if md_rows:
                            parts.append("\n".join(md_rows))
                except Exception:
                    pass  # 표 추출 실패 시 본문만 사용

        result = "\n\n".join(parts)
        if not result.strip():
            print(
                "[WARNING] PDF 텍스트 추출 결과가 비어 있습니다. "
                "이미지 기반 PDF이거나 OCR이 필요한 파일일 수 있습니다.",
                file=sys.stderr,
            )
            return ""
        return _collapse_whitespace(result)
    except Exception as exc:
        return f"[PDF 추출 오류: {exc}]"


def file_to_text(source_path: Path) -> str:
    """source.html / source.txt / source.pdf 를 받아 텍스트(마크다운) 반환."""
    suffix = source_path.suffix.lower()
    if suffix in (".html", ".htm"):
        html = source_path.read_text(encoding="utf-8", errors="replace")
        return html_to_markdown(html)
    elif suffix == ".pdf":
        pdf_bytes = source_path.read_bytes()
        return pdf_to_text(pdf_bytes)
    elif suffix == ".txt":
        return source_path.read_text(encoding="utf-8", errors="replace")
    else:
        return source_path.read_text(encoding="utf-8", errors="replace")


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
