"""ChromaDB에서 금융용어를 의미 기반으로 검색하는 모듈."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMA_PATH = PROJECT_ROOT / "data" / "chroma"

COLLECTION_NAME = "financial_terms"
MODEL_NAME = "kekeappa/kor-static-embedding-128"
DEFAULT_TOP_K = 5


def validate_search_input(query: str, top_k: int) -> None:
    """검색어와 검색 개수를 검증한다."""
    if not query.strip():
        raise ValueError("검색어가 비어 있습니다.")

    if top_k <= 0:
        raise ValueError("top_k는 1 이상의 정수여야 합니다.")


def normalize_search_query(query: str) -> str:
    """질문에서 검색에 불필요한 요청 표현을 제거한다."""
    removable_phrases = (
        "에 대해서",
        "에 대해",
        "이 무엇인가요",
        "이 뭔가요",
        "무엇인가요",
        "뭔가요",
        "이해하기 쉽게",
        "쉽게",
        "자세히",
        "설명해줘",
        "설명해 주세요",
        "알려줘",
        "알려 주세요",
    )

    normalized_query = query.strip()

    for phrase in removable_phrases:
        normalized_query = normalized_query.replace(
            phrase,
            " ",
        )

    normalized_query = " ".join(
        normalized_query.split()
    )

    return normalized_query or query.strip()


def normalize_match_text(text: str) -> str:
    """키워드 비교를 위해 공백과 문장부호를 제거한다."""
    return re.sub(
        r"[^0-9a-zA-Z가-힣]",
        "",
        text,
    ).lower()


def create_term_variants(term: str) -> list[str]:
    """괄호 속 약어를 포함한 용어와 제외한 용어를 비교 형태로 만든다."""
    clean_term = term.strip()

    if not clean_term:
        return []

    term_without_parentheses = re.sub(
        r"\([^)]*\)",
        "",
        clean_term,
    ).strip()

    variants = {
        normalize_match_text(clean_term),
        normalize_match_text(term_without_parentheses),
    }

    return [
        variant
        for variant in variants
        if variant
    ]


@lru_cache(maxsize=1)
def load_collection() -> Any:
    """기존 ChromaDB 컬렉션을 불러온다."""
    # 빈 폴더에서 PersistentClient를 열면 빈 DB가 새로 생기므로 먼저 확인한다.
    if not (CHROMA_PATH / "chroma.sqlite3").exists():
        raise FileNotFoundError(
            f"ChromaDB 데이터가 없습니다: {CHROMA_PATH}. "
            "finflow/README.md의 '데이터 준비'에 따라 "
            "스냅샷을 내려받으세요."
        )

    client = chromadb.PersistentClient(
        path=str(CHROMA_PATH)
    )

    collection_names = {
        collection.name
        for collection in client.list_collections()
    }

    if COLLECTION_NAME not in collection_names:
        raise ValueError(
            f"ChromaDB 컬렉션을 찾을 수 없습니다: {COLLECTION_NAME}"
        )

    return client.get_collection(
        name=COLLECTION_NAME
    )


@lru_cache(maxsize=1)
def load_embedding_model() -> SentenceTransformer:
    """금융용어 저장 시 사용한 임베딩 모델을 불러온다."""
    print("[정보] 임베딩 모델을 불러오는 중입니다.")
    print(f"- 모델: {MODEL_NAME}")

    return SentenceTransformer(
        MODEL_NAME,
        device="cpu",
    )


def create_query_embedding(
    query: str,
) -> list[float]:
    """사용자 질문을 임베딩 벡터로 변환한다."""
    model = load_embedding_model()

    embedding = model.encode(
        [query],
        batch_size=1,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )

    return embedding[0].astype(float).tolist()


def distance_to_similarity(distance: float) -> float:
    """코사인 거리를 0~1 범위의 유사도로 변환한다."""
    similarity = 1.0 - distance

    return max(
        0.0,
        min(1.0, similarity),
    )


def format_search_results(
    raw_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """ChromaDB 검색 결과를 사용하기 쉬운 구조로 변환한다."""
    ids = raw_results.get("ids", [[]])[0]
    documents = raw_results.get("documents", [[]])[0]
    metadatas = raw_results.get("metadatas", [[]])[0]
    distances = raw_results.get("distances", [[]])[0]

    formatted_results: list[dict[str, Any]] = []

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
        safe_metadata = metadata or {}
        numeric_distance = float(distance)

        formatted_results.append(
            {
                "rank": rank,
                "chunk_id": chunk_id,
                "term": safe_metadata.get(
                    "term",
                    "알 수 없음",
                ),
                "term_id": safe_metadata.get(
                    "term_id",
                    "알 수 없음",
                ),
                "chunk_index": safe_metadata.get(
                    "chunk_index",
                    0,
                ),
                "document": document,
                "distance": numeric_distance,
                "similarity": distance_to_similarity(
                    numeric_distance
                ),
                "metadata": safe_metadata,
            }
        )

    return formatted_results


def search_financial_terms(
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """키워드 검색과 벡터 검색을 결합해 금융용어를 검색한다."""
    clean_query = query.strip()

    validate_search_input(
        query=clean_query,
        top_k=top_k,
    )

    normalized_query = normalize_search_query(
        clean_query
    )

    collection = load_collection()
    available_count = collection.count()

    if available_count == 0:
        return []

    keyword_results = search_by_keyword(
        keyword=clean_query,
        limit=top_k,
    )

    vector_query = normalized_query

    if keyword_results:
        best_keyword_result = keyword_results[0]
        keyword_similarity = float(
            best_keyword_result.get("similarity", 0.0)
        )

        if keyword_similarity >= 0.9:
            vector_query = str(
                best_keyword_result.get(
                    "term",
                    normalized_query,
                )
            )

    query_embedding = create_query_embedding(
        query=vector_query
    )

    vector_result_count = min(
        max(top_k * 2, top_k),
        available_count,
    )

    raw_vector_results = collection.query(
        query_embeddings=[query_embedding],
        n_results=vector_result_count,
        include=[
            "documents",
            "metadatas",
            "distances",
        ],
    )

    vector_results = format_search_results(
        raw_results=raw_vector_results
    )

    for result in vector_results:
        result["search_type"] = "vector"

    return merge_search_results(
        keyword_results=keyword_results,
        vector_results=vector_results,
        top_k=top_k,
    )


def search_by_keyword(
    keyword: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """질문에 포함된 금융용어명과 문서 키워드를 검색한다."""
    collection = load_collection()

    raw_data = collection.get(
        include=[
            "documents",
            "metadatas",
        ],
    )

    ids = raw_data.get("ids", [])
    documents = raw_data.get("documents", [])
    metadatas = raw_data.get("metadatas", [])

    normalized_keyword = normalize_match_text(keyword)

    if not normalized_keyword:
        return []

    candidates: list[dict[str, Any]] = []
    keyword_results: list[dict[str, Any]] = []
    seen_terms: set[str] = set()

    for chunk_id, document, metadata in zip(
        ids,
        documents,
        metadatas,
        strict=True,
    ):
        safe_metadata = metadata or {}

        term = str(
            safe_metadata.get("term", "")
        ).strip()

        safe_document = document or ""

        term_variants = create_term_variants(term)

        normalized_document = normalize_match_text(
            safe_document
        )

        if not term_variants:
            continue

        term_in_query = any(
            len(term_variant) >= 2
            and term_variant in normalized_keyword
            for term_variant in term_variants
        )

        query_in_term = any(
            normalized_keyword in term_variant
            for term_variant in term_variants
        )

        # 질문 안에 금융용어명이 직접 포함된 경우
        if term_in_query:
            keyword_score = 1.0

        # 질문이 금융용어명의 일부인 경우
        elif query_in_term:
            keyword_score = 0.9

        # 긴 질문 전체가 문서 내용에 들어 있는 경우
        elif normalized_keyword in normalized_document:
            keyword_score = 0.6

        else:
            continue

        candidates.append(
            {
                "chunk_id": chunk_id,
                "term": term or "알 수 없음",
                "term_id": safe_metadata.get(
                    "term_id",
                    "알 수 없음",
                ),
                "chunk_index": safe_metadata.get(
                    "chunk_index",
                    0,
                ),
                "document": safe_document,
                "distance": None,
                "similarity": keyword_score,
                "metadata": safe_metadata,
                "search_type": "keyword",
            }
        )

    candidates.sort(
        key=lambda item: (
            float(item["similarity"]),
            -int(item.get("chunk_index", 0)),
        ),
        reverse=True,
    )

    for candidate in candidates:
        term_id = str(
            candidate.get("term_id", "")
        ).strip()

        term = str(
            candidate.get("term", "")
        ).strip()

        unique_key = term_id or term

        if not unique_key:
            continue

        if unique_key in seen_terms:
            continue

        seen_terms.add(unique_key)
        keyword_results.append(candidate)

        if len(keyword_results) >= limit:
            break

    return keyword_results


def merge_search_results(
    keyword_results: list[dict[str, Any]],
    vector_results: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """검색 결과를 용어 기준으로 병합하고 중복을 제거한다."""
    merged_results: list[dict[str, Any]] = []
    seen_term_ids: set[str] = set()

    for result in keyword_results + vector_results:
        term_id = str(result.get("term_id", "")).strip()
        term = str(result.get("term", "")).strip()

        unique_key = term_id or term

        if not unique_key or unique_key in seen_term_ids:
            continue

        seen_term_ids.add(unique_key)
        merged_results.append(result)

        if len(merged_results) >= top_k:
            break

    for rank, result in enumerate(
        merged_results,
        start=1,
    ):
        result["rank"] = rank

    return merged_results
