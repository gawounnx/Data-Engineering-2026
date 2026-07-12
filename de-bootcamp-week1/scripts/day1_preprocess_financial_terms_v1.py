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


DEFAULT_MIN_CHARS = 250
DEFAULT_TARGET_CHARS = 350
DEFAULT_MAX_CHARS = 450


def parse_args() -> argparse.Namespace:
    """터미널에서 입력한 옵션을 읽습니다."""
    parser = argparse.ArgumentParser(
        description="한국은행 금융용어 전처리 및 청킹"
    )

    parser.add_argument(
    "--input",
    type=Path,
    default=Path("data/bok_financial_terms_raw.json"),
    help="금융용어 원본 JSON",
)

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/bok_financial_terms_chunks.json"),
        help="결과 JSON",
    )

    parser.add_argument(
    "--min-chars",
    type=int,
    default=DEFAULT_MIN_CHARS,
    help=f"청크 최소 문자 수 기준 (기본값: {DEFAULT_MIN_CHARS})",
)

    parser.add_argument(
        "--target-chars",
        type=int,
        default=DEFAULT_TARGET_CHARS,
        help=f"청크 목표 문자 수 (기본값: {DEFAULT_TARGET_CHARS})",
    )

    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"청크 최대 문자 수 (기본값: {DEFAULT_MAX_CHARS})",
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


def split_sentences(text: str) -> list[str]:
    """
    마침표, 물음표, 느낌표를 기준으로 문장을 분리합니다.

    문장부호 뒤의 공백을 기준으로 나누기 때문에
    문장 중간에서 청크가 끊기는 문제를 줄일 수 있습니다.
    """
    normalized = normalize_text(text)

    sentences = re.split(
        r"(?<=[.!?])\s+",
        normalized,
    )

    return [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]


def split_long_sentence(
    sentence: str,
    max_chars: int,
) -> list[str]:
    """
    문장 하나가 max_chars보다 긴 예외 상황을 처리합니다.

    쉼표나 공백 위치를 우선 찾아 자르기 때문에
    단어 중간 절단을 최대한 방지합니다.
    """
    if len(sentence) <= max_chars:
        return [sentence]

    pieces: list[str] = []
    remaining = sentence.strip()

    while len(remaining) > max_chars:
        search_area = remaining[: max_chars + 1]

        candidate_positions = [
            search_area.rfind(", "),
            search_area.rfind("; "),
            search_area.rfind(" "),
        ]

        cut_position = max(candidate_positions)

        # 적절한 구분 위치가 너무 앞에 있으면 최대 길이에서 자릅니다.
        if cut_position < int(max_chars * 0.6):
            cut_position = max_chars
            piece = remaining[:cut_position].strip()
            remaining = remaining[cut_position:].strip()
        else:
            piece = remaining[: cut_position + 1].strip()
            remaining = remaining[cut_position + 1 :].strip()

        if piece:
            pieces.append(piece)

    if remaining:
        pieces.append(remaining)

    return pieces


def chunk_text(
    text: str,
    min_chars: int,
    target_chars: int,
    max_chars: int,
) -> list[str]:
    """
    문장 의미를 보존하면서 비슷한 길이의 청크를 만듭니다.

    1. 문장 단위 분리
    2. 긴 문장은 쉼표·공백 기준 보조 분할
    3. 문장을 목표 길이에 가깝게 결합
    4. 마지막 청크가 지나치게 짧으면 이전 청크와 재조정
    """
    if min_chars <= 0:
        raise ValueError("--min-chars는 1 이상이어야 합니다.")

    if not min_chars <= target_chars <= max_chars:
        raise ValueError(
            "길이 설정은 min_chars <= target_chars <= max_chars여야 합니다."
        )

    sentences: list[str] = []

    for sentence in split_sentences(text):
        sentences.extend(
            split_long_sentence(
                sentence=sentence,
                max_chars=max_chars,
            )
        )

    if not sentences:
        return []

    grouped_sentences: list[list[str]] = []
    current: list[str] = []

    for sentence in sentences:
        candidate = " ".join(current + [sentence])

        if current and len(candidate) > max_chars:
            grouped_sentences.append(current)
            current = [sentence]
        else:
            current.append(sentence)

            # 목표 길이를 넘었고, 현재 문장이 완결된 경우 청크 확정
            if len(" ".join(current)) >= target_chars:
                grouped_sentences.append(current)
                current = []

    if current:
        grouped_sentences.append(current)

    # 마지막 청크가 너무 짧으면 이전 청크의 마지막 문장을 이동
    if len(grouped_sentences) >= 2:
        last_group = grouped_sentences[-1]
        previous_group = grouped_sentences[-2]

        while (
            len(" ".join(last_group)) < min_chars
            and len(previous_group) > 1
        ):
            moved_sentence = previous_group[-1]
            candidate_last = " ".join(
                [moved_sentence] + last_group
            )

            if len(candidate_last) > max_chars:
                break

            previous_group.pop()
            last_group.insert(0, moved_sentence)

        previous_text = " ".join(previous_group)
        last_text = " ".join(last_group)

        # 재배치 후에도 마지막 청크가 짧고 두 청크를 합칠 수 있으면 병합
        if (
            len(last_text) < min_chars
            and len(previous_text + " " + last_text) <= max_chars
        ):
            grouped_sentences[-2] = previous_group + last_group
            grouped_sentences.pop()

    return [
        " ".join(group).strip()
        for group in grouped_sentences
        if group
    ]

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
    min_chars: int,
    target_chars: int,
    max_chars: int,
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

        description = normalize_text(
            str(record.get("description", ""))
        )

        if not description:
            raise ValueError(
                f"description이 비어 있습니다: term={term}"
            )

        description_chunks = chunk_text(
            text=description,
            min_chars=min_chars,
            target_chars=target_chars,
            max_chars=max_chars,
        )

        for chunk_index, description_chunk in enumerate(
            description_chunks
        ):
            chunk_value = f"{term}: {description_chunk}"
            chunks.append(
                {
                    "chunk_id": f"bok_term_{term_id}_chunk_{chunk_index}",
                    "term_id": term_id,
                    "term": term,
                    "chunk_index": chunk_index,
                    "text": chunk_value,
                    "text_length": len(chunk_value),
                    "metadata": metadata,
                }
            )

    return {
        "source": payload.get("source"),
        "processed_at_utc": datetime.now(timezone.utc).isoformat(),
        "chunking_config": {
            "strategy": "sentence_based_balanced_chunking",
            "min_chars": min_chars,
            "target_chars": target_chars,
            "max_chars": max_chars,
            "dedupe_key": "ec_word_sn",
            "term_prefix_per_chunk": True,
            "rules": [
                "금융용어명과 설명을 검색 문서로 구성",
                "연속 공백·탭·줄바꿈 정규화",
                "문장부호 기준 문장 분리",
                "문장 의미를 유지하며 목표 길이에 맞게 결합",
                "모든 청크 앞에 금융용어명 반복",
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
    text_lengths = [
    len(str(chunk.get("text", "")))
    for chunk in chunks
    ]

    if text_lengths:
        average_length = sum(text_lengths) / len(text_lengths)

        print(
            "[청크 길이 통계] "
            f"최소={min(text_lengths)}자, "
            f"평균={average_length:.1f}자, "
            f"최대={max(text_lengths)}자"
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
        min_chars=args.min_chars,
        target_chars=args.target_chars,
        max_chars=args.max_chars,
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
    