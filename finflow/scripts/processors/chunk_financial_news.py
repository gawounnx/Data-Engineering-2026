"""BigQuery 금융뉴스를 RAG 검색용 청크로 변환한다.

증분 처리:
- 최근 NEWS_RETENTION_DAYS일(기본 7일) 안에 발행된 기사만 대상이다.
- 한 번에 최대 NEWS_MAX_ARTICLES_PER_RUN건(기본 2000건)만 처리한다.
- 진행 위치는 financial_news 컬렉션 메타데이터(스냅샷 안)에 저장한다.
- 새 기사를 최신순으로 먼저 처리하고, 처리량이 남으면 아직 못 채운
  구간(gap)을 최신 쪽부터 거꾸로 채운다. 첫 실행·장애 복구 때도
  최신 기사부터 앱에 제공된다.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

from dotenv import load_dotenv
from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery


OUTPUT_PATH = Path(
    "data/processed/financial_news_chunks.json"
)

CHROMA_PATH = Path("data/chroma")
NEWS_COLLECTION_NAME = "financial_news"

DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_ARTICLES_PER_RUN = 2000
BIGQUERY_PAGE_SIZE = 500

TARGET_CHUNK_SIZE = 450
MIN_CHUNK_SIZE = 150
MAX_CHUNK_SIZE = 600
OVERLAP_SENTENCES = 1


def read_positive_int_env(
    name: str,
    default: int,
) -> int:
    """양의 정수 환경 변수를 읽는다. 비어 있으면 기본값을 쓴다."""
    raw_value = os.getenv(name, "").strip()

    if not raw_value:
        return default

    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(
            f"{name}은 1 이상의 정수여야 합니다: {raw_value!r}"
        ) from error

    if value < 1:
        raise ValueError(
            f"{name}은 1 이상의 정수여야 합니다: {raw_value!r}"
        )

    return value


def parse_timestamp(
    value: Any,
) -> datetime | None:
    """ISO 8601 문자열을 UTC datetime으로 바꾼다. 실패하면 None."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()

        if not text:
            return None

        try:
            parsed = datetime.fromisoformat(
                text.replace("Z", "+00:00")
            )
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


# 진행 상태는 financial_news 컬렉션 메타데이터에 저장한다.
# 스냅샷과 함께 저장·교체되므로, 업로드 전에 실패한 실행은 진행 위치를 바꾸지 않는다.
INGEST_STATE_VERSION = 1
INGEST_STATE_KEYS: tuple[str, ...] = (
    "ingest_state_version",
    "ingest_cursor_collected_at",
    "ingest_cursor_article_id",
    "ingest_gap_lower_collected_at",
    "ingest_gap_lower_article_id",
    "ingest_gap_upper_collected_at",
    "ingest_gap_upper_article_id",
)

# (collected_at, article_id). 수집기는 한 번 실행에 같은 collected_at을 쓰므로
# article_id까지 함께 비교해야 같은 시각 기사 사이의 위치를 정확히 표시할 수 있다.
IngestKey = tuple[datetime, str]


class IngestState(NamedTuple):
    """증분 처리 위치.

    cursor: 이 키 이하의 기사는 처리했거나 gap 안에 있다.
    gap: (lower, upper) 사이(양끝 제외)의 기사는 아직 처리하지 않았다.
         lower가 None이면 보관 기간 시작까지다.
    """

    cursor: IngestKey | None = None
    gap_lower: IngestKey | None = None
    gap_upper: IngestKey | None = None

    @property
    def has_gap(self) -> bool:
        return self.gap_upper is not None


def _key_from_metadata(
    metadata: dict[str, Any],
    prefix: str,
) -> IngestKey | None:
    collected_at = parse_timestamp(
        metadata.get(f"{prefix}_collected_at")
    )
    article_id = str(
        metadata.get(f"{prefix}_article_id") or ""
    ).strip()

    if collected_at is None or not article_id:
        return None

    return collected_at, article_id


def state_from_metadata(
    metadata: dict[str, Any] | None,
) -> IngestState:
    """컬렉션 메타데이터에서 진행 상태를 읽는다. 없으면 빈 상태."""
    metadata = metadata or {}

    if metadata.get("ingest_state_version") != INGEST_STATE_VERSION:
        return IngestState()

    gap_upper = _key_from_metadata(metadata, "ingest_gap_upper")

    return IngestState(
        cursor=_key_from_metadata(metadata, "ingest_cursor"),
        gap_lower=(
            _key_from_metadata(metadata, "ingest_gap_lower")
            if gap_upper
            else None
        ),
        gap_upper=gap_upper,
    )


def state_to_metadata(
    state: IngestState,
) -> dict[str, str | int]:
    """진행 상태를 ChromaDB 컬렉션 메타데이터(문자열) 형식으로 바꾼다."""
    def put(prefix: str, key: IngestKey | None) -> dict[str, str]:
        return {
            f"{prefix}_collected_at": key[0].isoformat() if key else "",
            f"{prefix}_article_id": key[1] if key else "",
        }

    return {
        "ingest_state_version": INGEST_STATE_VERSION,
        **put("ingest_cursor", state.cursor),
        **put("ingest_gap_lower", state.gap_lower),
        **put("ingest_gap_upper", state.gap_upper),
    }


def read_ingest_state(
    chroma_path: Path,
) -> IngestState:
    """기존 뉴스 컬렉션의 진행 상태를 읽는다.

    ChromaDB·뉴스 컬렉션·상태가 없으면(첫 실행, 이전 형식 스냅샷) 빈 상태다.
    DB 파일이 없으면 새로 만들지 않는다.
    """
    if not (chroma_path / "chroma.sqlite3").exists():
        return IngestState()

    import chromadb

    client = chromadb.PersistentClient(
        path=str(chroma_path)
    )

    try:
        collection = client.get_collection(
            NEWS_COLLECTION_NAME
        )
    except Exception:
        return IngestState()

    return state_from_metadata(collection.metadata)


FETCH_QUERY_TEMPLATE = """
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
      AND article_id IS NOT NULL
      AND collected_at IS NOT NULL
      AND published_at >= @published_since
      AND (
        NOT @has_lower
        OR collected_at > @lower_collected_at
        OR (collected_at = @lower_collected_at AND article_id > @lower_article_id)
      )
      AND (
        NOT @has_upper
        OR collected_at < @upper_collected_at
        OR (collected_at = @upper_collected_at AND article_id < @upper_article_id)
      )
    ORDER BY collected_at DESC, article_id DESC
    LIMIT @row_limit
"""


def fetch_news_rows(
    client: bigquery.Client,
    table_id: str,
    location: str,
    published_since: datetime,
    lower: IngestKey | None,
    upper: IngestKey | None,
    row_limit: int,
) -> list[dict[str, Any]]:
    """(lower, upper) 사이(양끝 제외) 기사를 최신 수집순으로 최대 row_limit행 읽는다."""
    def key_params(name: str, key: IngestKey | None) -> list:
        return [
            bigquery.ScalarQueryParameter(
                f"has_{name}", "BOOL", key is not None
            ),
            bigquery.ScalarQueryParameter(
                f"{name}_collected_at",
                "TIMESTAMP",
                key[0] if key else published_since,
            ),
            bigquery.ScalarQueryParameter(
                f"{name}_article_id",
                "STRING",
                key[1] if key else "",
            ),
        ]

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(
                "published_since",
                "TIMESTAMP",
                published_since,
            ),
            *key_params("lower", lower),
            *key_params("upper", upper),
            bigquery.ScalarQueryParameter(
                "row_limit",
                "INT64",
                row_limit,
            ),
        ]
    )

    query_job = client.query(
        FETCH_QUERY_TEMPLATE.format(table_id=table_id),
        job_config=job_config,
        location=location,
    )

    return [
        dict(row.items())
        for row in query_job.result(
            page_size=BIGQUERY_PAGE_SIZE
        )
    ]


def row_key(row: dict[str, Any]) -> IngestKey:
    collected_at = parse_timestamp(row.get("collected_at"))

    if collected_at is None:
        raise ValueError(
            f"collected_at이 없는 행입니다: {row.get('article_id')!r}"
        )

    return collected_at, str(row.get("article_id") or "").strip()


def take_newest(
    rows: list[dict[str, Any]],
    budget: int,
) -> tuple[list[dict[str, Any]], bool]:
    """최신순 rows에서 budget행을 고른다. 남는 행이 있으면 True."""
    return rows[:budget], len(rows) > budget


def fetch_financial_news(
    client: bigquery.Client,
    project_id: str,
    dataset_id: str,
    location: str,
    published_since: datetime,
    state: IngestState,
    max_articles: int,
) -> tuple[list[dict[str, Any]], IngestState, dict[str, int]]:
    """이번 실행에서 처리할 기사와 실행 후 진행 상태를 계산한다.

    1. 새 기사(cursor 이후): 최신 수집순으로 최대 max_articles건.
       더 많으면 최신 max_articles건만 처리하고, 나머지는 gap으로 남긴다.
       그래서 첫 실행이나 장애 복구 때도 최신 기사부터 제공한다.
    2. 남은 처리량으로 gap을 최신 쪽부터 거꾸로 채운다.

    같은 수집 시각의 기사가 처리량 경계에 걸쳐 있어도
    (collected_at, article_id) 키로 경계를 표시하므로 누락·무한 반복이 없다.
    """
    table_id = f"{project_id}.{dataset_id}.financial_news"

    def fetch(lower, upper, limit):
        return fetch_news_rows(
            client=client,
            table_id=table_id,
            location=location,
            published_since=published_since,
            lower=lower,
            upper=upper,
            row_limit=limit,
        )

    # 1) 새 기사
    new_rows, overflow = take_newest(
        fetch(state.cursor, None, max_articles + 1),
        max_articles,
    )

    cursor = row_key(new_rows[0]) if new_rows else state.cursor
    gap_lower, gap_upper = state.gap_lower, state.gap_upper

    if overflow:
        # 기존 gap이 있으면 아래쪽 경계를 유지해 하나로 합친다.
        # 그 사이 이미 처리한 기사는 다시 읽지만 같은 chunk_id로 덮어써진다.
        if gap_upper is None:
            gap_lower = state.cursor
        gap_upper = row_key(new_rows[-1])

    # 2) 남은 처리량으로 gap 채우기
    backfill_rows: list[dict[str, Any]] = []
    remaining = max_articles - len(new_rows)

    if gap_upper is not None and remaining > 0:
        backfill_rows, more = take_newest(
            fetch(gap_lower, gap_upper, remaining + 1),
            remaining,
        )

        if more:
            gap_upper = row_key(backfill_rows[-1])
        else:
            gap_lower, gap_upper = None, None

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for record in new_rows + backfill_rows:
        article_id = clean_text(record.get("article_id"))

        if not article_id or article_id in seen_ids:
            continue

        seen_ids.add(article_id)
        records.append(record)

    new_state = IngestState(
        cursor=cursor,
        gap_lower=gap_lower if gap_upper else None,
        gap_upper=gap_upper,
    )

    counts = {
        "new_rows": len(new_rows),
        "backfill_rows": len(backfill_rows),
        "articles": len(records),
    }

    return records, new_state, counts


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
    """생성된 청크를 검증한다. 빈 목록은 '새 뉴스 없음'으로 허용한다."""
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

    if not lengths:
        return {
            "minimum_length": 0,
            "maximum_length": 0,
            "average_length": 0,
            "median_length": 0,
            "short_chunk_count": 0,
        }

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
    window: dict[str, Any] | None = None,
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
            "fetch_window": window or {},
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

    # .env에 빈 값으로 남아 있어도 기본값을 사용한다.
    project_id = (
        os.getenv("GCP_PROJECT_ID", "").strip()
        or "melodic-furnace-501500-g4"
    )

    dataset_id = (
        os.getenv("BQ_DATASET_ID", "").strip()
        or "finflow"
    )

    location = (
        os.getenv("BQ_LOCATION", "").strip()
        or "asia-northeast3"
    )

    try:
        retention_days = read_positive_int_env(
            "NEWS_RETENTION_DAYS",
            DEFAULT_RETENTION_DAYS,
        )
        max_articles = read_positive_int_env(
            "NEWS_MAX_ARTICLES_PER_RUN",
            DEFAULT_MAX_ARTICLES_PER_RUN,
        )

        state = read_ingest_state(CHROMA_PATH)
        published_since = datetime.now(timezone.utc) - timedelta(
            days=retention_days
        )

        def describe(key: IngestKey | None) -> str:
            return f"{key[0].isoformat()} / {key[1]}" if key else "없음"

        print("[정보] 뉴스 청킹 대상 범위")
        print(f"- 보관 기간: 최근 {retention_days}일")
        print(f"- 발행 시각 하한: {published_since.isoformat()}")
        print(f"- 마지막 처리 위치: {describe(state.cursor)}")
        print(
            "- 아직 못 채운 구간: "
            + (
                f"{describe(state.gap_lower)} ~ {describe(state.gap_upper)}"
                if state.has_gap
                else "없음"
            )
        )
        print(f"- 이번 실행 최대 기사 수: {max_articles:,}건")

        client = bigquery.Client(
            project=project_id
        )

        records, new_state, counts = fetch_financial_news(
            client=client,
            project_id=project_id,
            dataset_id=dataset_id,
            location=location,
            published_since=published_since,
            state=state,
            max_articles=max_articles,
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
            window={
                "retention_days": retention_days,
                "published_since": published_since.isoformat(),
                "max_articles": max_articles,
                **counts,
                "gap_remaining": new_state.has_gap,
                "ingest_state_before": state_to_metadata(state),
                "ingest_state_after": state_to_metadata(new_state),
            },
        )

        print("\n[뉴스 청킹 완료]")
        print(f"- 새 기사: {counts['new_rows']:,}행")
        print(f"- 이전 구간 채우기: {counts['backfill_rows']:,}행")
        print(f"- 처리 기사(중복 제외): {len(records):,}건")
        print(f"- 생성 청크: {len(chunks):,}건")

        if new_state.has_gap:
            print(
                "- 아직 못 채운 구간이 남았습니다. "
                "다음 실행에서 이어서 처리합니다."
            )

        if not chunks:
            print("- 새로 처리할 뉴스가 없습니다.")
            return

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
