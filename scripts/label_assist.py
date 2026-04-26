"""
label_assist.py — ground_truth 디렉토리의 source 파일을 v1.0 /analyze로 돌려
expected.json 초안을 자동 생성한다. 사람은 diff만 검수.

사용법:
  python -m scripts.label_assist --notice-dir data/ground_truth/local_001
  python -m scripts.label_assist --notice-dir data/ground_truth/local_001 --api http://localhost:8000 --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
import yaml

# source 파일 탐색 우선순위
_SOURCE_PRIORITY = [".html", ".pdf", ".txt"]


def _find_source(notice_dir: Path) -> Path | None:
    for ext in _SOURCE_PRIORITY:
        candidate = notice_dir / f"source{ext}"
        if candidate.exists():
            return candidate
    return None


_MAX_DIRECT_BYTES = 9 * 1024 * 1024  # 9MB 초과 시 텍스트 추출 후 전송
_ANALYZE_TIMEOUT = 300  # 초


def _call_analyze(api_base: str, source_path: Path) -> dict:
    url = f"{api_base.rstrip('/')}/analyze"

    # 대용량 PDF → 텍스트 추출 후 txt로 전송
    file_bytes = source_path.read_bytes()
    if source_path.suffix.lower() == ".pdf" and len(file_bytes) > _MAX_DIRECT_BYTES:
        try:
            import sys as _sys
            _root = Path(__file__).resolve().parent.parent
            if str(_root) not in _sys.path:
                _sys.path.insert(0, str(_root))
            from app.preprocess import pdf_to_text
            text = pdf_to_text(file_bytes, max_pages=5)
            file_bytes = text.encode("utf-8")
            upload_name = source_path.stem + "_extracted.txt"
            mime = "text/plain"
            print(f"[INFO] 대용량 PDF ({source_path.stat().st_size // 1024 // 1024}MB) → 텍스트 추출 ({len(text)} chars)")
        except Exception as exc:
            print(f"[ERROR] PDF 텍스트 추출 실패: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        upload_name = source_path.name
        mime = None  # requests가 자동 결정

    try:
        if mime:
            resp = requests.post(
                url,
                files={"file": (upload_name, file_bytes, mime)},
                timeout=_ANALYZE_TIMEOUT,
            )
        else:
            resp = requests.post(
                url,
                files={"file": (upload_name, file_bytes)},
                timeout=_ANALYZE_TIMEOUT,
            )
    except requests.exceptions.ConnectionError:
        print(
            f"[ERROR] API 서버에 연결할 수 없습니다: {api_base}\n"
            "  서버가 실행 중인지 확인하세요 (예: uvicorn app.main:app --port 8000)",
            file=sys.stderr,
        )
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(
            f"[ERROR] API 응답 시간 초과 ({_ANALYZE_TIMEOUT}초). 서버 부하를 확인하세요: {api_base}",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code != 200:
        print(
            f"[ERROR] API 응답 오류 (HTTP {resp.status_code}): {resp.text[:500]}",
            file=sys.stderr,
        )
        sys.exit(1)

    return resp.json()


def _map_to_expected(api_response: dict) -> dict:
    """AnalyzeResponse → expected.json 포맷으로 매핑."""
    return {
        "extracted": api_response.get("extracted", {}),
        "source": api_response.get("source", {}),
    }


def _update_meta(meta_path: Path) -> None:
    """meta.yaml의 labeler와 reviewed_at을 label_assist 초안 상태로 갱신."""
    if not meta_path.exists():
        return
    with open(meta_path, encoding="utf-8") as f:
        content = f.read()

    # 단순 문자열 치환 (PyYAML dump 시 코멘트 손실 방지)
    lines = content.splitlines()
    updated = []
    for line in lines:
        if line.startswith("labeler:"):
            updated.append('labeler: "label_assist"')
        elif line.startswith("reviewed_at:"):
            updated.append("reviewed_at: null")
        else:
            updated.append(line)

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="v1.0 /analyze를 호출하여 expected.json 초안을 자동 생성"
    )
    parser.add_argument(
        "--notice-dir",
        type=Path,
        required=True,
        help="대상 ground_truth 공고 디렉토리 (예: data/ground_truth/local_001)",
    )
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="v1.0 API 베이스 URL (기본: http://localhost:8000)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="기존 expected.json 덮어쓰기 허용",
    )
    args = parser.parse_args()

    notice_dir: Path = args.notice_dir.resolve()

    if not notice_dir.is_dir():
        print(f"[ERROR] 디렉토리가 존재하지 않습니다: {notice_dir}", file=sys.stderr)
        sys.exit(1)

    source_path = _find_source(notice_dir)
    if source_path is None:
        print(
            f"[ERROR] source 파일을 찾을 수 없습니다: {notice_dir}\n"
            f"  지원 파일명: source.html / source.pdf / source.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    expected_path = notice_dir / "expected.json"
    if expected_path.exists() and not args.overwrite:
        print(
            f"[ERROR] expected.json이 이미 존재합니다: {expected_path}\n"
            "  덮어쓰려면 --overwrite 플래그를 추가하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[INFO] source: {source_path.name}")
    print(f"[INFO] API: {args.api}")
    sys.stdout.flush()
    print("[INFO] /analyze 호출 중...")
    sys.stdout.flush()

    api_response = _call_analyze(args.api, source_path)
    expected = _map_to_expected(api_response)

    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(expected, f, ensure_ascii=False, indent=2)

    meta_path = notice_dir / "meta.yaml"
    _update_meta(meta_path)

    print(f"[OK] expected.json 초안 생성 완료: {expected_path}")
    print(f"[OK] meta.yaml 갱신 (labeler: label_assist, reviewed_at: null)")
    print("[INFO] 이제 expected.json을 직접 검수하세요.")


if __name__ == "__main__":
    main()
