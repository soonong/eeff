"""
redact.py — 공고 원문에서 개인정보를 마스킹한다.

사용법:
  python -m scripts.redact --in path/to/source.html --out path/to/redacted.html
  python -m scripts.redact --in data/ground_truth/g2b_001/ --out data/ground_truth/g2b_001_redacted/
  python -m scripts.redact --in path/to/source.txt --inplace
"""

from __future__ import annotations

import argparse
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

# ──────────────────────────────────────────────
# 패턴 정의
# ──────────────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (
        "mobile_phone",
        re.compile(r"01[016789]-?\d{3,4}-?\d{4}"),
        "[REDACTED_PHONE]",
    ),
    (
        "landline_phone",
        re.compile(r"0\d{1,2}-?\d{3,4}-?\d{4}"),
        "[REDACTED_PHONE]",
    ),
    (
        "email",
        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        "[REDACTED_EMAIL]",
    ),
    (
        "name_1",
        re.compile(r"담당자[\s:]*([가-힣]{2,4})"),
        "담당자 [REDACTED_NAME]",
    ),
    (
        "name_2",
        re.compile(r"담당[\s:]*([가-힣]{2,4})"),
        "담당 [REDACTED_NAME]",
    ),
]

# 처리 대상 파일 확장자
_TEXT_EXTS = {".txt", ".html", ".htm", ".json", ".yaml", ".yml", ".md", ".csv"}
_SKIP_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".hwp", ".docx"}


# ──────────────────────────────────────────────
# 통계
# ──────────────────────────────────────────────


@dataclass
class RedactStats:
    files_processed: int = 0
    files_skipped: int = 0
    match_counts: dict[str, int] = field(default_factory=dict)

    def add(self, other: "RedactStats") -> None:
        self.files_processed += other.files_processed
        self.files_skipped += other.files_skipped
        for pattern_name, cnt in other.match_counts.items():
            self.match_counts[pattern_name] = self.match_counts.get(pattern_name, 0) + cnt

    def print_summary(self) -> None:
        print(f"\n[redact] 처리 완료")
        print(f"  파일 처리: {self.files_processed}건")
        print(f"  파일 skip: {self.files_skipped}건")
        print(f"  패턴별 매치 횟수:")
        for name, cnt in self.match_counts.items():
            print(f"    {name}: {cnt}회")
        total = sum(self.match_counts.values())
        print(f"  총 마스킹: {total}건")


# ──────────────────────────────────────────────
# 핵심 마스킹 함수
# ──────────────────────────────────────────────


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """텍스트에서 개인정보 패턴을 마스킹하고 통계를 반환한다.

    mobile_phone을 landline_phone보다 먼저 적용해야
    010-xxxx-xxxx 같은 번호가 landline 패턴에도 걸리지 않도록 한다.
    """
    counts: dict[str, int] = {}
    result = text

    for pattern_name, pattern, replacement in _PATTERNS:
        matches = pattern.findall(result)
        counts[pattern_name] = len(matches)
        result = pattern.sub(replacement, result)

    return result, counts


def redact_file(src: Path, dst: Path) -> RedactStats:
    """단일 파일을 마스킹한다. dst == src이면 inplace."""
    ext = src.suffix.lower()
    stats = RedactStats()

    if ext in _SKIP_EXTS:
        warnings.warn(f"[SKIP] 바이너리/PDF 파일은 처리 불가: {src}", stacklevel=2)
        stats.files_skipped += 1
        return stats

    if ext not in _TEXT_EXTS and ext != "":
        # 확장자 없는 파일도 텍스트로 시도
        pass

    try:
        content = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = src.read_text(encoding="cp949")
        except Exception as e:
            warnings.warn(f"[SKIP] 파일 읽기 실패 ({e}): {src}", stacklevel=2)
            stats.files_skipped += 1
            return stats

    redacted, counts = redact_text(content)
    stats.match_counts = counts
    stats.files_processed += 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(redacted, encoding="utf-8")

    return stats


def redact_dir(src_dir: Path, dst_dir: Path) -> RedactStats:
    """디렉토리를 재귀적으로 마스킹한다."""
    total = RedactStats()

    for src_file in sorted(src_dir.rglob("*")):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src_dir)
        dst_file = dst_dir / rel
        stats = redact_file(src_file, dst_file)
        total.add(stats)

    return total


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="공고 원문 개인정보 마스킹")
    parser.add_argument("--in", dest="src", type=Path, required=True, help="입력 파일 또는 디렉토리")
    parser.add_argument(
        "--out",
        dest="dst",
        type=Path,
        default=None,
        help="출력 파일 또는 디렉토리 (--inplace와 함께 사용 불가)",
    )
    parser.add_argument(
        "--inplace",
        action="store_true",
        help="입력 파일을 직접 덮어쓴다",
    )
    args = parser.parse_args()

    src: Path = args.src
    inplace: bool = args.inplace
    dst: Path | None = args.dst

    if inplace and dst is not None:
        print("[ERROR] --inplace 와 --out 은 함께 쓸 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    if not inplace and dst is None:
        print("[ERROR] --out 또는 --inplace 중 하나를 지정하세요.", file=sys.stderr)
        sys.exit(1)

    if not src.exists():
        print(f"[ERROR] 입력 경로가 존재하지 않음: {src}", file=sys.stderr)
        sys.exit(1)

    if inplace:
        dst = src  # 같은 경로로 덮어씀

    if src.is_dir():
        out_dir = dst if dst != src else src
        stats = redact_dir(src, out_dir)
    else:
        out_file = dst if dst != src else src
        stats = redact_file(src, out_file)

    stats.print_summary()


if __name__ == "__main__":
    main()
