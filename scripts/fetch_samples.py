"""
fetch_samples.py — 공고 원문 샘플을 ground_truth 디렉토리에 수집한다.

사용법:
  python -m scripts.fetch_samples --source local   --count 1 --out data/ground_truth/
  python -m scripts.fetch_samples --source bidding2 --notice-ids R26BK01482737-000 --out data/ground_truth/
  python -m scripts.fetch_samples --source bidding2 --notice-ids-file data/ground_truth_input/d5_initial_ids.txt --out data/ground_truth/
  python -m scripts.fetch_samples --source g2b      --count 1 --out data/ground_truth/
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import warnings
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────

_COLUMNS_CSV = Path(__file__).parent.parent / "data" / "columns.csv"
_TEMPLATE_DIR = Path(__file__).parent.parent / "data" / "ground_truth" / "_template"

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_id(notice_id: str) -> str:
    """파일명에 사용 불가한 문자 제거."""
    return _UNSAFE_CHARS.sub("_", notice_id)


def _load_keys() -> list[str]:
    """columns.csv에서 key 목록을 읽는다."""
    with open(_COLUMNS_CSV, "rb") as f:
        raw = f.read()
    decoded = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))
    return [row["key"].strip() for row in reader if row["key"].strip()]


def _null_expected(keys: list[str]) -> dict:
    return {
        "extracted": {k: None for k in keys},
        "source": {k: None for k in keys},
    }


def _meta_yaml(category: str = "", difficulty: int = 1, labeler: str = "") -> str:
    return (
        f'category: "{category}"\n'
        f"difficulty: {difficulty}\n"
        f'labeler: "{labeler}"\n'
        f"reviewed_at: null\n"
        f'notes: ""\n'
    )


def _make_notice_dir(out_root: Path, prefix: str, notice_id: str) -> Path:
    """출력 디렉토리를 생성하고 경로를 반환한다."""
    safe_id = _sanitize_id(notice_id)
    name = f"{prefix}_{safe_id}"
    target = out_root / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_template_files(notice_dir: Path, keys: list[str], category: str = "", labeler: str = "") -> None:
    """expected.json과 meta.yaml을 새 notice 디렉토리에 작성한다."""
    expected_path = notice_dir / "expected.json"
    meta_path = notice_dir / "meta.yaml"

    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(_null_expected(keys), f, ensure_ascii=False, indent=2)

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(_meta_yaml(category=category, labeler=labeler))


# ──────────────────────────────────────────────
# 분기별 수집 함수
# ──────────────────────────────────────────────


def fetch_local(count: int, out_root: Path) -> list[Path]:
    """로컬 samples/sample_g2b.html 을 ground_truth에 복사한다."""
    sample_src = Path(__file__).parent.parent / "samples" / "sample_g2b.html"
    if not sample_src.exists():
        print(f"[ERROR] 로컬 샘플 파일 없음: {sample_src}", file=sys.stderr)
        sys.exit(1)

    keys = _load_keys()
    created: list[Path] = []

    for i in range(1, count + 1):
        notice_id = f"local_{i:03d}"
        notice_dir = _make_notice_dir(out_root, "local", f"{i:03d}")

        dest = notice_dir / "source.html"
        shutil.copy2(sample_src, dest)
        _write_template_files(notice_dir, keys, category="g2b")

        print(f"[OK] {notice_dir.name}/ 생성 (source.html {dest.stat().st_size} bytes)")
        created.append(notice_dir)

    return created


def fetch_bidding2(
    count: int,
    out_root: Path,
    notice_ids: list[str] | None = None,
) -> list[Path]:
    """bidding2.kr API로 공고를 수집한다. (.env 필수)"""
    api_url = os.getenv("BIDDING2_API_URL")
    if not api_url:
        print(
            "[ERROR] bidding2.kr API 환경변수 미설정.\n"
            "  .env 파일에 BIDDING2_API_URL 을 추가하세요.\n"
            "  (예: BIDDING2_API_URL=https://bidding2.kr/...?gongsanum={notice_id})",
            file=sys.stderr,
        )
        sys.exit(1)

    if notice_ids is None:
        print(
            "[ERROR] bidding2 수집에는 --notice-ids 또는 --notice-ids-file 이 필요합니다.\n"
            "  목록 API가 없어 공고번호를 직접 지정해야 합니다.",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = os.getenv("BIDDING2_API_KEY", "")  # 인증 불필요 확인됨, 빈 값 OK

    return _bidding2_fetch(
        api_url=api_url,
        api_key=api_key,
        count=count,
        out_root=out_root,
        notice_ids=notice_ids,
    )


def _bidding2_fetch(
    api_url: str,
    api_key: str,
    count: int,
    out_root: Path,
    notice_ids: list[str] | None = None,
) -> list[Path]:
    """bidding2.kr API로 공고문 첨부파일 받음.

    1. notice_ids가 주어지면 그 목록 사용 (count 무시), 없으면 에러 (목록 API 미정)
    2. 각 공고번호별:
       a. api_url의 {notice_id} 치환 → GET → JSON 응답 (UTF-8)
       b. 응답에서 '공고문' 시작하는 키 중 .pdf 우선 (없으면 .hwp skip with warning)
       c. URL → g2b.go.kr GET (timeout 30s, redirects 허용, stream)
       d. {out_root}/bidding2_{notice_id}/source.pdf 저장
       e. expected.json + meta.yaml 템플릿 생성 (category="bidding2")
    3. 진행 상황 stdout
    4. 실패 케이스 stderr + 계속 진행
    5. notice_id 파일명 sanitize
    6. 응답 JSON 키 UTF-8 처리
    """
    if notice_ids is None:
        print(
            "[ERROR] notice_ids가 없습니다. 목록 API 미정 — 공고번호를 직접 지정하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    keys = _load_keys()
    created: list[Path] = []
    total = len(notice_ids)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; DingpagoEval/1.0)"})

    for idx, notice_id in enumerate(notice_ids, 1):
        notice_id = notice_id.strip()
        if not notice_id:
            continue

        print(f"[{idx}/{total}] {notice_id} 처리 중...", flush=True)

        # ── a. bidding2 API 호출 ──
        url = api_url.replace("{notice_id}", notice_id)
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[FAIL] {idx}/{total} {notice_id}: bidding2 API 오류 → {exc}", file=sys.stderr)
            continue

        # UTF-8 명시 디코딩
        resp.encoding = "utf-8"

        # 빈 응답 처리 (bidding2가 첨부파일 없는 공고에 HTTP 200 + 빈 body 반환)
        if not resp.content:
            print(
                f"[FAIL] {idx}/{total} {notice_id}: bidding2 응답 없음 "
                f"(HTTP 200 + 빈 body — 공고번호가 bidding2에 없거나 첨부파일 미등록)",
                file=sys.stderr,
            )
            continue

        try:
            file_map: dict = resp.json()
        except Exception as exc:
            print(f"[FAIL] {idx}/{total} {notice_id}: JSON 파싱 오류 → {exc}", file=sys.stderr)
            print(f"  응답 내용 앞 200자: {resp.text[:200]}", file=sys.stderr)
            continue

        if not isinstance(file_map, dict) or not file_map:
            print(f"[FAIL] {idx}/{total} {notice_id}: 첨부파일 목록이 비어있음 (응답: {resp.text[:200]})", file=sys.stderr)
            continue

        # ── b. '공고문' 키 필터링 → PDF 우선 ──
        gonggo_keys = [k for k in file_map if k.startswith("공고문")]
        if not gonggo_keys:
            # '공고문' 없으면 전체 키 중 PDF 우선
            all_keys = list(file_map.keys())
            gonggo_keys = all_keys

        pdf_key = next((k for k in gonggo_keys if k.lower().endswith(".pdf")), None)
        hwp_key = next((k for k in gonggo_keys if k.lower().endswith(".hwp")), None)

        if pdf_key:
            chosen_key = pdf_key
            chosen_ext = ".pdf"
        elif hwp_key:
            print(f"[WARN] {notice_id}: PDF 없음, HWP skip (텍스트 추출 불가)", file=sys.stderr)
            continue
        else:
            # PDF/HWP 아닌 다른 파일 (xlsx 등) — skip
            print(f"[WARN] {notice_id}: 공고문 PDF/HWP 없음, 키 목록={list(file_map.keys())[:5]}", file=sys.stderr)
            continue

        download_url: str = file_map[chosen_key]

        # ── c. g2b.go.kr에서 실제 파일 다운로드 ──
        notice_dir = _make_notice_dir(out_root, "bidding2", notice_id)
        dest_path = notice_dir / f"source{chosen_ext}"

        # 이미 다운로드된 경우 skip (비용 가드)
        if dest_path.exists() and dest_path.stat().st_size > 1024:
            file_size = dest_path.stat().st_size
            print(f"[SKIP] {idx}/{total} {notice_id} → 이미 존재 ({file_size // 1024}KB), 재다운로드 생략", flush=True)
            # template 파일이 없으면 생성
            if not (notice_dir / "expected.json").exists():
                _write_template_files(notice_dir, keys, category="bidding2")
            created.append(notice_dir)
            continue

        try:
            dl_resp = session.get(download_url, timeout=30, allow_redirects=True, stream=True)
            dl_resp.raise_for_status()
        except requests.exceptions.SSLError:
            # SSL 인증서 검증 실패 시 verify=False 재시도 (경고 억제)
            import warnings as _warnings
            try:
                with _warnings.catch_warnings():
                    _warnings.simplefilter("ignore")
                    import urllib3
                    urllib3.disable_warnings()
                dl_resp = session.get(download_url, timeout=30, allow_redirects=True, stream=True, verify=False)
                dl_resp.raise_for_status()
                print(f"[WARN] {notice_id}: SSL 검증 우회하여 다운로드 성공", file=sys.stderr)
            except Exception as exc2:
                print(f"[FAIL] {idx}/{total} {notice_id}: g2b 다운로드 오류 (SSL 우회 후에도 실패) → {exc2}", file=sys.stderr)
                if notice_dir.exists() and not any(notice_dir.iterdir()):
                    notice_dir.rmdir()
                continue
        except Exception as exc:
            print(f"[FAIL] {idx}/{total} {notice_id}: g2b 다운로드 오류 → {exc}", file=sys.stderr)
            # 빈 디렉토리 정리
            if notice_dir.exists() and not any(notice_dir.iterdir()):
                notice_dir.rmdir()
            continue

        # ── d. 파일 저장 ──
        with open(dest_path, "wb") as f:
            for chunk in dl_resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        file_size = dest_path.stat().st_size
        if file_size < 100:
            print(
                f"[WARN] {notice_id}: 다운로드된 파일이 너무 작음 ({file_size}bytes) "
                f"— 토큰 만료 또는 접근 오류일 수 있음",
                file=sys.stderr,
            )

        # ── e. template 파일 생성 ──
        _write_template_files(notice_dir, keys, category="bidding2")

        file_size_kb = file_size // 1024
        print(f"[OK] {idx}/{total} {notice_id} → {notice_dir.name}/ ({chosen_ext[1:].upper()} {file_size_kb}KB)", flush=True)
        created.append(notice_dir)

    print(f"\n수집 완료: {len(created)}/{total}건 성공")
    return created


def fetch_g2b(count: int, out_root: Path) -> list[Path]:
    """나라장터(G2B) 공고를 수집한다. (미구현 — D3 이후)"""
    print("[WARN] g2b 분기는 아직 미구현입니다. D3 세션 예정.", file=sys.stderr)
    sys.exit(1)


# ──────────────────────────────────────────────
# 공고번호 목록 로드
# ──────────────────────────────────────────────


def _load_notice_ids(ids_str: str | None, ids_file: str | None) -> list[str] | None:
    """--notice-ids 또는 --notice-ids-file에서 공고번호 목록 로드."""
    if ids_str and ids_file:
        print("[ERROR] --notice-ids와 --notice-ids-file은 동시에 사용할 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    if ids_str:
        return [x.strip() for x in ids_str.split(",") if x.strip()]

    if ids_file:
        p = Path(ids_file)
        if not p.exists():
            print(f"[ERROR] notice-ids-file 파일을 찾을 수 없습니다: {p}", file=sys.stderr)
            sys.exit(1)
        with open(p, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    return None


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="공고 샘플을 ground_truth 디렉토리에 수집")
    parser.add_argument(
        "--source",
        choices=["bidding2", "g2b", "local"],
        required=True,
        help="수집 출처 (bidding2 | g2b | local)",
    )
    parser.add_argument("--count", type=int, default=1, help="수집 건수 (기본 1, bidding2에서는 --notice-ids 우선)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/ground_truth"),
        help="저장 루트 디렉토리 (기본 data/ground_truth/)",
    )
    # bidding2 전용 공고번호 지정 옵션
    parser.add_argument(
        "--notice-ids",
        type=str,
        default=None,
        help="수집할 공고번호 목록 (콤마 구분, bidding2 전용). 예: R26BK01482737-000,R26BK01482088-000",
    )
    parser.add_argument(
        "--notice-ids-file",
        type=str,
        default=None,
        help="공고번호 목록 파일 경로 (1줄당 1개, bidding2 전용). --notice-ids와 동시 사용 불가",
    )
    args = parser.parse_args()

    out_root = args.out
    out_root.mkdir(parents=True, exist_ok=True)

    notice_ids = _load_notice_ids(args.notice_ids, args.notice_ids_file)

    if args.source == "local":
        fetch_local(args.count, out_root)
    elif args.source == "bidding2":
        fetch_bidding2(args.count, out_root, notice_ids=notice_ids)
    elif args.source == "g2b":
        fetch_g2b(args.count, out_root)


if __name__ == "__main__":
    main()
