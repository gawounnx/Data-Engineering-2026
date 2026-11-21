"""FinFlow의 전체 RAG 파이프라인을 실행한다."""

from __future__ import annotations

from typing import Any

from .llm import generate_answer
from .news_retriever import (
    format_news_context,
    search_financial_news,
)
from .prompt import build_rag_prompt
from .query_analyzer import analyze_user_query
from .reranker import rerank_financial_terms
from .retriever import search_financial_terms


DEFAULT_TOP_K = 3


def validate_rag_input(
    query: str,
    top_k: int,
) -> str:
    """RAG 실행에 필요한 입력값을 검증한다."""
    clean_query = query.strip()

    if not clean_query:
        raise ValueError("사용자 질문이 비어 있습니다.")

    if top_k <= 0:
        raise ValueError("top_k는 1 이상의 정수여야 합니다.")

    return clean_query


def extract_sources(
    search_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """검색 결과에서 사용자에게 보여줄 출처 정보를 추출한다."""
    sources: list[dict[str, Any]] = []

    for result in search_results:
        sources.append(
            {
                "rank": result.get("rank"),
                "term": result.get("term", "알 수 없음"),
                "term_id": result.get("term_id", ""),
                "similarity": result.get("similarity", 0.0),
                "document": result.get("document", ""),
            }
        )

    return sources


def extract_news_sources(
    news_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """검색된 뉴스에서 UI에 보여줄 출처 정보를 추출한다."""
    sources: list[dict[str, Any]] = []

    for rank, article in enumerate(news_results, start=1):
        sources.append(
            {
                "rank": rank,
                "title": article.get("title", "제목 없음"),
                "summary": article.get("summary", ""),
                "published_at": article.get("published_at", ""),
                "category": article.get("category", "미분류"),
                "original_url": article.get("original_url", ""),
                "retrieval_score": article.get(
                    "retrieval_score",
                    0.0,
                ),
            }
        )

    return sources


def get_result_identity(result: dict[str, Any]) -> str:
    """검색 결과의 중복 판별용 식별자를 반환한다."""
    metadata = result.get("metadata", {})

    if isinstance(metadata, dict):
        term_id = metadata.get("term_id")
        chunk_id = metadata.get("chunk_id")
        term = metadata.get("term")

        if term_id and chunk_id:
            return f"{term_id}:{chunk_id}"

        if term_id:
            return str(term_id)

        if term:
            return str(term)

    document = result.get("document", "")
    return str(document)[:200]


def get_result_distance(result: dict[str, Any]) -> float:
    """검색 결과의 거리를 반환하며 값이 없으면 낮은 우선순위를 부여한다."""
    distance = result.get("distance")

    if isinstance(distance, (int, float)):
        return float(distance)

    return float("inf")


def merge_search_results(
    result_groups: list[list[dict[str, Any]]],
    top_k: int,
) -> list[dict[str, Any]]:
    """여러 검색 결과를 통합하고 중복 제거 후 거리순으로 정렬한다."""
    unique_results: dict[str, dict[str, Any]] = {}

    for result_group in result_groups:
        for result in result_group:
            if not isinstance(result, dict):
                continue

            identity = get_result_identity(result)
            existing_result = unique_results.get(identity)

            if existing_result is None:
                unique_results[identity] = result
                continue

            if get_result_distance(result) < get_result_distance(existing_result):
                unique_results[identity] = result

    sorted_results = sorted(
        unique_results.values(),
        key=get_result_distance,
    )

    return sorted_results[:top_k]


def ask_finflow(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    news_top_k: int = 5,
) -> dict[str, Any]:
    """질문을 분석하고 금융용어와 뉴스를 검색해 답변을 생성한다."""
    clean_query = validate_rag_input(
        query=query,
        top_k=top_k,
    )

    query_analysis = analyze_user_query(clean_query)

    financial_term_query = query_analysis[
        "financial_term_query"
    ]
    financial_term_queries = query_analysis.get(
        "financial_term_queries",
        [],
    )
    news_query = query_analysis[
        "news_query"
    ]

    candidate_queries: list[str] = [
        clean_query,
        financial_term_query,
    ]

    if isinstance(financial_term_queries, list):
        candidate_queries.extend(
            query
            for query in financial_term_queries
            if isinstance(query, str) and query.strip()
        )

    unique_queries = list(
        dict.fromkeys(
            query.strip()
            for query in candidate_queries
            if query.strip()
        )
    )

    search_result_groups: list[list[dict[str, Any]]] = []
    candidate_top_k = 10

    for retrieval_query in unique_queries:
        results = search_financial_terms(
            query=retrieval_query,
            top_k=candidate_top_k,
        )
        search_result_groups.append(results)

    candidate_results = merge_search_results(
        result_groups=search_result_groups,
        top_k=candidate_top_k,
    )

    rerank_result = rerank_financial_terms(
        query=clean_query,
        candidates=candidate_results,
        top_k=top_k,
    )

    search_results = rerank_result["results"]

    news_results = search_financial_news(
        query=news_query,
        top_k=news_top_k,
    )

    prompt = build_rag_prompt(
        query=clean_query,
        search_results=search_results,
        news_results=news_results,
    )

    answer = generate_answer(
        prompt=prompt,
    )

    sources = extract_sources(
        search_results=search_results,
    )

    news_sources = extract_news_sources(
        news_results=news_results,
    )

    return {
        "query": clean_query,
        "query_analysis": query_analysis,
        "rerank_info": {
            "reason": rerank_result["reason"],
            "used_fallback": rerank_result["used_fallback"],
            "candidate_count": len(candidate_results),
        },
        "answer": answer,
        "sources": sources,
        "news_sources": news_sources,
        "retrieved_count": len(sources),
        "news_retrieved_count": len(news_sources),
    }
