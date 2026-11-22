"""[6/7] 금융용어 청크를 임베딩 벡터로 변환한다.

입력: data/terms/processed/financial_terms_chunks.json
출력: data/terms/processed/financial_terms_embeddings.json
모델: kekeappa/kor-static-embedding-128 (앱 검색 모델과 같아야 한다)
캐시에 모델이 없으면 Hugging Face Hub에서 내려받는다.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

import terms_config


INPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_chunks.json"
)

OUTPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_embeddings.json"
)

MODEL_NAME = terms_config.EMBEDDING_MODEL_NAME
BATCH_SIZE = 16


def load_chunks(
    input_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """청킹 결과 JSON을 불러온다."""
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
    chunks = payload.get("chunks")

    if not isinstance(metadata, dict):
        raise ValueError(
            "metadata 형식이 올바르지 않습니다."
        )

    if not isinstance(chunks, list):
        raise ValueError(
            "chunks 형식이 올바르지 않습니다."
        )

    if not chunks:
        raise ValueError(
            "임베딩할 청크가 없습니다."
        )

    return metadata, chunks


def validate_chunks(
    chunks: list[dict[str, Any]],
) -> None:
    """임베딩 전에 청크 데이터를 검증한다."""
    required_fields = {
        "chunk_id",
        "term_id",
        "term",
        "text",
    }

    chunk_ids: list[str] = []

    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise ValueError(
                f"{index}번째 청크가 딕셔너리가 아닙니다."
            )

        missing_fields = (
            required_fields - set(chunk.keys())
        )

        if missing_fields:
            raise ValueError(
                f"{index}번째 청크의 필드가 부족합니다: "
                f"{sorted(missing_fields)}"
            )

        chunk_id = str(chunk["chunk_id"]).strip()
        text = str(chunk["text"]).strip()

        if not chunk_id:
            raise ValueError(
                f"{index}번째 청크의 chunk_id가 비어 있습니다."
            )

        if not text:
            raise ValueError(
                f"{chunk_id}의 text가 비어 있습니다."
            )

        chunk_ids.append(chunk_id)

    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError(
            "중복된 chunk_id가 있습니다."
        )


def load_model(
    model_name: str,
) -> SentenceTransformer:
    """Sentence Transformer 모델을 불러온다."""
    print("[정보] 임베딩 모델 로딩")
    print(f"- 모델: {model_name}")

    model = SentenceTransformer(
        model_name_or_path=model_name,
        device="cpu",
    )

    return model


def create_embeddings(
    model: SentenceTransformer,
    chunks: list[dict[str, Any]],
) -> np.ndarray:
    """청크 텍스트를 임베딩 벡터로 변환한다."""
    texts = [
        str(chunk["text"])
        for chunk in chunks
    ]

    print("[정보] 임베딩 생성 시작")
    print(f"- 청크 수: {len(texts):,}건")
    print(f"- 배치 크기: {BATCH_SIZE}")
    print("- 실행 장치: CPU")

    embeddings = model.encode(
        sentences=texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )

    return embeddings


def validate_embeddings(
    chunks: list[dict[str, Any]],
    embeddings: np.ndarray,
) -> None:
    """생성된 임베딩의 크기와 값 등을 검증한다."""
    if embeddings.ndim != 2:
        raise ValueError(
            "임베딩 배열이 2차원이 아닙니다."
        )

    if len(chunks) != embeddings.shape[0]:
        raise ValueError(
            "청크 수와 임베딩 수가 일치하지 않습니다."
        )

    if embeddings.shape[1] <= 0:
        raise ValueError(
            "임베딩 차원이 올바르지 않습니다."
        )

    if np.isnan(embeddings).any():
        raise ValueError(
            "임베딩에 NaN 값이 포함되어 있습니다."
        )

    if np.isinf(embeddings).any():
        raise ValueError(
            "임베딩에 무한대 값이 포함되어 있습니다."
        )

    vector_norms = np.linalg.norm(
        embeddings,
        axis=1,
    )

    average_norm = float(
        np.mean(vector_norms)
    )

    if not np.isclose(
        average_norm,
        1.0,
        atol=0.01,
    ):
        print(
            "[경고] 평균 벡터 크기가 1과 다릅니다:",
            round(average_norm, 4),
        )


def build_embedding_records(
    chunks: list[dict[str, Any]],
    embeddings: np.ndarray,
) -> list[dict[str, Any]]:
    """청크 정보와 임베딩 벡터를 결합한다."""
    records: list[dict[str, Any]] = []

    for chunk, embedding in zip(
        chunks,
        embeddings,
        strict=True,
    ):
        record = {
            "chunk_id": chunk["chunk_id"],
            "term_id": chunk["term_id"],
            "term": chunk["term"],
            "chunk_index": chunk.get(
                "chunk_index",
                0,
            ),
            "total_chunks": chunk.get(
                "total_chunks",
                1,
            ),
            "text": chunk["text"],
            "text_length": chunk.get(
                "text_length",
                len(str(chunk["text"])),
            ),
            "embedding": embedding.astype(
                float
            ).tolist(),
            "source": chunk.get(
                "source",
                "한국은행",
            ),
            "source_document": chunk.get(
                "source_document",
                "경제금융용어",
            ),
            "data_version": chunk.get(
                "data_version",
                "v3",
            ),
        }

        records.append(record)

    return records


def calculate_statistics(
    embeddings: np.ndarray,
) -> dict[str, float | int]:
    """생성된 임베딩의 통계를 계산한다."""
    vector_norms = np.linalg.norm(
        embeddings,
        axis=1,
    )

    return {
        "embedding_count": int(
            embeddings.shape[0]
        ),
        "embedding_dimension": int(
            embeddings.shape[1]
        ),
        "minimum_vector_norm": round(
            float(np.min(vector_norms)),
            6,
        ),
        "maximum_vector_norm": round(
            float(np.max(vector_norms)),
            6,
        ),
        "average_vector_norm": round(
            statistics.mean(
                vector_norms.tolist()
            ),
            6,
        ),
    }


def save_embeddings(
    input_metadata: dict[str, Any],
    records: list[dict[str, Any]],
    statistics_data: dict[str, float | int],
    elapsed_seconds: float,
    output_path: Path,
) -> None:
    """임베딩 결과를 JSON으로 저장한다."""
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "metadata": {
            "dataset_name": (
                "financial_terms_embeddings"
            ),
            "source_chunk_file": terms_config.display_path(
                INPUT_PATH
            ),
            "source_chunk_count": (
                len(records)
            ),
            "model_name": MODEL_NAME,
            "batch_size": BATCH_SIZE,
            "device": "cpu",
            "normalized": True,
            "elapsed_seconds": round(
                elapsed_seconds,
                2,
            ),
            "embedding_statistics": (
                statistics_data
            ),
            "chunking_metadata": (
                input_metadata
            ),
        },
        "embeddings": records,
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


def print_samples(
    records: list[dict[str, Any]],
    dimension: int,
) -> None:
    """임베딩 결과 일부를 출력한다."""
    print("\n[샘플]")

    for record in records[:3]:
        vector = record["embedding"]

        print("-" * 60)
        print(f"chunk_id: {record['chunk_id']}")
        print(f"term: {record['term']}")
        print(f"vector_dimension: {dimension}")
        print(
            "vector_preview:",
            vector[:5],
        )


def main() -> None:
    """금융용어 임베딩 파이프라인을 실행한다."""
    started_at = time.perf_counter()

    try:
        input_metadata, chunks = load_chunks(
            INPUT_PATH
        )

        validate_chunks(chunks)

        print("[정보] 금융용어 임베딩 시작")
        print(
            f"- 입력 파일: {INPUT_PATH.resolve()}"
        )
        print(
            f"- 입력 청크: {len(chunks):,}건"
        )

        model = load_model(MODEL_NAME)

        embeddings = create_embeddings(
            model=model,
            chunks=chunks,
        )

        validate_embeddings(
            chunks=chunks,
            embeddings=embeddings,
        )

        records = build_embedding_records(
            chunks=chunks,
            embeddings=embeddings,
        )

        statistics_data = calculate_statistics(
            embeddings
        )

        elapsed_seconds = (
            time.perf_counter() - started_at
        )

        save_embeddings(
            input_metadata=input_metadata,
            records=records,
            statistics_data=statistics_data,
            elapsed_seconds=elapsed_seconds,
            output_path=OUTPUT_PATH,
        )

        print("\n[완료] 금융용어 임베딩 성공")
        print(
            f"- 임베딩 개수: "
            f"{statistics_data['embedding_count']:,}개"
        )
        print(
            f"- 벡터 차원: "
            f"{statistics_data['embedding_dimension']}차원"
        )
        print(
            f"- 평균 벡터 크기: "
            f"{statistics_data['average_vector_norm']}"
        )
        print(
            f"- 실행 시간: "
            f"{elapsed_seconds:.2f}초"
        )
        print(
            f"- 결과 파일: {OUTPUT_PATH.resolve()}"
        )

        print_samples(
            records=records,
            dimension=int(
                statistics_data[
                    "embedding_dimension"
                ]
            ),
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
