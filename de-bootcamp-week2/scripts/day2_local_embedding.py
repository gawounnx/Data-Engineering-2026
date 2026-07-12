#!/usr/bin/env python3
"""
2일차 실습: 청킹 텍스트 로컬 임베딩 생성

기능:
- day1 청킹 JSON 로드
- sentence-transformers 모델로 CPU 임베딩 생성
- 텍스트/벡터/메타데이터를 하나의 JSON으로 저장

사용 예시:
python day2_local_embedding.py \
  --input ../data/chunks.json \
  --output ../data/embeddings.json \
  --model kekeappa/kor-static-embedding-128 \
  --batch-size 16
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sentence_transformers import SentenceTransformer


DEFAULT_MODEL = "kekeappa/kor-static-embedding-128"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="로컬 임베딩 생성 스크립트")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("../data/chunks.json"),
        help="청킹 결과 JSON 경로",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../data/embeddings.json"),
        help="임베딩 결과 저장 경로",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"sentence-transformers 모델명 (기본값: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="임베딩 배치 크기 (기본값: 16)",
    )
    return parser.parse_args()


def load_chunks(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("청킹 JSON 루트는 객체(dict)여야 합니다.")

    chunks = payload.get("chunks", [])
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("chunks 필드가 비어 있거나 리스트가 아닙니다.")
    return payload


def build_embedding_records(
    chunks: list[dict[str, Any]],
    model: SentenceTransformer,
    batch_size: int,
) -> list[dict[str, Any]]:
    """
    청크 텍스트를 배치 단위로 임베딩하고 결과 레코드를 구성합니다.

    학습 포인트:
        - model.encode(): 텍스트 리스트를 고차원 벡터로 변환
        - convert_to_numpy=True: 이후 JSON 직렬화를 위해 numpy 배열로 반환
        - normalize_embeddings=True: 코사인 유사도 검색 안정성을 위해 L2 정규화
    """
    if batch_size <= 0:
        raise ValueError("--batch-size 값은 1 이상이어야 합니다.")

    texts = [str(chunk["text"]) for chunk in chunks]
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )

    records: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        records.append(
            {
                "chunk_id": chunk["chunk_id"],
                "persona_id": chunk["persona_id"],
                "chunk_index": chunk["chunk_index"],
                "text": chunk["text"],
                "embedding": vector.tolist(),
                "metadata": chunk.get("metadata", {}),
            }
        )
    return records


def validate_embedding_payload(
    chunk_payload: dict[str, Any],
    output_payload: dict[str, Any],
) -> None:
    chunks = chunk_payload["chunks"]
    records = output_payload["records"]
    embedding_dim = output_payload["embedding_dim"]

    expected_count = chunk_payload.get("chunk_count", len(chunks))
    if output_payload["record_count"] != expected_count:
        raise ValueError(
            f"record_count 불일치: embeddings={output_payload['record_count']}, "
            f"chunks={expected_count}"
        )
    if output_payload["record_count"] != len(records):
        raise ValueError(
            f"record_count 불일치: 메타데이터={output_payload['record_count']}, "
            f"실제={len(records)}"
        )
    if len(records) != len(chunks):
        raise ValueError(f"청크/임베딩 개수 불일치: chunks={len(chunks)}, records={len(records)}")

    empty_text = [r.get("chunk_id") for r in records if not str(r.get("text", "")).strip()]
    if empty_text:
        raise ValueError(f"빈 text 레코드 발견: {empty_text[:3]}")

    empty_embedding = [r.get("chunk_id") for r in records if not r.get("embedding")]
    if empty_embedding:
        raise ValueError(f"빈 embedding 레코드 발견: {empty_embedding[:3]}")

    bad_dimensions = [
        r.get("chunk_id")
        for r in records
        if len(r.get("embedding", [])) != embedding_dim
    ]
    if bad_dimensions:
        raise ValueError(f"embedding_dim 불일치 레코드 발견: {bad_dimensions[:3]}")

    id_mismatches = [
        (chunk.get("chunk_id"), record.get("chunk_id"))
        for chunk, record in zip(chunks, records, strict=True)
        if chunk.get("chunk_id") != record.get("chunk_id")
    ]
    if id_mismatches:
        raise ValueError(f"chunk_id 순서 불일치 발견: {id_mismatches[:3]}")


def save_json(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {args.input}")

    chunk_payload = load_chunks(args.input)
    chunks = chunk_payload["chunks"]

    print(f"[정보] 모델 로드: {args.model}")
    model = SentenceTransformer(args.model)

    records = build_embedding_records(
        chunks=chunks,
        model=model,
        batch_size=args.batch_size,
    )

    embedding_dim = len(records[0]["embedding"]) if records else 0
    output_payload = {
        "source_chunks": str(args.input),
        "embedded_at_utc": datetime.now(timezone.utc).isoformat(),
        "embedding_model": args.model,
        "embedding_dim": embedding_dim,
        "record_count": len(records),
        "records": records,
    }
    validate_embedding_payload(chunk_payload, output_payload)
    save_json(output_payload, args.output)

    print(
        f"[품질 검증 통과] embeddings — {len(records)}건, "
        f"dim={embedding_dim}, 빈 text/embedding 없음"
    )
    print(f"[완료] {len(records)}건 임베딩 생성 (dim={embedding_dim}) -> {args.output}")


if __name__ == "__main__":
    main()
