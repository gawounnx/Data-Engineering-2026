"""정확히 일치한 금융용어가 후보 순위에서 밀리지 않는지 검증한다.

임베딩 모델과 Gemini는 가짜로 대체한다.
"""

from __future__ import annotations

import numpy as np
import pytest

import chromadb
import src.rag as rag
import src.retriever as retriever

DIM = 16
OTHER_TERMS = [f"용어{i:02d}" for i in range(14)]


class FakeModel:
    """질문을 항상 같은 벡터로 바꾼다."""

    def encode(self, texts, **kwargs):
        return np.array([[1.0] + [0.0] * (DIM - 1) for _ in texts])


@pytest.fixture
def terms_db(isolated, monkeypatch):
    """'기준금리'와 질문 벡터에 더 가까운 다른 용어 14개."""
    _, local_chroma = isolated
    client = chromadb.PersistentClient(path=str(local_chroma))
    collection = client.get_or_create_collection(
        "financial_terms",
        metadata={"hnsw:space": "cosine"},
    )
    rng = np.random.default_rng(0)
    names = ["기준금리", *OTHER_TERMS]
    embeddings = [[0.0, 1.0] + [0.0] * (DIM - 2)]  # 기준금리는 벡터상 가장 멂
    embeddings += [list(np.r_[0.9, rng.normal(0, 0.05, DIM - 1)]) for _ in OTHER_TERMS]
    collection.add(
        ids=[f"t{i}_0" for i in range(len(names))],
        documents=[f"{name} 설명" for name in names],
        metadatas=[
            {"term": name, "term_id": f"t{i}", "chunk_index": 0}
            for i, name in enumerate(names)
        ],
        embeddings=embeddings,
    )
    monkeypatch.setattr(retriever, "load_embedding_model", lambda: FakeModel())
    retriever.load_collection.cache_clear()
    return collection


def test_keyword_match_has_finite_distance(terms_db):
    results = retriever.search_by_keyword(keyword="기준금리", limit=5)

    assert results[0]["term"] == "기준금리"
    assert results[0]["distance"] == pytest.approx(0.0)
    assert results[0]["similarity"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "term_queries",
    [["기준금리"], ["기준금리", "기준금리 인상"]],
    ids=["single-query", "multi-query"],
)
def test_exact_term_is_first_rerank_candidate(terms_db, monkeypatch, term_queries):
    captured = {}

    def fake_rerank(query, candidates, top_k=3):
        captured["terms"] = [c["term"] for c in candidates]
        return {"results": candidates[:top_k], "reason": "", "used_fallback": True}

    monkeypatch.setattr(
        rag,
        "analyze_user_query",
        lambda q: {
            "intent": "",
            "financial_term_query": "기준금리",
            "financial_term_queries": term_queries,
            "news_query": "기준금리",
            "keywords": [],
            "used_fallback": False,
        },
    )
    monkeypatch.setattr(rag, "rerank_financial_terms", fake_rerank)
    monkeypatch.setattr(rag, "search_financial_news", lambda **kwargs: [])
    monkeypatch.setattr(rag, "generate_answer", lambda prompt: "ok")

    result = rag.ask_finflow("기준금리가 뭐야?")

    assert captured["terms"][0] == "기준금리"
    # 재정렬이 대체 동작(상위 3개)으로 떨어져도 최종 근거에 포함된다.
    assert "기준금리" in [source.get("term") for source in result["sources"]]
