"""FinFlow 금융뉴스 ChromaDB 벡터 검색을 테스트한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer


CHROMA_PATH = Path("data/chroma")
COLLECTION_NAME = "financial_news"
MODEL_NAME = "kekeappa/kor-static-embedding-128"


def search_news(
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """질문과 의미가 유사한 금융뉴스를 검색한다."""
    clean_query = query.strip()

    if not clean_query:
        raise ValueError("검색어가 비어 있습니다.")

    if top_k <= 0:
        raise ValueError("top_k는 1 이상이어야 합니다.")

    client = chromadb.PersistentClient(
        path=str(CHROMA_PATH)
    )

    collection = client.get_collection(
        name=COLLECTION_NAME
    )

    model = SentenceTransformer(
        model_name_or_path=MODEL_NAME,
        device="cpu",
    )

    query_embedding = model.encode(
        [clean_query],
        convert_to_numpy=True,
        normalize_embeddings=True,
    )[0].astype(float).tolist()

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(
            top_k,
            collection.count(),
        ),
        include=[
            "documents",
            "metadatas",
            "distances",
        ],
    )

    ids = result.get("ids", [[]])[0]
    documents = result.get(
        "documents",
        [[]],
    )[0]
    metadatas = result.get(
        "metadatas",
        [[]],
    )[0]
    distances = result.get(
        "distances",
        [[]],
    )[0]

    search_results: list[dict[str, Any]] = []

    for rank, (
        chunk_id,
        document,
        metadata,
        distance,
    ) in enumerate(
        zip(
            ids,
            documents,
            metadatas,
            distances,
            strict=True,
        ),
        start=1,
    ):
        similarity = max(
            0.0,
            1.0 - float(distance),
        )

        search_results.append(
            {
                "rank": rank,
                "chunk_id": chunk_id,
                "title": metadata.get(
                    "title",
                    "제목 없음",
                ),
                "summary": document,
                "published_at": metadata.get(
                    "published_at",
                    "",
                ),
                "category": metadata.get(
                    "category",
                    "미분류",
                ),
                "original_url": metadata.get(
                    "original_url",
                    "",
                ),
                "retrieval_score": round(
                    similarity,
                    4,
                ),
                "distance": round(
                    float(distance),
                    4,
                ),
            }
        )

    return search_results


def main() -> None:
    """여러 금융 질문으로 뉴스 벡터 검색을 시험한다."""
    test_queries = [
        "반도체 기업의 실적과 주가 전망",
        "미국 금리와 금융시장 영향",
        "코스피가 변동한 이유",
        "환율과 달러 강세 영향",
    ]

    for query in test_queries:
        print("\n" + "=" * 80)
        print(f"[질문] {query}")

        results = search_news(
            query=query,
            top_k=5,
        )

        for result in results:
            print("-" * 80)
            print(
                f"{result['rank']}위 | "
                f"유사도={result['retrieval_score']} | "
                f"카테고리={result['category']}"
            )
            print(
                f"제목: {result['title']}"
            )
            print(
                f"발행일: {result['published_at']}"
            )


if __name__ == "__main__":
    main()