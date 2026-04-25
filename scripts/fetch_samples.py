"""
fetch_samples.py — 공고 원문 샘플을 ground_truth 디렉토리에 수집한다.

사용법:
  python -m scripts.fetch_samples --source local   --count 1 --out data/ground_truth/
  python -m scripts.fetch_samples --source bidding2 --count 5 --out data/ground_truth/
  python -m scripts.fetch_samples --source g2b      --count 1 --out data/ground_truth/
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────

_COLUMNS_CSV = Path(__file__).parent.parent / "data" / "columns.csv"
_TEMPLATE_DIR = Path(__file__).parent.parent / "data" / "ground_truth" / "_template"


def _load_keys() -> list[str]:
    """columns.csv에서 49개(+) key 목록을 읽는다."""
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


def _meta_yaml(category: str = "", difficulty: int = 1) -> str:
    return (
        f'category: "{category}"\n'
        f"difficulty: {difficulty}\n"
        f'labeler: ""\n'
        f"reviewed_at: null\n"
        f'notes: ""\n'
    )


def _make_notice_dir(out_root: Path, prefix: str, notice_id: str) -> Path:
    """출력 디렉토리를 생성하고 경로를 반환한다."""
    name = f"{prefix}_{notice_id}"
    target = out_root / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_template_files(notice_dir: Path, keys: list[str], category: str = "") -> None:
    """expected.json과 meta.yaml을 새 notice 디렉토리에 작성한다."""
    expected_path = notice_dir / "expected.json"
    meta_path = notice_dir / "meta.yaml"

    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(_null_expected(keys), f, ensure_ascii=False, indent=2)

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(_meta_yaml(category=category))


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


def fetch_bidding2(count: int, out_root: Path) -> list[Path]:
    """bidding2.kr A2 API로 공고를 수집한다. (.env 필수)"""
    api_url = os.getenv("BIDDING2_API_URL")
    api_key = os.getenv("BIDDING2_API_KEY")

    if not api_url or not api_key:
        print(
            "[ERROR] bidding2.kr API 환경변수 미설정.\n"
            "  .env 파일에 BIDDING2_API_URL 과 BIDDING2_API_KEY 를 추가하세요.\n"
            "  (참고: .env.example)",
            file=sys.stderr,
        )
        sys.exit(1)

    # 실제 API 호출 인터페이스 — 환경변수 확보 후 구현
    _bidding2_fetch(api_url=api_url, api_key=api_key, count=count, out_root=out_root)
    return []


def _bidding2_fetch(
    api_url: str,
    api_key: str,
    count: int,
    out_root: Path,
) -> list[Path]:
    """bidding2.kr A2 API 호출 시그니처. D2 이후 구현 예정.

    Args:
        api_url: BIDDING2_API_URL 환경변수 값
        api_key: BIDDING2_API_KEY 환경변수 값
        count: 수집할 공고 건수
        out_root: 저장 루트 디렉토리

    Returns:
        생성된 notice 디렉토리 경로 목록
    """
    raise NotImplementedError(
        "bidding2.kr API 연동은 D2 세션에서 구현 예정입니다."
    )


def fetch_g2b(count: int, out_root: Path) -> list[Path]:
    """나라장터(G2B) 공고를 수집한다. (미구현 — D3 이후)"""
    print("[WARN] g2b 분기는 아직 미구현입니다. D3 세션 예정.", file=sys.stderr)
    sys.exit(1)


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
    parser.add_argument("--count", type=int, default=1, help="수집 건수 (기본 1)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/ground_truth"),
        help="저장 루트 디렉토리 (기본 data/ground_truth/)",
    )
    args = parser.parse_args()

    out_root = args.out
    out_root.mkdir(parents=True, exist_ok=True)

    if args.source == "local":
        fetch_local(args.count, out_root)
    elif args.source == "bidding2":
        fetch_bidding2(args.count, out_root)
    elif args.source == "g2b":
        fetch_g2b(args.count, out_root)


if __name__ == "__main__":
    main()
