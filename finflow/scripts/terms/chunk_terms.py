"""[5/7] BigQuery 금융용어를 문장 단위의 RAG 청크로 변환한다.

입력(기본): {GCP_PROJECT_ID}.{BQ_DATASET_ID}.{TERMS_BQ_TABLE} — 읽기만 한다.
입력(오프라인 검증): --from-cleaned-json
    4단계와 같은 정규화를 거친 정제 JSON을 term_id 순으로 읽는다.
    BigQuery 조회 결과와 같은 열·순서를 만들기 위한 모드이다.
출력: data/terms/processed/financial_terms_chunks.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery

import terms_config


OUTPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_chunks.json"
)

CLEANED_JSON_PATH = (
    terms_config.processed_dir()
    / "financial_terms_800_v4_cleaned.json"
)

# BigQuery에서 읽는 열 (fetch_financial_terms의 SELECT와 같다)
SOURCE_COLUMNS = (
    "term_id",
    "term",
    "definition",
    "source",
    "source_document",
    "data_version",
)

TARGET_CHUNK_SIZE = 450
MIN_CHUNK_SIZE = 250
MAX_CHUNK_SIZE = 600
OVERLAP_SENTENCES = 1


def fetch_financial_terms(
    client: bigquery.Client,
    table_id: str,
    location: str,
) -> list[dict[str, Any]]:
    """BigQuery에서 금융용어를 조회한다 (읽기 전용)."""

    query = f"""
    SELECT
        term_id,
        term,
        definition,
        source,
        source_document,
        data_version
    FROM `{table_id}`
    ORDER BY term_id
    """

    query_job = client.query(
        query,
        location=location,
    )

    return [
        dict(row.items())
        for row in query_job.result()
    ]


def clean_text(text: str) -> str:
    """PDF 추출 과정에서 생긴 불필요한 공백을 정리한다."""
    cleaned = re.sub(r"\s+", " ", text).strip()

    # 한글 단어 중간에 들어간 공백을 일부 복원한다.
    # 예: '비 율' → '비율', '저 축' → '저축'
    cleaned = re.sub(
        r"(?<=[가-힣])\s(?=[가-힣]{1,2}(?:[.,;:)]|\s))",
        "",
        cleaned,
    )

    return cleaned


def split_sentences(text: str) -> list[str]:
    """한국어 설명을 문장 단위로 분리한다."""
    cleaned = clean_text(text)

    if not cleaned:
        return []

    sentence_pattern = re.compile(
        r"(?<=[.!?])\s+(?=[가-힣A-Za-z0-9\"'(])"
    )

    sentences = sentence_pattern.split(cleaned)

    return [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]


def split_long_sentence(
    sentence: str,
    max_size: int,
) -> list[str]:
    """최대 길이보다 긴 한 문장을 쉼표와 세미콜론 기준으로 나눈다."""
    if len(sentence) <= max_size:
        return [sentence]

    parts = re.split(
        r"(?<=[,;:])\s+",
        sentence,
    )

    results: list[str] = []
    current = ""

    for part in parts:
        candidate = (
            f"{current} {part}".strip()
            if current
            else part
        )

        if len(candidate) <= max_size:
            current = candidate
            continue

        if current:
            results.append(current)

        # 쉼표로도 나눌 수 없는 매우 긴 문장은 마지막 수단으로 자른다.
        while len(part) > max_size:
            cut_position = part.rfind(
                " ",
                0,
                max_size + 1,
            )

            if cut_position <= 0:
                cut_position = max_size

            results.append(
                part[:cut_position].strip()
            )
            part = part[cut_position:].strip()

        current = part

    if current:
        results.append(current)

    return results


def prepare_sentences(text: str) -> list[str]:
    """문장을 분리하고 지나치게 긴 문장을 추가로 나눈다."""
    sentences = split_sentences(text)
    prepared: list[str] = []

    for sentence in sentences:
        prepared.extend(
            split_long_sentence(
                sentence=sentence,
                max_size=MAX_CHUNK_SIZE,
            )
        )

    return prepared


def pack_sentences(
    sentences: list[str],
) -> list[str]:
    """문장을 목표 길이에 가깝게 묶어 청크를 생성한다."""
    if not sentences:
        return []

    chunks: list[str] = []
    current_sentences: list[str] = []

    for sentence in sentences:
        candidate_sentences = [
            *current_sentences,
            sentence,
        ]
        candidate_text = " ".join(candidate_sentences)

        if len(candidate_text) <= TARGET_CHUNK_SIZE:
            current_sentences.append(sentence)
            continue

        if (
            current_sentences
            and len(" ".join(current_sentences))
            >= MIN_CHUNK_SIZE
        ):
            chunks.append(
                " ".join(current_sentences)
            )

            overlap = (
                current_sentences[-OVERLAP_SENTENCES:]
                if OVERLAP_SENTENCES > 0
                else []
            )

            current_sentences = [
                *overlap,
                sentence,
            ]

            # 중복 문장 때문에 최대 길이를 넘는 경우 중복 제거
            if (
                len(" ".join(current_sentences))
                > MAX_CHUNK_SIZE
            ):
                current_sentences = [sentence]

            continue

        # 현재 청크가 최소 길이보다 짧으면 문장을 더 포함한다.
        if len(candidate_text) <= MAX_CHUNK_SIZE:
            current_sentences.append(sentence)
            continue

        if current_sentences:
            chunks.append(
                " ".join(current_sentences)
            )

        current_sentences = [sentence]

    if current_sentences:
        last_chunk = " ".join(current_sentences)

        # 마지막 청크가 너무 짧으면 이전 청크와 합칠 수 있는지 확인
        if (
            chunks
            and len(last_chunk) < MIN_CHUNK_SIZE
            and len(chunks[-1]) + 1 + len(last_chunk)
            <= MAX_CHUNK_SIZE
        ):
            chunks[-1] = (
                f"{chunks[-1]} {last_chunk}"
            )
        else:
            chunks.append(last_chunk)

    return chunks


def create_chunks(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """금융용어를 문장 기반 청크로 변환한다."""
    chunks: list[dict[str, Any]] = []

    for record in records:
        term_id = str(record["term_id"])
        term = clean_text(str(record["term"]))
        definition = clean_text(
            str(record["definition"])
        )

        sentences = prepare_sentences(definition)
        split_chunks = pack_sentences(sentences)

        # 설명이 비어 있거나 문장 분리가 실패한 경우
        if not split_chunks and definition:
            split_chunks = [definition]

        total_chunks = len(split_chunks)

        for chunk_index, body_text in enumerate(
            split_chunks
        ):
            chunk_id = (
                f"{term_id}_chunk_{chunk_index:03d}"
            )

            # 모든 청크에 용어명을 포함해 검색 문맥을 유지한다.
            chunk_text = (
                f"금융용어: {term}\n"
                f"설명: {body_text}"
            )

            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "term_id": term_id,
                    "term": term,
                    "chunk_index": chunk_index,
                    "total_chunks": total_chunks,
                    "text": chunk_text,
                    "text_length": len(chunk_text),
                    "source": record.get(
                        "source",
                        "한국은행",
                    ),
                    "source_document": record.get(
                        "source_document",
                        "경제금융용어",
                    ),
                    "data_version": record.get(
                        "data_version",
                        "v4",
                    ),
                    "chunking_strategy": (
                        "sentence_aware_packing"
                    ),
                }
            )

    return chunks


def validate_chunks(
    source_count: int,
    chunks: list[dict[str, Any]],
) -> None:
    """생성된 청크의 품질을 검증한다."""
    if not chunks:
        raise ValueError(
            "생성된 청크가 없습니다."
        )

    chunk_ids = [
        chunk["chunk_id"]
        for chunk in chunks
    ]

    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError(
            "중복 chunk_id가 있습니다."
        )

    empty_chunks = [
        chunk["chunk_id"]
        for chunk in chunks
        if not chunk["text"].strip()
    ]

    if empty_chunks:
        raise ValueError(
            f"빈 청크가 있습니다: {empty_chunks[:5]}"
        )

    covered_term_ids = {
        chunk["term_id"]
        for chunk in chunks
    }

    if len(covered_term_ids) != source_count:
        raise ValueError(
            "일부 금융용어가 청크에 포함되지 않았습니다."
        )

    oversized_chunks = [
        chunk
        for chunk in chunks
        if chunk["text_length"]
        > MAX_CHUNK_SIZE + 100
    ]

    if oversized_chunks:
        print(
            "[경고] 최대 권장 길이를 초과한 청크:",
            len(oversized_chunks),
        )


def calculate_statistics(
    chunks: list[dict[str, Any]],
) -> dict[str, float | int]:
    """청크 길이 통계를 계산한다."""
    lengths = [
        chunk["text_length"]
        for chunk in chunks
    ]

    return {
        "minimum_length": min(lengths),
        "maximum_length": max(lengths),
        "average_length": round(
            statistics.mean(lengths),
            2,
        ),
        "median_length": round(
            statistics.median(lengths),
            2,
        ),
        "short_chunk_count": sum(
            length < MIN_CHUNK_SIZE
            for length in lengths
        ),
    }


def save_chunks(
    chunks: list[dict[str, Any]],
    output_path: Path,
    source_count: int,
    statistics_data: dict[str, float | int],
    source: str = "BigQuery financial_terms",
) -> None:
    """청크와 메타데이터를 JSON 파일로 저장한다."""
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "metadata": {
            "dataset_name": (
                "financial_terms_chunks"
            ),
            "source": source,
            "source_record_count": source_count,
            "chunk_count": len(chunks),
            "chunking_strategy": (
                "sentence_aware_packing"
            ),
            "target_chunk_size": (
                TARGET_CHUNK_SIZE
            ),
            "minimum_chunk_size": (
                MIN_CHUNK_SIZE
            ),
            "maximum_chunk_size": (
                MAX_CHUNK_SIZE
            ),
            "overlap_sentences": (
                OVERLAP_SENTENCES
            ),
            "length_statistics": (
                statistics_data
            ),
        },
        "chunks": chunks,
    }

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )


def load_records_from_cleaned_json(
    path: Path,
) -> list[dict[str, Any]]:
    """정제 JSON을 BigQuery 적재 때와 같은 방식으로 변환해 읽는다."""
    from load_terms_to_bigquery import load_json, normalize_records

    rows = normalize_records(load_json(path))

    return sorted(
        (
            {column: row[column] for column in SOURCE_COLUMNS}
            for row in rows
        ),
        key=lambda row: row["term_id"],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="금융용어를 문장 단위 청크로 만든다 (기본 입력: BigQuery)."
    )
    parser.add_argument(
        "--from-cleaned-json",
        action="store_true",
        help="BigQuery 대신 정제 JSON에서 읽는다 (오프라인 검증용).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """금융용어 조회부터 문장 기반 청킹까지 실행한다."""
    args = parse_args(argv)
    load_dotenv()

    try:
        if args.from_cleaned_json:
            source_label = terms_config.display_path(CLEANED_JSON_PATH)
            records = load_records_from_cleaned_json(CLEANED_JSON_PATH)
        else:
            table_id = terms_config.bigquery_table_id()
            source_label = f"BigQuery {table_id}"
            client = bigquery.Client(
                project=table_id.split(".", 1)[0]
            )
            records = fetch_financial_terms(
                client=client,
                table_id=table_id,
                location=terms_config.bigquery_location(),
            )

        print(f"[정보] 입력: {source_label}")

        chunks = create_chunks(records)

        validate_chunks(
            source_count=len(records),
            chunks=chunks,
        )

        statistics_data = calculate_statistics(
            chunks
        )

        save_chunks(
            chunks=chunks,
            output_path=OUTPUT_PATH,
            source_count=len(records),
            statistics_data=statistics_data,
            source=(
                "cleaned JSON (offline, BigQuery 적재와 같은 변환)"
                if args.from_cleaned_json
                else "BigQuery financial_terms"
            ),
        )

        print(
            "[완료] 문장 단위 금융용어 청킹 성공"
        )
        print(
            f"- 원본 금융용어: {len(records):,}건"
        )
        print(
            f"- 생성 청크: {len(chunks):,}건"
        )
        print(
            f"- 최소 길이: "
            f"{statistics_data['minimum_length']}자"
        )
        print(
            f"- 최대 길이: "
            f"{statistics_data['maximum_length']}자"
        )
        print(
            f"- 평균 길이: "
            f"{statistics_data['average_length']}자"
        )
        print(
            f"- {MIN_CHUNK_SIZE}자 미만: "
            f"{statistics_data['short_chunk_count']}건"
        )
        print(
            f"- 결과 파일: {terms_config.display_path(OUTPUT_PATH)}"
        )

    except GoogleAPIError as exc:
        print(f"[BigQuery 오류] {exc}")
        raise SystemExit(1) from exc

    except ValueError as exc:
        print(f"[검증 오류] {exc}")
        raise SystemExit(1) from exc

    except Exception as exc:
        print(f"[예상하지 못한 오류] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
