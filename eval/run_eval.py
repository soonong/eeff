"""eval/run_eval.py — ground_truth 순회 + API 호출 + 점수 계산 + 리포트 출력.

사용법:
    python -m eval.run_eval --dataset data/ground_truth --out reports/ \\
        [--api http://localhost:8000] [--limit N] [--vcr replay|record|disabled]
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# 프로젝트 루트를 sys.path에 추가 (app 임포트용)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_columns_rules(csv_path: Path) -> list[dict]:
    """columns.csv → dict 리스트."""
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader if (row.get("key") or "").strip()]


def _load_ground_truth(gt_dir: Path) -> tuple[dict, dict, dict, str]:
    """expected.json + meta.yaml + source 파일 로드.

    반환: (extracted, source, meta, raw_text)
    """
    # expected.json
    expected_path = gt_dir / "expected.json"
    if not expected_path.exists():
        raise FileNotFoundError(f"expected.json not found: {expected_path}")
    with expected_path.open(encoding="utf-8") as f:
        data = json.load(f)
    extracted = data.get("extracted", {})
    source = data.get("source", {})

    # meta.yaml (없으면 빈 dict)
    meta: dict = {}
    meta_path = gt_dir / "meta.yaml"
    if meta_path.exists():
        try:
            import yaml  # type: ignore[import]
            with meta_path.open(encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
        except Exception:
            pass

    # source 파일 (source.html / source.txt / source.pdf 중 하나)
    raw_text = ""
    for ext in ("html", "txt", "pdf"):
        src_path = gt_dir / f"source.{ext}"
        if src_path.exists():
            try:
                raw_text = src_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            break

    return extracted, source, meta, raw_text


def _cassette_path(cassettes_dir: Path, notice_id: str) -> Path:
    return cassettes_dir / f"{notice_id}.json"


def _load_cassette(cassette_file: Path) -> dict | None:
    if not cassette_file.exists():
        return None
    with cassette_file.open(encoding="utf-8") as f:
        return json.load(f)


def _save_cassette(cassette_file: Path, response: dict) -> None:
    cassette_file.parent.mkdir(parents=True, exist_ok=True)
    with cassette_file.open("w", encoding="utf-8") as f:
        json.dump(response, f, ensure_ascii=False, indent=2)


def _call_api(api_base: str, source_path: Path) -> dict:
    """v1.0 /analyze API 호출."""
    import urllib.request

    url = f"{api_base.rstrip('/')}/analyze"
    # multipart/form-data 수동 구성
    boundary = "----EvalBoundary7f3a2b"
    file_bytes = source_path.read_bytes()
    mime = "text/html" if source_path.suffix in (".html", ".htm") else "text/plain"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{source_path.name}"\r\n'
        f"Content-Type: {mime}\r\n"
        f"\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"API call failed: {exc}") from exc


def run_eval(
    dataset_dir: Path,
    out_dir: Path,
    api_base: str = "http://localhost:8000",
    limit: int | None = None,
    vcr_mode: str = "replay",
) -> dict:
    """메인 평가 루틴. 집계 결과 dict 반환."""
    from eval.matchers import matcher_for
    from eval.scoring import aggregate, score_record
    from eval.report import write_report

    columns_csv = _ROOT / "data" / "columns.csv"
    rules = _load_columns_rules(columns_csv)

    cassettes_dir = _ROOT / "tests" / "fixtures" / "gemini_cassettes"

    # ground_truth 폴더 순회 (prefix _ 는 skip)
    gt_dirs = sorted(
        d for d in dataset_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )
    if limit:
        gt_dirs = gt_dirs[:limit]

    if not gt_dirs:
        log.warning("평가할 ground_truth 폴더가 없습니다.")
        return {}

    records: list[dict] = []

    for gt_dir in gt_dirs:
        notice_id = gt_dir.name
        log.info(f"Evaluating: {notice_id}")

        try:
            e_extracted, e_source, meta, raw_text = _load_ground_truth(gt_dir)
        except FileNotFoundError as exc:
            log.warning(f"Skip {notice_id}: {exc}")
            continue

        # source 파일 경로 결정
        src_file: Path | None = None
        for ext in ("html", "txt", "pdf"):
            p = gt_dir / f"source.{ext}"
            if p.exists():
                src_file = p
                break

        # VCR 처리
        cassette_file = _cassette_path(cassettes_dir, notice_id)
        api_response: dict | None = None

        if vcr_mode == "replay":
            api_response = _load_cassette(cassette_file)
            if api_response is None:
                log.error(
                    f"Cassette not found for {notice_id} (replay mode). "
                    "Run with --vcr record first."
                )
                # fail-fast: cassette 없으면 skip
                continue
        elif vcr_mode == "record":
            if src_file is None:
                log.warning(f"Skip {notice_id}: no source file found")
                continue
            try:
                api_response = _call_api(api_base, src_file)
                _save_cassette(cassette_file, api_response)
                log.info(f"Cassette saved: {cassette_file}")
            except RuntimeError as exc:
                log.error(f"API error for {notice_id}: {exc}")
                continue
        elif vcr_mode == "disabled":
            if src_file is None:
                log.warning(f"Skip {notice_id}: no source file found")
                continue
            try:
                api_response = _call_api(api_base, src_file)
            except RuntimeError as exc:
                log.error(f"API error for {notice_id}: {exc}")
                continue
        else:
            raise ValueError(f"Unknown vcr_mode={vcr_mode!r}")

        # cassette 형식: {"request":..., "response": {"extracted":..., "source":...}}
        # 또는 API 응답 직접: {"extracted":..., "source":..., ...}
        if "response" in api_response:
            resp_data = api_response["response"]
        else:
            resp_data = api_response

        a_extracted = resp_data.get("extracted", {})
        a_source = resp_data.get("source", {})

        # 점수 계산
        rec = score_record(
            expected=e_extracted,
            actual=a_extracted,
            rules=rules,
            raw_text=raw_text,
            expected_source=e_source,
            actual_source=a_source,
        )
        rec["notice_id"] = notice_id
        rec["expected"] = e_extracted
        rec["actual"] = a_extracted
        rec["expected_source"] = e_source
        rec["actual_source"] = a_source
        rec["meta"] = meta

        records.append(rec)
        log.info(f"  weighted_score={rec['weighted_score']:.4f}")

    if not records:
        log.error("평가 가능한 레코드가 없습니다.")
        return {}

    # 집계
    aggregated = aggregate(records, rules)
    log.info(f"weighted_avg={aggregated['weighted_avg']:.4f}")

    # 리포트 출력
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = out_dir / ts
    write_report(aggregated, records, report_dir)
    log.info(f"리포트 출력: {report_dir}")

    return aggregated


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Bid eval pipeline")
    parser.add_argument("--dataset", default="data/ground_truth", help="ground_truth 루트 경로")
    parser.add_argument("--out", default="reports", help="리포트 출력 경로")
    parser.add_argument("--api", default="http://localhost:8000", help="v1.0 API base URL")
    parser.add_argument("--limit", type=int, default=None, help="평가 건수 제한")
    parser.add_argument(
        "--vcr",
        choices=["replay", "record", "disabled"],
        default="replay",
        help="VCR 모드 (기본: replay)",
    )
    args = parser.parse_args()

    result = run_eval(
        dataset_dir=Path(args.dataset),
        out_dir=Path(args.out),
        api_base=args.api,
        limit=args.limit,
        vcr_mode=args.vcr,
    )

    if result:
        print(f"\n=== 평가 완료 ===")
        print(f"weighted_avg: {result.get('weighted_avg', 0.0):.4f}")
        print(f"source_grounding_avg: {result.get('source_grounding_avg', 0.0):.4f}")
        print(f"hallucination_rate: {result.get('hallucination_rate', 0.0):.4f}")
        top10 = result.get("top10_failures", [])
        if top10:
            print("\nTop10 실패 필드:")
            for item in top10:
                print(f"  {item['field']}: {item['accuracy']:.4f} ({item['mismatch']}/{item['total']} mismatch)")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
