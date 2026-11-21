"""금융뉴스 임베딩을 로컬 ChromaDB에 증분 저장한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection


INPUT_PATH = Path(
    "data/processed/financial_news_embeddings.json"
)

CHROMA_PATH = Path("data/chroma")

COLLECTION_NAME = "financial_news"

BATCH_SIZE = 200


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

    if not records:
        raise ValueError(
            "저장할 뉴스 임베딩이 없습니다."
        )

    return metadata, records


def validate_records(
    records: list[dict[str, Any]],
) -> int:
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

    if embedding_dimension is None:
        raise ValueError(
            "임베딩 차원을 확인할 수 없습니다."
        )

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


def get_or_create_collection(
    client: chromadb.PersistentClient,
    embedding_dimension: int,
    model_name: str,
) -> Collection:
    """기존 뉴스 컬렉션을 가져오거나 새로 생성한다."""
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "description": "FinFlow 금융뉴스 벡터 컬렉션",
            "embedding_model": model_name,
            "embedding_dimension": embedding_dimension,
            "source": "Naver News Search API",
            "retention_policy": "recent_1_year",
        },
        configuration={
            "hnsw": {
                "space": "cosine",
            }
        },
    )


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
    """뉴스 ChromaDB 증분 적재 파이프라인을 실행한다."""
    try:
        metadata, records = load_embedding_records(
            INPUT_PATH
        )

        embedding_dimension = validate_records(
            records
        )

        model_name = str(
            metadata.get(
                "model_name",
                "unknown",
            )
        )

        print("[정보] 뉴스 ChromaDB 적재 시작")
        print(f"- 입력 데이터: {len(records):,}건")
        print(f"- 임베딩 차원: {embedding_dimension}")
        print(f"- 임베딩 모델: {model_name}")
        print(f"- 저장 경로: {CHROMA_PATH.resolve()}")
        print(f"- 컬렉션: {COLLECTION_NAME}")

        client = create_chroma_client(
            CHROMA_PATH
        )

        collection = get_or_create_collection(
            client=client,
            embedding_dimension=embedding_dimension,
            model_name=model_name,
        )

        before_count = collection.count()

        upsert_records(
            collection=collection,
            records=records,
            batch_size=BATCH_SIZE,
        )

        input_chunk_ids = {
            str(record["chunk_id"])
            for record in records
        }

        validate_collection(
            collection=collection,
            input_chunk_ids=input_chunk_ids,
        )

        after_count = collection.count()

        print("\n[뉴스 ChromaDB 적재 완료]")
        print(f"- 적재 전 컬렉션 수: {before_count:,}건")
        print(f"- 이번 입력 데이터: {len(records):,}건")
        print(f"- 적재 후 컬렉션 수: {after_count:,}건")
        print(f"- 컬렉션명: {collection.name}")
        print(f"- 저장 위치: {CHROMA_PATH.resolve()}")

        sample = collection.peek(limit=1)

        print("\n[샘플]")
        print("chunk_id:", sample["ids"][0])
        print("metadata:", sample["metadatas"][0])
        print(
            "document:",
            sample["documents"][0][:200],
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