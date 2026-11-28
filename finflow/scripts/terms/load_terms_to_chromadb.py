"""[7/7] 금융용어 임베딩을 로컬 ChromaDB에 저장한다.

입력: data/terms/processed/financial_terms_embeddings.json
출력: --chroma-path (기본 data/terms/chroma)의 financial_terms 컬렉션

운영 데이터 보호:
- 기본 저장 경로는 서비스 DB(data/chroma)와 분리된 data/terms/chroma이다.
- 서비스 DB에 쓰려면 --allow-service-db가 필요하다. 서비스 DB는
  뉴스 Job이 GCS 스냅샷과 동기화하는 경로이다.
- 같은 이름의 컬렉션이 이미 있으면 기본적으로 중단한다.
  --replace를 주면 financial_terms 컬렉션만 **삭제 후 다시 만든다(초기화)**.
  다른 컬렉션(financial_news 등)은 건드리지 않는다.
- GCS 업로드는 하지 않는다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection

import terms_config


INPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_embeddings.json"
)

COLLECTION_NAME = terms_config.TERMS_COLLECTION_NAME

BATCH_SIZE = 200


def load_embedding_records(
    input_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """임베딩 JSON 파일을 읽는다."""
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
            "저장할 임베딩 데이터가 없습니다."
        )

    return metadata, records


def validate_records(
    records: list[dict[str, Any]],
) -> int:
    """ChromaDB 저장 전에 데이터를 검증한다."""
    required_fields = {
        "chunk_id",
        "term_id",
        "term",
        "text",
        "embedding",
    }

    chunk_ids: list[str] = []
    embedding_dimension: int | None = None

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(
                f"{index}번째 데이터가 딕셔너리가 아닙니다."
            )

        missing_fields = (
            required_fields - set(record.keys())
        )

        if missing_fields:
            raise ValueError(
                f"{index}번째 데이터의 필드가 부족합니다: "
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

        if not isinstance(embedding, list):
            raise ValueError(
                f"{chunk_id}의 embedding이 리스트가 아닙니다."
            )

        if not embedding:
            raise ValueError(
                f"{chunk_id}의 embedding이 비어 있습니다."
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
            "중복된 chunk_id가 있습니다."
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

    client = chromadb.PersistentClient(
        path=str(chroma_path)
    )

    return client


def recreate_collection(
    client: chromadb.PersistentClient,
    collection_name: str,
    embedding_dimension: int,
    model_name: str,
    replace: bool = False,
) -> Collection:
    """컬렉션을 새로 만든다. replace=True면 기존 컬렉션을 삭제 후 다시 만든다."""
    existing_names = {
        collection.name
        for collection in client.list_collections()
    }

    if collection_name in existing_names:
        if not replace:
            raise ValueError(
                f"컬렉션 {collection_name}이 이미 있습니다. "
                "삭제 후 다시 만들려면 --replace를 지정하세요."
            )

        print(
            f"[정보] 기존 컬렉션 삭제(초기화): {collection_name}"
        )
        client.delete_collection(
            name=collection_name
        )

    collection = client.create_collection(
        name=collection_name,
        metadata={
            "description": (
                "한국은행 경제금융용어 벡터 컬렉션"
            ),
            "embedding_model": model_name,
            "embedding_dimension": embedding_dimension,
            "source": "한국은행 경제금융용어",
        },
        configuration={
            "hnsw": {
                "space": "cosine"
            }
        },
    )

    return collection


def make_chroma_metadata(
    record: dict[str, Any],
) -> dict[str, str | int | float | bool]:
    """ChromaDB에 저장할 메타데이터를 만든다."""
    return {
        "term_id": str(
            record["term_id"]
        ),
        "term": str(
            record["term"]
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
        "source": str(
            record.get("source", "한국은행")
        ),
        "source_document": str(
            record.get(
                "source_document",
                "경제금융용어",
            )
        ),
        "data_version": str(
            record.get("data_version", "v4")
        ),
    }


def insert_records(
    collection: Collection,
    records: list[dict[str, Any]],
    batch_size: int,
) -> None:
    """임베딩 데이터를 배치 단위로 ChromaDB에 저장한다."""
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

        batch = records[
            start_index:end_index
        ]

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

        collection.add(
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
    expected_count: int,
    expected_dimension: int,
) -> None:
    """저장 건수와 샘플 데이터를 검증한다."""
    actual_count = collection.count()

    if actual_count != expected_count:
        raise ValueError(
            "ChromaDB 저장 건수가 일치하지 않습니다. "
            f"예상={expected_count}, 실제={actual_count}"
        )

    sample = collection.peek(
        limit=1
    )

    sample_ids = sample.get("ids")
    sample_embeddings = sample.get(
        "embeddings"
    )

    if not sample_ids:
        raise ValueError(
            "ChromaDB에서 샘플 데이터를 읽지 못했습니다."
        )

    if sample_embeddings is not None:
        sample_dimension = len(
            sample_embeddings[0]
        )

        if sample_dimension != expected_dimension:
            raise ValueError(
                "저장된 임베딩 차원이 다릅니다. "
                f"예상={expected_dimension}, "
                f"실제={sample_dimension}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="금융용어 임베딩을 ChromaDB에 저장한다."
    )
    parser.add_argument(
        "--chroma-path",
        type=Path,
        default=None,
        help="저장할 ChromaDB 폴더 (기본: data/terms/chroma)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="기존 financial_terms 컬렉션을 삭제 후 다시 만든다.",
    )
    parser.add_argument(
        "--allow-service-db",
        action="store_true",
        help="서비스 DB(data/chroma)에 쓰는 것을 허용한다.",
    )
    return parser.parse_args(argv)


def resolve_chroma_path(
    chroma_path: Path | None,
    allow_service_db: bool,
) -> Path:
    """저장 경로를 정하고 서비스 DB 보호 규칙을 검사한다."""
    path = chroma_path or terms_config.default_chroma_path()

    if not path.is_absolute():
        path = terms_config.PROJECT_ROOT / path

    if terms_config.is_service_chroma_path(path) and not allow_service_db:
        raise ValueError(
            f"{terms_config.display_path(path)}는 서비스 ChromaDB입니다. "
            "쓰려면 --allow-service-db를 지정하세요."
        )

    return path


def main(argv: list[str] | None = None) -> None:
    """금융용어 ChromaDB 적재 파이프라인을 실행한다."""
    args = parse_args(argv)

    try:
        chroma_path = resolve_chroma_path(
            args.chroma_path,
            args.allow_service_db,
        )

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

        print("[정보] ChromaDB 적재 시작")
        print(
            f"- 입력 파일: {terms_config.display_path(INPUT_PATH)}"
        )
        print(
            f"- 입력 데이터: {len(records):,}건"
        )
        print(
            f"- 임베딩 차원: {embedding_dimension}"
        )
        print(
            f"- 임베딩 모델: {model_name}"
        )
        print(
            f"- 저장 경로: {terms_config.display_path(chroma_path)}"
        )
        print(
            f"- 컬렉션: {COLLECTION_NAME}"
        )

        client = create_chroma_client(
            chroma_path
        )

        collection = recreate_collection(
            client=client,
            collection_name=COLLECTION_NAME,
            embedding_dimension=embedding_dimension,
            model_name=model_name,
            replace=args.replace,
        )

        insert_records(
            collection=collection,
            records=records,
            batch_size=BATCH_SIZE,
        )

        validate_collection(
            collection=collection,
            expected_count=len(records),
            expected_dimension=embedding_dimension,
        )

        print("\n[완료] ChromaDB 적재 성공")
        print(
            f"- 컬렉션명: {collection.name}"
        )
        print(
            f"- 저장 건수: {collection.count():,}건"
        )
        print(
            f"- 벡터 차원: {embedding_dimension}"
        )
        print(
            f"- 저장 위치: {terms_config.display_path(chroma_path)}"
        )

        sample = collection.peek(
            limit=1
        )

        print("\n[샘플]")
        print(
            "chunk_id:",
            sample["ids"][0],
        )
        print(
            "metadata:",
            sample["metadatas"][0],
        )
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
        print(
            "\n[중단] 사용자가 실행을 중단했습니다."
        )
        raise SystemExit(130)

    except Exception as exc:
        print(
            f"[예상하지 못한 오류] {exc}"
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
