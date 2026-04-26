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


_MAX_API_BYTES = 10 * 1024 * 1024  # 서버 10MB 제한


def _call_api(api_base: str, source_path: Path) -> dict:
    """v1.0 /analyze API 호출.

    PDF 파일이 10MB 초과인 경우 앞 2페이지만 텍스트 추출 후 txt로 전송.
    """
    import urllib.request

    url = f"{api_base.rstrip('/')}/analyze"
    boundary = "----EvalBoundary7f3a2b"

    suffix = source_path.suffix.lower()
    file_bytes = source_path.read_bytes()

    if suffix == ".pdf" and len(file_bytes) > _MAX_API_BYTES:
        # 대용량 PDF → 앞 2페이지 텍스트 추출 후 txt로 전송
        try:
            _ROOT_inner = Path(__file__).resolve().parent.parent
            import sys as _sys
            if str(_ROOT_inner) not in _sys.path:
                _sys.path.insert(0, str(_ROOT_inner))
            from app.preprocess import pdf_to_text
            text = pdf_to_text(file_bytes, max_pages=2)
            file_bytes = text.encode("utf-8")
            upload_name = source_path.stem + "_p2.txt"
            mime = "text/plain"
            log.info(f"PDF 대용량({source_path.stat().st_size // 1024 // 1024}MB) → 앞 2페이지 텍스트 추출 ({len(text)} chars)")
        except Exception as exc:
            raise RuntimeError(f"PDF 텍스트 추출 실패: {exc}") from exc
    elif suffix in (".html", ".htm"):
        upload_name = source_path.name
        mime = "text/html"
    elif suffix == ".pdf":
        upload_name = source_path.name
        mime = "application/pdf"
    else:
        upload_name = source_path.name
        mime = "text/plain"

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{upload_name}"\r\n'
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
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"API call failed: {exc}") from exc


def _is_reviewed(meta: dict) -> bool:
    """reviewed_at이 null/빈값이면 미검수(False). 채워진 경우만 True."""
    reviewed_at = meta.get("reviewed_at")
    if reviewed_at is None:
        return False
    if isinstance(reviewed_at, str) and reviewed_at.strip().lower() in ("", "null", "none"):
        return False
    return True


def _save_raw_response(raw_dir: Path, notice_id: str, api_response: dict) -> None:
    """raw_responses/ 디렉토리에 각 공고의 실제 응답 JSON 저장."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"{notice_id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(api_response, f, ensure_ascii=False, indent=2)


def run_eval(
    dataset_dir: Path,
    out_dir: Path,
    api_base: str = "http://localhost:8000",
    limit: int | None = None,
    vcr_mode: str = "replay",
) -> dict:
    """메인 평가 루틴. 집계 결과 dict 반환.

    reviewed_at=null인 expected.json은 채점에서 제외.
    record 모드에서는 모든 공고의 raw 응답을 raw_responses/ 에 보관.
    """
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
    skipped_unreviewed: list[str] = []
    raw_responses: dict[str, dict] = {}

    # record 모드면 report_dir 미리 결정 (raw_responses 저장용)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = out_dir / ts

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
                continue
        elif vcr_mode == "record":
            if src_file is None:
                log.warning(f"Skip {notice_id}: no source file found")
                continue
            # cassette 이미 존재하면 재호출 금지 (비용 가드)
            existing = _load_cassette(cassette_file)
            if existing is not None:
                log.info(f"Cassette exists, skip API call: {notice_id}")
                api_response = existing
            else:
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

        # raw 응답 보관 (record/disabled 모드)
        if vcr_mode in ("record", "disabled") and api_response:
            raw_responses[notice_id] = api_response

        # cassette 형식: {"request":..., "response": {"extracted":..., "source":...}}
        # 또는 API 응답 직접: {"extracted":..., "source":..., ...}
        if "response" in api_response:
            resp_data = api_response["response"]
        else:
            resp_data = api_response

        a_extracted = resp_data.get("extracted", {})
        a_source = resp_data.get("source", {})

        # reviewed_at=null이면 채점 제외 (label_assist 초안 상태)
        if not _is_reviewed(meta):
            log.info(f"  [채점 제외] {notice_id}: reviewed_at=null (미검수 상태)")
            skipped_unreviewed.append(notice_id)
            continue

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

    if skipped_unreviewed:
        log.info(f"미검수(reviewed_at=null) 제외: {len(skipped_unreviewed)}건 — {skipped_unreviewed[:5]}{'...' if len(skipped_unreviewed) > 5 else ''}")

    # raw_responses 저장
    if raw_responses:
        raw_dir = report_dir / "raw_responses"
        for nid, resp in raw_responses.items():
            _save_raw_response(raw_dir, nid, resp)
        log.info(f"raw_responses 저장: {len(raw_responses)}건 → {raw_dir}")

    if not records:
        log.warning("채점 가능한 레코드가 없습니다 (모두 미검수 또는 source 없음).")
        # 리포트 디렉토리만 생성하고 빈 결과 반환
        if raw_responses:
            log.info("raw_responses는 저장됨. CEO 검수 후 reviewed_at 채우면 채점 가능.")
        return {"skipped_unreviewed": len(skipped_unreviewed), "raw_responses_saved": len(raw_responses)}

    # 집계
    aggregated = aggregate(records, rules)
    log.info(f"weighted_avg={aggregated['weighted_avg']:.4f}")
    aggregated["skipped_unreviewed"] = len(skipped_unreviewed)
    aggregated["raw_responses_saved"] = len(raw_responses)

    # 리포트 출력
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
        skipped = result.get("skipped_unreviewed", 0)
        raw_saved = result.get("raw_responses_saved", 0)
        if skipped:
            print(f"미검수 제외: {skipped}건 (reviewed_at=null)")
        if raw_saved:
            print(f"raw_responses 저장: {raw_saved}건")
        if "weighted_avg" in result:
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
