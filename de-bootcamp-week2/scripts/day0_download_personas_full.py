#!/usr/bin/env python3
"""
HF 데이터셋 전체 다운로드: nvidia/Nemotron-Personas-Korea

기능:
- Hugging Face Hub 데이터셋을 streaming=True로 순차 로드
- 전체 레코드를 메모리에 쌓지 않고 JSONL로 한 줄씩 저장
- 중간 진행 상황을 출력하고, 선택적으로 기존 JSONL에 이어받기 가능

사용 예시:
python scripts/day0_download_personas_full.py \
  --output data/personas_full.jsonl

이어받기:
python scripts/day0_download_personas_full.py \
  --output data/personas_full.jsonl \
  --resume

주의:
- 전체 데이터셋은 크기가 크므로 충분한 디스크 용량과 시간이 필요합니다.
- 수업 실습 파이프라인은 day0_download_personas.py의 샘플 JSON을 사용합니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Iterable

from datasets import load_dataset


HF_DATASET = "nvidia/Nemotron-Personas-Korea"
HF_CONFIG = "default"
HF_SPLIT = "train"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nemotron-Personas-Korea 전체 JSONL 다운로드")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/personas_full.jsonl"),
        help="저장할 JSONL 경로 (기본값: data/personas_full.jsonl)",
    )
    parser.add_argument(
        "--dataset",
        default=HF_DATASET,
        help=f"Hugging Face dataset ID (기본값: {HF_DATASET})",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="테스트용 최대 저장 건수 (기본값: 제한 없음)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10_000,
        help="진행 상황 출력 주기 (기본값: 10000)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="이미 존재하는 JSONL 파일의 줄 수만큼 건너뛰고 이어받기",
    )
    return parser.parse_args()


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0

    count = 0
    with path.open("rb") as f:
        for _ in f:
            count += 1
    return count


def load_stream(dataset_id: str) -> Iterable[dict[str, Any]]:
    return load_dataset(
        dataset_id,
        name=HF_CONFIG,
        split=HF_SPLIT,
        streaming=True,
    )


def write_jsonl(
    rows: Iterable[dict[str, Any]],
    output_path: Path,
    progress_every: int,
    append: bool,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    written = 0

    with output_path.open(mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
            written += 1

            if progress_every > 0 and written % progress_every == 0:
                f.flush()
                print(f"[진행] {written:,}건 저장 중 -> {output_path}", flush=True)

    return written


def write_metadata(
    output_path: Path,
    dataset_id: str,
    total_records: int,
    skipped_records: int,
) -> None:
    metadata = {
        "source": dataset_id,
        "hf_config": HF_CONFIG,
        "hf_split": HF_SPLIT,
        "format": "jsonl",
        "output": str(output_path),
        "record_count": total_records,
        "skipped_records_on_resume": skipped_records,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()

    if args.max_records is not None and args.max_records <= 0:
        raise ValueError("--max-records 값은 1 이상이어야 합니다.")

    if args.progress_every <= 0:
        raise ValueError("--progress-every 값은 1 이상이어야 합니다.")

    skipped_records = count_lines(args.output) if args.resume else 0
    if skipped_records > 0:
        print(f"[이어받기] 기존 {skipped_records:,}건 건너뜀: {args.output}", flush=True)

    stream = load_stream(args.dataset)
    rows = islice(stream, skipped_records, None)
    if args.max_records is not None:
        rows = islice(rows, args.max_records)

    written = write_jsonl(
        rows=rows,
        output_path=args.output,
        progress_every=args.progress_every,
        append=args.resume and skipped_records > 0,
    )
    total_records = skipped_records + written
    write_metadata(
        output_path=args.output,
        dataset_id=args.dataset,
        total_records=total_records,
        skipped_records=skipped_records,
    )

    print(f"[완료] 이번 실행 {written:,}건 저장, 총 {total_records:,}건 -> {args.output}")


if __name__ == "__main__":
    main()
