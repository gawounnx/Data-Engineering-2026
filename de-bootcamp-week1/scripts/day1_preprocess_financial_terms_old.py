#!/usr/bin/env python3
"""
FinFlow AI - 한국은행 금융용어 전처리 및 청킹

프로젝트 흐름:
한국은행 금융용어 원본 JSON
→ 텍스트 정규화
→ 중복 제거
→ 설명 청킹
→ 메타데이터 부착
→ JSON 저장

실행 예시:
python scripts/day1_preprocess_financial_terms.py \
  --input data/bok_financial_terms_raw.json \
  --output data/bok_financial_terms_chunks.json \
  --max-chars 400 \
  --overlap-chars 50
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MAX_CHARS = 400
DEFAULT_OVERLAP_CHARS = 50


def parse_args() -> argparse.Namespace:
    """터미널에서 입력한 옵션을 읽습니다."""
    parser = argparse.ArgumentParser(
        description="한국은행 금융용어 전처리 및 청킹"
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/bok_financial_terms_raw.json"),
        help="금융용어 원본 JSON 경로",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/bok_financial_terms_chunks.json"),
        help="청킹 결과 저장 경로",
    )

    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"청크 최대 문자 수 (기본값: {DEFAULT_MAX_CHARS})",
    )

    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=DEFAULT_OVERLAP_CHARS,
        help=f"청크 간 겹침 문자 수 (기본값: {DEFAULT_OVERLAP_CHARS})",
    )

    return parser.parse_args()


def load_financial_terms(path: Path) -> dict[str, Any]:
    """금융용어 JSON 파일을 읽고 기본 구조를 검증합니다."""
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError("원본 JSON의 최상위 구조는 dict여야 합니다.")

    records = payload.get("records")

    if not isinstance(records, list):
        raise ValueError("records 필드는 list여야 합니다.")

    return payload


def normalize_text(text: str) -> str:
    """
    불필요한 공백, 탭, 줄바꿈을 공백 하나로 정리합니다.

    예:
    '기준금리는\\n\\t 정책금리이다.'
    → '기준금리는 정책금리이다.'
    """
    cleaned = text.strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def get_term_id(record: dict[str, Any]) -> str:
    """
    한국은행 금융용어의 고유번호를 가져옵니다.

    ec_word_sn이 없으면 term을 대신 사용합니다.
    """
    ec_word_sn = record.get("ec_word_sn")

    if ec_word_sn is not None:
        return str(ec_word_sn).strip()

    term = normalize_text(str(record.get("term", "")))

    if not term:
        raise ValueError("ec_word_sn과 term이 모두 비어 있는 레코드가 있습니다.")

    return term


def dedupe_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """ec_word_sn 기준으로 중복 금융용어를 제거합니다."""
    seen: set[str] = set()
    unique_records: list[dict[str, Any]] = []

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("records 내부 항목은 dict여야 합니다.")

        term_id = get_term_id(record)

        if term_id in seen:
            continue

        seen.add(term_id)
        unique_records.append(record)

    return unique_records


def build_term_text(record: dict[str, Any]) -> str:
    """
    금융용어명과 설명을 하나의 검색용 문서로 만듭니다.

    예:
    기준금리: 한국은행 금융통화위원회에서 결정하는 정책금리를 말한다.
    """
    term = normalize_text(str(record.get("term", "")))
    description = normalize_text(str(record.get("description", "")))

    if not term:
        raise ValueError(f"term이 비어 있습니다: {record}")

    if not description:
        raise ValueError(f"description이 비어 있습니다: term={term}")

    return f"{term}: {description}"


def chunk_text(
    text: str,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    """
    긴 금융용어 설명을 최대 길이 기준으로 나눕니다.

    다음 청크가 이전 청크의 마지막 일부를 포함하도록
    overlap_chars를 적용합니다.
    """
    if max_chars <= 0:
        raise ValueError("--max-chars는 1 이상이어야 합니다.")

    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError(
            "--overlap-chars는 0 이상이고 --max-chars보다 작아야 합니다."
        )

    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - overlap_chars

    return chunks


def extract_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """
    ChromaDB 검색 및 출처 추적에 사용할 메타데이터를 만듭니다.
    """
    return {
        "term_id": get_term_id(record),
        "term": normalize_text(str(record.get("term", ""))),
        "search_keyword": normalize_text(
            str(record.get("search_keyword", ""))
        ),
        "source": record.get("source"),
        "source_url": record.get("source_url"),
        "collection": "glossary",
    }


def preprocess_and_chunk(
    payload: dict[str, Any],
    max_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    """금융용어 전체를 전처리하고 청크 목록으로 변환합니다."""
    records = payload.get("records", [])

    if not isinstance(records, list):
        raise ValueError("records 필드는 list여야 합니다.")

    unique_records = dedupe_records(records)
    chunks: list[dict[str, Any]] = []

    for record in unique_records:
        term_id = get_term_id(record)
        term = normalize_text(str(record.get("term", "")))
        metadata = extract_metadata(record)
        full_text = build_term_text(record)

        text_chunks = chunk_text(
            text=full_text,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

        for chunk_index, chunk_value in enumerate(text_chunks):
            chunks.append(
                {
                    "chunk_id": f"bok_term_{term_id}_chunk_{chunk_index}",
                    "term_id": term_id,
                    "term": term,
                    "chunk_index": chunk_index,
                    "text": chunk_value,
                    "metadata": metadata,
                }
            )

    return {
        "source": payload.get("source"),
        "processed_at_utc": datetime.now(timezone.utc).isoformat(),
        "chunking_config": {
            "strategy": "term_description_sliding_window",
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
            "dedupe_key": "ec_word_sn",
            "rules": [
                "금융용어명과 설명 결합",
                "연속 공백·탭·줄바꿈 정규화",
                "max_chars 초과 시 overlap 적용",
                "ec_word_sn 기준 중복 제거",
            ],
        },
        "term_count": len(unique_records),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


def save_json(
    payload: dict[str, Any],
    output_path: Path,
) -> None:
    """결과를 한글이 깨지지 않는 JSON으로 저장합니다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )


def validate_chunks(payload: dict[str, Any]) -> None:
    """청킹 결과의 개수와 필수 필드를 검증합니다."""
    chunks = payload.get("chunks", [])

    if not isinstance(chunks, list):
        raise ValueError("chunks 필드는 list여야 합니다.")

    if payload.get("chunk_count") != len(chunks):
        raise ValueError(
            "chunk_count와 실제 chunks 개수가 일치하지 않습니다."
        )

    empty_text_ids = [
        chunk.get("chunk_id")
        for chunk in chunks
        if not str(chunk.get("text", "")).strip()
    ]

    if empty_text_ids:
        raise ValueError(
            f"빈 text 청크가 발견됐습니다: {empty_text_ids[:3]}"
        )

    missing_metadata_ids = [
        chunk.get("chunk_id")
        for chunk in chunks
        if not isinstance(chunk.get("metadata"), dict)
    ]

    if missing_metadata_ids:
        raise ValueError(
            f"metadata가 없는 청크가 발견됐습니다: "
            f"{missing_metadata_ids[:3]}"
        )


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise FileNotFoundError(
            f"입력 파일을 찾을 수 없습니다: {args.input}"
        )

    source_payload = load_financial_terms(args.input)

    chunked_payload = preprocess_and_chunk(
        payload=source_payload,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )

    validate_chunks(chunked_payload)
    save_json(chunked_payload, args.output)

    print(
        f"[품질 검증 통과] 금융용어 chunks — "
        f"{chunked_payload['chunk_count']}건, 빈 text 없음"
    )

    print(
        f"[완료] 금융용어 {chunked_payload['term_count']}건 "
        f"-> {chunked_payload['chunk_count']}개 청크 저장: "
        f"{args.output}"
    )


if __name__ == "__main__":
    main()
    