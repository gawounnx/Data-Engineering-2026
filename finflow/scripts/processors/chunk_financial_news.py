"""BigQuery 금융뉴스를 RAG 검색용 청크로 변환한다."""

from __future__ import annotations

import json
import os
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery


OUTPUT_PATH = Path(
    "data/processed/financial_news_chunks.json"
)

TARGET_CHUNK_SIZE = 450
MIN_CHUNK_SIZE = 150
MAX_CHUNK_SIZE = 600
OVERLAP_SENTENCES = 1


def fetch_financial_news(
    client: bigquery.Client,
    project_id: str,
    dataset_id: str,
    location: str,
) -> list[dict[str, Any]]:
    """BigQuery에서 금융뉴스를 조회한다."""
    table_id = f"{project_id}.{dataset_id}.financial_news"

    query = f"""
    SELECT
        article_id,
        title,
        summary,
        published_at,
        original_url,
        naver_url,
        category,
        financial_relevance_score,
        search_queries,
        collected_at,
        source_type
    FROM `{table_id}`
    WHERE title IS NOT NULL
      AND TRIM(title) != ''
      AND financial_relevance_score >= 2
      AND DATE(published_at) >= DATE_SUB(
          CURRENT_DATE(),
          INTERVAL 1 YEAR
      )
    ORDER BY published_at DESC
    """

    query_job = client.query(
        query,
        location=location,
    )

    return [
        dict(row.items())
        for row in query_job.result()
    ]


def clean_text(value: Any) -> str:
    """공백과 줄바꿈을 정리한다."""
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def serialize_datetime(value: Any) -> str:
    """날짜 값을 ISO 8601 문자열로 변환한다."""
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


def split_sentences(text: str) -> list[str]:
    """한국어 문장을 문장부호 기준으로 분리한다."""
    cleaned = clean_text(text)

    if not cleaned:
        return []

    pattern = re.compile(
        r"(?<=[.!?])\s+(?=[가-힣A-Za-z0-9\"'(])"
    )

    return [
        sentence.strip()
        for sentence in pattern.split(cleaned)
        if sentence.strip()
    ]


def split_long_sentence(
    sentence: str,
    max_size: int,
) -> list[str]:
    """긴 문장을 쉼표와 공백 기준으로 추가 분리한다."""
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

        remaining = part

        while len(remaining) > max_size:
            cut_position = remaining.rfind(
                " ",
                0,
                max_size + 1,
            )

            if cut_position <= 0:
                cut_position = max_size

            results.append(
                remaining[:cut_position].strip()
            )
            remaining = remaining[
                cut_position:
            ].strip()

        current = remaining

    if current:
        results.append(current)

    return results


def prepare_sentences(text: str) -> list[str]:
    """문장을 나누고 지나치게 긴 문장을 추가 분리한다."""
    prepared: list[str] = []

    for sentence in split_sentences(text):
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
    """문장을 목표 길이에 가깝게 묶어 청크를 만든다."""
    if not sentences:
        return []

    chunks: list[str] = []
    current_sentences: list[str] = []

    for sentence in sentences:
        candidate_sentences = [
            *current_sentences,
            sentence,
        ]
        candidate_text = " ".join(
            candidate_sentences
        )

        if len(candidate_text) <= TARGET_CHUNK_SIZE:
            current_sentences.append(sentence)
            continue

        if current_sentences:
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

        if len(" ".join(current_sentences)) > MAX_CHUNK_SIZE:
            current_sentences = [sentence]

    if current_sentences:
        chunks.append(
            " ".join(current_sentences)
        )

    return chunks


def create_chunks(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """뉴스 레코드를 RAG 검색용 청크로 변환한다."""
    chunks: list[dict[str, Any]] = []

    for record in records:
        article_id = clean_text(
            record.get("article_id")
        )
        title = clean_text(
            record.get("title")
        )
        summary = clean_text(
            record.get("summary")
        )

        if not article_id or not title:
            continue

        body_text = summary or title
        sentences = prepare_sentences(body_text)
        split_chunks = pack_sentences(sentences)

        if not split_chunks:
            split_chunks = [body_text]

        total_chunks = len(split_chunks)

        for chunk_index, chunk_body in enumerate(
            split_chunks
        ):
            chunk_id = (
                f"{article_id}_chunk_{chunk_index:03d}"
            )

            chunk_text = (
                f"뉴스 제목: {title}\n"
                f"뉴스 내용: {chunk_body}"
            )

            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "article_id": article_id,
                    "title": title,
                    "chunk_index": chunk_index,
                    "total_chunks": total_chunks,
                    "text": chunk_text,
                    "text_length": len(chunk_text),
                    "published_at": serialize_datetime(
                        record.get("published_at")
                    ),
                    "collected_at": serialize_datetime(
                        record.get("collected_at")
                    ),
                    "original_url": clean_text(
                        record.get("original_url")
                    ),
                    "naver_url": clean_text(
                        record.get("naver_url")
                    ),
                    "category": clean_text(
                        record.get("category")
                    ),
                    "financial_relevance_score": int(
                        record.get(
                            "financial_relevance_score",
                            0,
                        )
                        or 0
                    ),
                    "source_type": clean_text(
                        record.get("source_type")
                    ),
                    "search_queries": list(
                        record.get(
                            "search_queries",
                            [],
                        )
                        or []
                    ),
                    "chunking_strategy": (
                        "sentence_aware_packing"
                    ),
                }
            )

    return chunks


def validate_chunks(
    chunks: list[dict[str, Any]],
) -> None:
    """생성된 청크를 검증한다."""
    if not chunks:
        raise ValueError(
            "생성된 뉴스 청크가 없습니다."
        )

    chunk_ids = [
        chunk["chunk_id"]
        for chunk in chunks
    ]

    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError(
            "중복된 chunk_id가 있습니다."
        )

    empty_chunks = [
        chunk["chunk_id"]
        for chunk in chunks
        if not str(chunk["text"]).strip()
    ]

    if empty_chunks:
        raise ValueError(
            f"빈 뉴스 청크가 있습니다: {empty_chunks[:5]}"
        )


def calculate_statistics(
    chunks: list[dict[str, Any]],
) -> dict[str, float | int]:
    """청크 길이 통계를 계산한다."""
    lengths = [
        int(chunk["text_length"])
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
    source_count: int,
    statistics_data: dict[str, float | int],
    output_path: Path,
) -> None:
    """뉴스 청크를 JSON 파일로 저장한다."""
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "metadata": {
            "dataset_name": "financial_news_chunks",
            "source": "BigQuery financial_news",
            "source_record_count": source_count,
            "chunk_count": len(chunks),
            "chunking_strategy": (
                "sentence_aware_packing"
            ),
            "target_chunk_size": TARGET_CHUNK_SIZE,
            "minimum_chunk_size": MIN_CHUNK_SIZE,
            "maximum_chunk_size": MAX_CHUNK_SIZE,
            "overlap_sentences": OVERLAP_SENTENCES,
            "length_statistics": statistics_data,
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


def main() -> None:
    """BigQuery 뉴스 조회부터 청킹까지 실행한다."""
    load_dotenv()

    project_id = os.getenv(
        "GCP_PROJECT_ID",
        "melodic-furnace-501500-g4",
    ).strip()

    dataset_id = os.getenv(
        "BQ_DATASET_ID",
        "finflow",
    ).strip()

    location = os.getenv(
        "BQ_LOCATION",
        "asia-northeast3",
    ).strip()

    try:
        client = bigquery.Client(
            project=project_id
        )

        records = fetch_financial_news(
            client=client,
            project_id=project_id,
            dataset_id=dataset_id,
            location=location,
        )

        chunks = create_chunks(records)
        validate_chunks(chunks)

        statistics_data = calculate_statistics(
            chunks
        )

        save_chunks(
            chunks=chunks,
            source_count=len(records),
            statistics_data=statistics_data,
            output_path=OUTPUT_PATH,
        )

        print("\n[뉴스 청킹 완료]")
        print(f"- 원본 뉴스: {len(records):,}건")
        print(f"- 생성 청크: {len(chunks):,}건")
        print(
            f"- 평균 길이: "
            f"{statistics_data['average_length']}자"
        )
        print(
            f"- 결과 파일: {OUTPUT_PATH.resolve()}"
        )

        print("\n[샘플]")
        for chunk in chunks[:3]:
            print("-" * 60)
            print(f"chunk_id: {chunk['chunk_id']}")
            print(f"title: {chunk['title']}")
            print(f"text: {chunk['text']}")

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
