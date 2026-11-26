"""금융뉴스 임베딩을 로컬 ChromaDB에 반영하고 보관 기간을 적용한다.

처리 순서:
1. 이번 실행의 새 뉴스 청크(임베딩 JSON)를 읽는다. 비어 있어도 된다.
2. 기존 financial_news에서 최근 NEWS_RETENTION_DAYS일(기본 7일) 안에
   발행된 청크만 골라, 새 청크와 함께 임시 컬렉션에 넣는다.
3. 임시 컬렉션 검증이 끝나면 기존 financial_news를 지우고 임시 컬렉션의
   이름을 financial_news로 바꾼다. 중간에 실패하면 기존 컬렉션이 남는다.
4. SQLite VACUUM으로 지운 데이터가 차지하던 공간을 정리한다.

증분 처리 위치(ingest_*)는 새 컬렉션 메타데이터에 함께 기록한다.
새 청크와 진행 위치가 같은 스냅샷에 들어가므로, 업로드 전에 실패한
실행은 다음 실행에서 같은 기사부터 다시 처리한다.

컬렉션을 새로 만드는 이유: ChromaDB는 delete 후에도 벡터 인덱스(HNSW)
파일이 줄지 않고, 삭제·추가를 반복하면 계속 커진다.
financial_terms 컬렉션은 읽지도 수정하지도 않는다.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection


INPUT_PATH = Path(
    "data/processed/financial_news_embeddings.json"
)

CHROMA_PATH = Path("data/chroma")

COLLECTION_NAME = "financial_news"
REBUILD_COLLECTION_NAME = "financial_news__rebuild"
TERMS_COLLECTION_NAME = "financial_terms"

BATCH_SIZE = 200
READ_BATCH_SIZE = 1000
DEFAULT_RETENTION_DAYS = 7
INGEST_STATE_PREFIX = "ingest_"


def load_embedding_records(
    input_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """뉴스 임베딩 JSON 파일을 불러온다."""
    if not input_path.exists():
        raise FileNotFoundError(
            f"입력 파일을 찾을 수 없습니다: {input_path}"
        )

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    metadata = payload.get("metadata")
    records = payload.get("embeddings")

    if not isinstance(metadata, dict):
        raise ValueError(
            "metadata 형식이 올바르지 않습니다."
        )

    if not isinstance(records, list):
        raise ValueError(
            "embeddings 형식이 올바르지 않습니다."
        )

    # 빈 목록은 '새 뉴스 없음'으로 허용한다. 보관 기간 정리는 계속 진행한다.
    return metadata, records


def validate_records(
    records: list[dict[str, Any]],
) -> int | None:
    """ChromaDB 적재 전 뉴스 임베딩을 검증한다."""
    required_fields = {
        "chunk_id",
        "article_id",
        "title",
        "text",
        "embedding",
    }

    chunk_ids: list[str] = []
    embedding_dimension: int | None = None

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(
                f"{index}번째 레코드가 딕셔너리가 아닙니다."
            )

        missing_fields = (
            required_fields - set(record.keys())
        )

        if missing_fields:
            raise ValueError(
                f"{index}번째 레코드의 필드가 부족합니다: "
                f"{sorted(missing_fields)}"
            )

        chunk_id = str(
            record["chunk_id"]
        ).strip()

        text = str(
            record["text"]
        ).strip()

        embedding = record["embedding"]

        if not chunk_id:
            raise ValueError(
                f"{index}번째 chunk_id가 비어 있습니다."
            )

        if not text:
            raise ValueError(
                f"{chunk_id}의 text가 비어 있습니다."
            )

        if not isinstance(embedding, list) or not embedding:
            raise ValueError(
                f"{chunk_id}의 embedding 형식이 잘못되었습니다."
            )

        current_dimension = len(embedding)

        if embedding_dimension is None:
            embedding_dimension = current_dimension

        if current_dimension != embedding_dimension:
            raise ValueError(
                f"{chunk_id}의 임베딩 차원이 다릅니다. "
                f"기준={embedding_dimension}, "
                f"현재={current_dimension}"
            )

        chunk_ids.append(chunk_id)

    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError(
            "입력 데이터에 중복된 chunk_id가 있습니다."
        )

    # 새 레코드가 없으면 None. 차원은 기존 컬렉션에서 확인한다.
    return embedding_dimension


def create_chroma_client(
    chroma_path: Path,
) -> chromadb.PersistentClient:
    """로컬 영구 저장형 ChromaDB 클라이언트를 생성한다."""
    chroma_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return chromadb.PersistentClient(
        path=str(chroma_path)
    )


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


def is_within_retention(
    published_at: Any,
    cutoff: datetime,
) -> bool:
    """발행 시각이 보관 기간 안이면 True. 날짜를 알 수 없으면 False."""
    published = parse_timestamp(published_at)
    return published is not None and published >= cutoff


def extract_ingest_state(
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """메타데이터에서 증분 처리 위치(ingest_*)만 꺼낸다."""
    return {
        key: value
        for key, value in (metadata or {}).items()
        if key.startswith(INGEST_STATE_PREFIX)
    }


def ingest_state_from_input(
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    """임베딩 JSON에 실린 실행 후 진행 위치. 없으면 None."""
    window = (
        (metadata.get("chunking_metadata") or {}).get("fetch_window")
        or {}
    )
    state = window.get("ingest_state_after")

    if state is None:
        return None

    if not isinstance(state, dict) or not all(
        isinstance(key, str)
        and key.startswith(INGEST_STATE_PREFIX)
        and isinstance(value, (str, int))
        for key, value in state.items()
    ):
        raise ValueError(
            f"ingest_state_after 형식이 올바르지 않습니다: {state!r}"
        )

    return dict(state)


def get_collection_or_none(
    client: chromadb.PersistentClient,
    name: str,
) -> Collection | None:
    """컬렉션이 없으면 None을 반환한다."""
    try:
        return client.get_collection(name)
    except Exception:
        return None


def read_kept_news(
    collection: Collection,
    cutoff: datetime,
    exclude_ids: set[str],
) -> tuple[list[dict[str, Any]], int]:
    """기존 뉴스 중 보관 기간 안의 청크를 읽는다.

    exclude_ids(이번 실행의 새 청크)는 새 값으로 대체되므로 건너뛴다.
    반환값: (유지할 청크 목록, 보관 기간이 지나 제외한 청크 수)
    """
    kept: list[dict[str, Any]] = []
    pruned_count = 0
    offset = 0

    while True:
        page = collection.get(
            include=["embeddings", "documents", "metadatas"],
            limit=READ_BATCH_SIZE,
            offset=offset,
        )
        ids = page.get("ids") or []

        if not ids:
            break

        for chunk_id, embedding, document, metadata in zip(
            ids,
            page["embeddings"],
            page["documents"],
            page["metadatas"],
            strict=True,
        ):
            if chunk_id in exclude_ids:
                continue

            metadata = dict(metadata or {})

            if not is_within_retention(
                metadata.get("published_at"),
                cutoff,
            ):
                pruned_count += 1
                continue

            kept.append(
                {
                    "id": chunk_id,
                    "embedding": [float(value) for value in embedding],
                    "document": document,
                    "metadata": metadata,
                }
            )

        offset += len(ids)

    return kept, pruned_count


def add_kept_news(
    collection: Collection,
    kept: list[dict[str, Any]],
    batch_size: int,
) -> None:
    """유지할 기존 청크를 배치 단위로 새 컬렉션에 넣는다."""
    for start_index in range(0, len(kept), batch_size):
        batch = kept[start_index:start_index + batch_size]
        collection.add(
            ids=[item["id"] for item in batch],
            embeddings=[item["embedding"] for item in batch],
            documents=[item["document"] for item in batch],
            metadatas=[item["metadata"] for item in batch],
        )


def rebuild_news_collection(
    client: chromadb.PersistentClient,
    new_records: list[dict[str, Any]],
    embedding_dimension: int | None,
    model_name: str,
    retention_days: int,
    now: datetime,
    ingest_state: dict[str, Any] | None = None,
) -> dict[str, int]:
    """보관 기간 안의 기존 청크와 새 청크로 financial_news를 다시 만든다.

    ingest_state가 None이면 기존 컬렉션의 진행 위치를 그대로 이어받는다.
    """
    cutoff = now - timedelta(days=retention_days)

    # 이전 실행이 남긴 임시 컬렉션이 있으면 지운다.
    if get_collection_or_none(client, REBUILD_COLLECTION_NAME):
        client.delete_collection(REBUILD_COLLECTION_NAME)

    fresh_records = [
        record
        for record in new_records
        if is_within_retention(record.get("published_at"), cutoff)
    ]
    fresh_ids = {str(record["chunk_id"]) for record in fresh_records}

    existing = get_collection_or_none(client, COLLECTION_NAME)
    before_count = existing.count() if existing else 0

    if existing:
        kept, pruned_count = read_kept_news(existing, cutoff, fresh_ids)
        existing_metadata = dict(existing.metadata or {})
    else:
        kept, pruned_count = [], 0
        existing_metadata = {}

    if embedding_dimension is None and kept:
        embedding_dimension = len(kept[0]["embedding"])

    if model_name == "unknown":
        model_name = str(existing_metadata.get("embedding_model", "unknown"))

    if ingest_state is None:
        ingest_state = extract_ingest_state(existing_metadata)

    expected_count = len(kept) + len(fresh_records)

    if expected_count == 0:
        raise ValueError(
            f"최근 {retention_days}일 안의 뉴스 청크가 없습니다. "
            "빈 뉴스 컬렉션으로 스냅샷을 갱신하지 않습니다."
        )

    rebuilt = client.create_collection(
        name=REBUILD_COLLECTION_NAME,
        metadata={
            "description": "FinFlow 금융뉴스 벡터 컬렉션",
            "embedding_model": model_name,
            "embedding_dimension": int(embedding_dimension or 0),
            "source": "Naver News Search API",
            "retention_policy": f"recent_{retention_days}_days",
            **ingest_state,
        },
        configuration={
            "hnsw": {
                "space": "cosine",
            }
        },
    )

    add_kept_news(rebuilt, kept, BATCH_SIZE)
    upsert_records(
        collection=rebuilt,
        records=fresh_records,
        batch_size=BATCH_SIZE,
    )
    validate_collection(
        collection=rebuilt,
        input_chunk_ids=fresh_ids,
    )

    if rebuilt.count() != expected_count:
        raise ValueError(
            "새 뉴스 컬렉션의 청크 수가 예상과 다릅니다: "
            f"예상={expected_count}, 실제={rebuilt.count()}"
        )

    # 여기까지 성공한 뒤에만 기존 컬렉션을 교체한다.
    if existing:
        client.delete_collection(COLLECTION_NAME)

    rebuilt.modify(name=COLLECTION_NAME)

    renamed = client.get_collection(COLLECTION_NAME)

    if extract_ingest_state(renamed.metadata) != ingest_state:
        raise ValueError(
            "뉴스 컬렉션의 진행 위치가 저장되지 않았습니다."
        )

    return {
        "before_count": before_count,
        "new_input_count": len(new_records),
        "new_out_of_window_count": len(new_records) - len(fresh_records),
        "kept_count": len(kept),
        "pruned_count": pruned_count,
        "after_count": expected_count,
    }


def compact_chroma(
    chroma_path: Path,
) -> list[str]:
    """지운 데이터가 차지하던 공간을 정리한다.

    1. SQLite VACUUM
    2. 지운 컬렉션의 벡터 인덱스 폴더 삭제. ChromaDB는 컬렉션을 지워도
       폴더를 남긴다. segments 테이블에 없는 UUID 이름 폴더만 지운다.
    ChromaDB 클라이언트 캐시를 비워 연결을 닫은 뒤 실행한다.
    반환값: 지운 폴더 이름 목록
    """
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()

    connection = sqlite3.connect(chroma_path / "chroma.sqlite3")

    try:
        active_segments = {
            str(row[0])
            for row in connection.execute("SELECT id FROM segments")
        }
        connection.execute("VACUUM")
    finally:
        connection.close()

    removed: list[str] = []

    for child in chroma_path.iterdir():
        if not child.is_dir() or child.name in active_segments:
            continue

        try:
            uuid.UUID(child.name)
        except ValueError:
            continue

        shutil.rmtree(child)
        removed.append(child.name)

    return sorted(removed)


def collection_count(
    client: chromadb.PersistentClient,
    name: str,
) -> int | None:
    """컬렉션 문서 수. 컬렉션이 없으면 None."""
    collection = get_collection_or_none(client, name)
    return collection.count() if collection else None


def safe_string(value: Any) -> str:
    """ChromaDB 메타데이터용 문자열을 만든다."""
    if value is None:
        return ""

    return str(value)


def make_chroma_metadata(
    record: dict[str, Any],
) -> dict[str, str | int | float | bool]:
    """ChromaDB에 저장할 뉴스 메타데이터를 만든다."""
    return {
        "article_id": safe_string(
            record["article_id"]
        ),
        "title": safe_string(
            record["title"]
        ),
        "chunk_index": int(
            record.get("chunk_index", 0)
        ),
        "total_chunks": int(
            record.get("total_chunks", 1)
        ),
        "text_length": int(
            record.get(
                "text_length",
                len(str(record["text"])),
            )
        ),
        "published_at": safe_string(
            record.get("published_at")
        ),
        "collected_at": safe_string(
            record.get("collected_at")
        ),
        "original_url": safe_string(
            record.get("original_url")
        ),
        "naver_url": safe_string(
            record.get("naver_url")
        ),
        "category": safe_string(
            record.get("category")
        ),
        "financial_relevance_score": int(
            record.get(
                "financial_relevance_score",
                0,
            )
            or 0
        ),
        "source_type": safe_string(
            record.get("source_type")
        ),
    }


def upsert_records(
    collection: Collection,
    records: list[dict[str, Any]],
    batch_size: int,
) -> None:
    """뉴스 임베딩을 배치 단위로 추가 또는 갱신한다."""
    total_count = len(records)

    for start_index in range(
        0,
        total_count,
        batch_size,
    ):
        end_index = min(
            start_index + batch_size,
            total_count,
        )

        batch = records[start_index:end_index]

        ids = [
            str(record["chunk_id"])
            for record in batch
        ]

        documents = [
            str(record["text"])
            for record in batch
        ]

        embeddings = [
            record["embedding"]
            for record in batch
        ]

        metadatas = [
            make_chroma_metadata(record)
            for record in batch
        ]

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        print(
            f"[저장] {end_index:,}/{total_count:,}건"
        )


def validate_collection(
    collection: Collection,
    input_chunk_ids: set[str],
) -> None:
    """입력한 청크가 컬렉션에 저장됐는지 검증한다."""
    if not input_chunk_ids:
        return

    stored = collection.get(
        ids=sorted(input_chunk_ids),
        include=[],
    )

    stored_ids = set(stored.get("ids", []))
    missing_ids = input_chunk_ids - stored_ids

    if missing_ids:
        raise ValueError(
            "일부 뉴스 청크가 저장되지 않았습니다: "
            f"{sorted(missing_ids)[:5]}"
        )


def main() -> None:
    """뉴스 ChromaDB 반영과 보관 기간 정리를 실행한다."""
    try:
        retention_days = read_positive_int_env(
            "NEWS_RETENTION_DAYS",
            DEFAULT_RETENTION_DAYS,
        )

        metadata, records = load_embedding_records(
            INPUT_PATH
        )

        embedding_dimension = validate_records(
            records
        )
        ingest_state = ingest_state_from_input(metadata)

        model_name = str(
            metadata.get(
                "model_name",
                "unknown",
            )
        )

        print("[정보] 뉴스 ChromaDB 반영 시작")
        print(f"- 새 입력 청크: {len(records):,}건")
        print(f"- 보관 기간: 최근 {retention_days}일")
        print(f"- 저장 경로: {CHROMA_PATH.resolve()}")

        client = create_chroma_client(
            CHROMA_PATH
        )

        terms_before = collection_count(client, TERMS_COLLECTION_NAME)

        stats = rebuild_news_collection(
            client=client,
            new_records=records,
            embedding_dimension=embedding_dimension,
            model_name=model_name,
            retention_days=retention_days,
            now=datetime.now(timezone.utc),
            ingest_state=ingest_state,
        )

        removed_dirs = compact_chroma(CHROMA_PATH)

        # 정리 후 다시 열어 결과를 확인한다.
        client = create_chroma_client(
            CHROMA_PATH
        )
        terms_after = collection_count(client, TERMS_COLLECTION_NAME)
        news_after = collection_count(client, COLLECTION_NAME)
        state_after = extract_ingest_state(
            client.get_collection(COLLECTION_NAME).metadata
        )

        if ingest_state is not None and state_after != ingest_state:
            raise ValueError(
                "정리 후 뉴스 컬렉션의 진행 위치가 예상과 다릅니다."
            )

        if terms_after != terms_before:
            raise ValueError(
                "금융용어 컬렉션 문서 수가 바뀌었습니다: "
                f"{terms_before} → {terms_after}"
            )

        if news_after != stats["after_count"]:
            raise ValueError(
                "정리 후 뉴스 청크 수가 예상과 다릅니다: "
                f"예상={stats['after_count']}, 실제={news_after}"
            )

        print("\n[뉴스 ChromaDB 반영 완료]")
        print(f"- 반영 전 뉴스 청크: {stats['before_count']:,}건")
        print(f"- 이번 새 청크: {stats['new_input_count']:,}건")
        print(
            "- 보관 기간 밖이라 제외한 새 청크: "
            f"{stats['new_out_of_window_count']:,}건"
        )
        print(f"- 유지한 기존 청크: {stats['kept_count']:,}건")
        print(f"- 보관 기간이 지나 제외한 청크: {stats['pruned_count']:,}건")
        print(f"- 반영 후 뉴스 청크: {news_after:,}건")
        print(f"- 금융용어 컬렉션: {terms_after}건 (변경 없음)")
        print(f"- 정리한 이전 인덱스 폴더: {len(removed_dirs)}개")
        print(
            "- 진행 위치: "
            f"{state_after.get('ingest_cursor_collected_at') or '없음'}"
            " / 남은 구간: "
            f"{'있음' if state_after.get('ingest_gap_upper_collected_at') else '없음'}"
        )

    except FileNotFoundError as exc:
        print(f"[파일 오류] {exc}")
        raise SystemExit(1) from exc

    except ValueError as exc:
        print(f"[검증 오류] {exc}")
        raise SystemExit(1) from exc

    except KeyboardInterrupt:
        print("\n[중단] 사용자가 실행을 중단했습니다.")
        raise SystemExit(130)

    except Exception as exc:
        print(f"[예상하지 못한 오류] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()