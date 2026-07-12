#!/usr/bin/env python3
"""
1일차 실습: 페르소나 텍스트 전처리 및 청킹(Chunking)

기능:
- day0에서 받은 Nemotron 페르소나 JSON 로드
- 결측/중복/불필요 공백 정리
- 의미 단위(문단) + 최대 길이 기준 청킹
- 청킹 결과를 재현 가능한 JSON으로 저장

사용 예시:
python day0_download_personas.py --limit 50
python day1_preprocess_chunk.py \
  --input ../data/personas_raw.json \
  --output ../data/chunks.json \
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

PERSONA_TEXT_FIELDS = [
    "persona",
    "professional_persona",
    "sports_persona",
    "arts_persona",
    "travel_persona",
    "culinary_persona",
    "family_persona",
    "cultural_background",
    "skills_and_expertise",
    "hobbies_and_interests",
    "career_goals_and_ambitions",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="페르소나 전처리 및 청킹 스크립트")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("../data/personas_raw.json"),
        help="원본 페르소나 JSON 경로",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../data/chunks.json"),
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


def load_personas(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("원본 JSON 루트는 객체(dict)여야 합니다.")
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise ValueError("records 필드는 리스트여야 합니다.")
    return payload


def normalize_text(text: str) -> str:
    """
    텍스트 정규화: 앞뒤 공백 제거, 연속 공백/줄바꿈 정리.

    학습 포인트:
        - re.sub(r"\\s+", " ", text): 여러 공백/탭/줄바꿈을 단일 공백으로 통일
        - 전처리 단계에서 동일 의미의 텍스트 변형을 줄여 임베딩 품질을 높입니다.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def get_persona_id(record: dict[str, Any]) -> str:
    persona_id = str(record.get("uuid") or record.get("persona_id", "")).strip()
    if not persona_id:
        raise ValueError("uuid(또는 persona_id)가 비어 있는 레코드가 있습니다.")
    return persona_id


def build_persona_text(record: dict[str, Any]) -> str:
    """
    Nemotron 레코드의 페르소나·속성 텍스트 필드를 결합합니다.

    persona(요약) + 7개 페르소나 유형 + 속성 설명을 이어 붙여
    검색·타겟팅에 유용한 단일 문서를 만듭니다.
    """
    parts: list[str] = []
    for field in PERSONA_TEXT_FIELDS:
        raw_value = record.get(field)
        if raw_value is None:
            continue

        value = normalize_text(str(raw_value))
        if value:
            parts.append(value)

    if not parts:
        raise ValueError(f"uuid={get_persona_id(record)} 텍스트가 비어 있습니다.")

    return "\n\n".join(parts)


def split_paragraphs(text: str) -> list[str]:
    """
    원문에 남아 있는 문단 구분(\\n\\n)을 기준으로 1차 분할합니다.
    문단 구분이 없으면 전체를 하나의 문단으로 취급합니다.
    """
    raw_parts = re.split(r"\n\s*\n", text)
    paragraphs = [normalize_text(part) for part in raw_parts if normalize_text(part)]
    return paragraphs or [text]


def chunk_paragraph(paragraph: str, max_chars: int, overlap_chars: int) -> list[str]:
    """
    긴 문단을 max_chars 이하 슬라이딩 윈도우로 분할합니다.

    overlap_chars만큼 이전 청크 끝 텍스트를 다음 청크 시작에 겹쳐
    문맥 단절을 완화합니다.
    """
    if len(paragraph) <= max_chars:
        return [paragraph]

    chunks: list[str] = []
    start = 0
    while start < len(paragraph):
        end = min(start + max_chars, len(paragraph))
        chunks.append(paragraph[start:end].strip())
        if end >= len(paragraph):
            break
        start = max(end - overlap_chars, start + 1)

    return [chunk for chunk in chunks if chunk]


def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("--max-chars 값은 1 이상이어야 합니다.")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("--overlap-chars는 0 이상이고 --max-chars보다 작아야 합니다.")

    chunks: list[str] = []
    for paragraph in split_paragraphs(text):
        chunks.extend(chunk_paragraph(paragraph, max_chars, overlap_chars))
    return chunks


def extract_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """
    3~4주차 Vector DB 필터링에 사용할 인구통계·지리 메타데이터를 추출합니다.
    (Nemotron-Personas-Korea 실제 스키마 기준)
    """
    return {
        "persona_id": get_persona_id(record),
        "sex": record.get("sex"),
        "age": record.get("age"),
        "marital_status": record.get("marital_status"),
        "military_status": record.get("military_status"),
        "family_type": record.get("family_type"),
        "housing_type": record.get("housing_type"),
        "education_level": record.get("education_level"),
        "bachelors_field": record.get("bachelors_field"),
        "occupation": record.get("occupation"),
        "district": record.get("district"),
        "province": record.get("province"),
        "country": record.get("country"),
        "hobbies_and_interests_list": record.get("hobbies_and_interests_list"),
        "skills_and_expertise_list": record.get("skills_and_expertise_list"),
    }


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    uuid 기준 중복 레코드를 제거합니다. (먼저 등장한 레코드 유지)
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        persona_id = get_persona_id(record)
        if persona_id in seen:
            continue
        seen.add(persona_id)
        unique.append(record)
    return unique


def preprocess_and_chunk(
    payload: dict[str, Any],
    max_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise ValueError("records 필드는 리스트여야 합니다.")

    unique_records = dedupe_records(records)
    chunks: list[dict[str, Any]] = []

    for record in unique_records:
        if not isinstance(record, dict):
            raise ValueError("records 항목은 객체(dict)여야 합니다.")

        persona_id = get_persona_id(record)
        metadata = extract_metadata(record)
        text = build_persona_text(record)
        text_chunks = chunk_text(text, max_chars=max_chars, overlap_chars=overlap_chars)

        for index, chunk_text_value in enumerate(text_chunks):
            chunks.append(
                {
                    "chunk_id": f"{persona_id}_chunk_{index}",
                    "persona_id": persona_id,
                    "chunk_index": index,
                    "text": chunk_text_value,
                    "metadata": metadata,
                }
            )

    return {
        "source": payload.get("source"),
        "processed_at_utc": datetime.now(timezone.utc).isoformat(),
        "chunking_config": {
            "strategy": "paragraph_first_then_sliding_window",
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
            "text_fields": PERSONA_TEXT_FIELDS,
            "rules": [
                "Nemotron 페르소나·속성 텍스트 필드 결합",
                "연속 공백/줄바꿈 정규화",
                "문단 단위 분할 후 max_chars 초과 시 overlap 적용",
                "uuid 기준 중복 레코드 제거",
            ],
        },
        "persona_count": len(unique_records),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


def save_json(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise FileNotFoundError(
            f"입력 파일을 찾을 수 없습니다: {args.input}\n"
            "먼저 day0_download_personas.py 를 실행해 HF 데이터를 받으세요."
        )

    source_payload = load_personas(args.input)
    chunked_payload = preprocess_and_chunk(
        payload=source_payload,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )
    save_json(chunked_payload, args.output)

    chunks = chunked_payload.get("chunks", [])
    if chunked_payload.get("chunk_count") != len(chunks):
        raise ValueError(
            f"chunk_count 불일치: 메타데이터={chunked_payload.get('chunk_count')}, "
            f"실제={len(chunks)}"
        )
    empty_text = [c.get("chunk_id") for c in chunks if not str(c.get("text", "")).strip()]
    if empty_text:
        raise ValueError(f"빈 text 청크 발견: {empty_text[:3]}")

    print(
        f"[품질 검증 통과] chunks — {len(chunks)}건, 빈 text 없음"
    )
    print(
        f"[완료] {chunked_payload['persona_count']}명 페르소나 -> "
        f"{chunked_payload['chunk_count']}개 청크 저장: {args.output}"
    )


if __name__ == "__main__":
    main()
