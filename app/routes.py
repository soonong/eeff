from __future__ import annotations

import csv
import io
import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import gemini_client, preprocess, storage
from .adapter_dream import to_dream_format
from .rules import load_rules
from .schemas import AnalyzeResponse
from .validator import validate

log = logging.getLogger(__name__)
router = APIRouter()

MAX_FILE_BYTES = 10 * 1024 * 1024
SUPPORTED_HTML_TYPES = {"text/html", "application/xhtml+xml"}
SUPPORTED_PDF_TYPES = {"application/pdf"}

_templates: Jinja2Templates | None = None

_COLUMNS_CSV = Path(__file__).resolve().parent.parent / "data" / "columns.csv"


@lru_cache(maxsize=1)
def _load_required_keys() -> frozenset[str]:
    """columns.csv에서 required=true 키만 추출 (한 번 캐시)."""
    with _COLUMNS_CSV.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return frozenset(
            row["key"].strip()
            for row in reader
            if row.get("key", "").strip()
            and (row.get("required") or "").strip().lower() in {"true", "1", "yes", "y"}
        )


def get_templates(request: Request) -> Jinja2Templates:
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(directory="templates")
    return _templates


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    templates = get_templates(request)
    return templates.TemplateResponse(request, "index.html", {})


@router.post("/analyze", response_class=JSONResponse)
async def analyze(
    file: UploadFile | None = None,
    url: Annotated[str | None, Form()] = None,
    format: Annotated[str | None, Query(alias="format")] = None,
) -> JSONResponse:
    if file is None and not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="file 또는 url 중 하나는 필수입니다.")

    file_name: str
    payload: bytes
    content_type: str

    if file is not None:
        payload = await file.read()
        if len(payload) > MAX_FILE_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="파일 크기는 10MB를 초과할 수 없습니다.")
        file_name = file.filename or "upload"
        content_type = (file.content_type or "").lower()
    else:
        payload, content_type = await _fetch_url(url)  # type: ignore[arg-type]
        file_name = url  # type: ignore[assignment]

    text = _to_text(file_name, payload, content_type)
    if not text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="입력에서 추출 가능한 텍스트가 없습니다.")

    rules = load_rules()
    try:
        raw = gemini_client.extract(text, rules)
    except RuntimeError as exc:
        log.exception("Gemini extraction failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Gemini 호출 실패: {exc}") from exc

    result = validate(raw, rules)
    usage = gemini_client.last_usage()

    issues_payload = [issue.model_dump() for issue in result.issues]
    storage.save_analysis(file_name, result.extracted, result.source, issues_payload, usage)

    # ?format=dream → 꿈자동화 F02 ai_columns 행 리스트 반환
    if format == "dream":
        required_keys = set(_load_required_keys())
        dream_rows = to_dream_format(result, required_keys=required_keys)
        return JSONResponse(dream_rows)

    response = AnalyzeResponse(
        file_name=file_name,
        char_count=len(text),
        extracted=result.extracted,
        source=result.source,
        issues=result.issues,
        usage=usage,
    )
    return JSONResponse(response.model_dump())


def _to_text(file_name: str, payload: bytes, content_type: str) -> str:
    name = file_name.lower()
    if content_type in SUPPORTED_HTML_TYPES or name.endswith((".html", ".htm")):
        html = _decode(payload)
        return preprocess.html_to_markdown(html)
    if content_type in SUPPORTED_PDF_TYPES or name.endswith(".pdf"):
        return _pdf_to_text(payload)
    if content_type.startswith("text/") or name.endswith(".txt"):
        return _decode(payload)
    raise HTTPException(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=f"지원하지 않는 형식입니다: content_type={content_type}, name={file_name}",
    )


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _pdf_to_text(payload: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(parts)


async def _fetch_url(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        try:
            r = await client.get(url)
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"URL 요청 실패: {exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"URL 응답 오류: {r.status_code}")
    if len(r.content) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="URL 컨텐츠가 10MB를 초과합니다.")
    return r.content, (r.headers.get("content-type") or "").split(";")[0].strip().lower()
