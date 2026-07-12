#!/usr/bin/env python3
"""
HF 데이터셋 다운로드: nvidia/Nemotron-Personas-Korea

기능:
- Hugging Face Hub에서 페르소나 레코드 로드
- 로컬 JSON(personas_raw.json)으로 저장

사용 예시:
python day0_download_personas.py \
  --limit 50 \
  --output ../data/personas_raw.json

참고:
- 전체 데이터셋은 약 100만 건(2GB)이므로 실습에서는 streaming=True와 --limit으로
  필요한 샘플만 읽습니다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any

from datasets import load_dataset


HF_DATASET = "nvidia/Nemotron-Personas-Korea"
HF_CONFIG = "default"
HF_SPLIT = "train"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nemotron-Personas-Korea HF 다운로드 스크립트")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="다운로드할 레코드 수 (기본값: 50)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../data/personas_raw.json"),
        help="저장할 JSON 경로 (기본값: ../data/personas_raw.json)",
    )
    parser.add_argument(
        "--dataset",
        default=HF_DATASET,
        help=f"Hugging Face dataset ID (기본값: {HF_DATASET})",
    )
    return parser.parse_args()


def fetch_records(dataset_id: str, limit: int) -> list[dict[str, Any]]:
    """
    HF datasets로 지정 개수만큼 레코드를 로드합니다.

    학습 포인트:
        - streaming=True: 전체 parquet shard를 로컬에 내려받지 않고 앞 N건만 순차 읽기
        - islice(dataset, limit): 스트리밍 데이터에서 실습용 샘플 개수만 자르기
        - dict(row): datasets Row 객체를 일반 dict로 변환해 JSON 직렬화 가능하게 함
    """
    if limit <= 0:
        raise ValueError("--limit 값은 1 이상이어야 합니다.")

    dataset = load_dataset(
        dataset_id,
        name=HF_CONFIG,
        split=HF_SPLIT,
        streaming=True,
    )

    records: list[dict[str, Any]] = []
    for row in islice(dataset, limit):
        records.append(dict(row))
    return records


def build_payload(dataset_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source": dataset_id,
        "hf_config": HF_CONFIG,
        "hf_split": HF_SPLIT,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "records": records,
    }


def save_json(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    records = fetch_records(dataset_id=args.dataset, limit=args.limit)
    payload = build_payload(dataset_id=args.dataset, records=records)
    save_json(payload, args.output)

    print(f"[완료] {len(records)}건 다운로드 -> {args.output}")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
