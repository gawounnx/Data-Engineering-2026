"""'최근 뉴스' 질문에서 최신 기사가 키워드 점수에 밀리지 않는지 검증한다.

배경 (2026-11-26 실제 Gemini 검증):
"최근 기준금리 관련 뉴스와 함께 설명해줘."에 대해 스냅샷에는 당일 '기준금리 3.25%로 인상'
기사가 있었지만, 최신 가산점(최대 3점)이 키워드 점수 차이보다 작아 결정 전(11/19~22)
예측 기사만 상위 5개에 들었고 답변이 "26일 결정할 예정"이라고 했다.

모두 오프라인: 뉴스 목록·벡터 후보는 대역으로 바꾸고 Gemini는 호출하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import src.news_retriever as nr
import src.rag as rag


def iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# 키워드 점수는 2026-11-26 실제 관측 수준(결정 전 기사 20점, 당일 기사 11~15점)에 맞춘다.
# 한계: 제목에 검색 키워드가 전부 들어간 옛 기사는 최근 의도에서도 앞설 수 있다.
# 결정 전 예측 기사: 키워드가 더 많이 맞지만 5일 전 (키워드 점수 18)
OLD_PREVIEW = {
    "article_id": "old-preview",
    "title": "기준금리 결정 앞두고 시장 분수령",
    "summary": "한국은행 금융통화위원회 동결 인하 전망",
    "published_at": iso_hours_ago(24 * 5),
    "financial_relevance_score": 3,
}
# 당일 결정 기사: 키워드는 덜 맞지만 2시간 전 (키워드 점수 9)
NEW_DECISION = {
    "article_id": "new-decision",
    "title": "한은, 기준금리 3.25%로 인상",
    "summary": "금융통화위원회가 오늘 인상을 결정했다",
    "published_at": iso_hours_ago(2),
    "financial_relevance_score": 3,
}
NEWS_QUERY = "한국은행 금융통화위원회 기준금리 동결 인하 결정"


@pytest.fixture
def news_pool(monkeypatch):
    """뉴스 목록을 두 기사로 고정하고 벡터 후보는 비운다(키워드 경로만 사용)."""
    articles = [OLD_PREVIEW, NEW_DECISION]
    monkeypatch.setattr(nr, "load_news_articles", lambda *a, **k: articles)
    monkeypatch.setattr(nr, "search_vector_candidates", lambda **k: [])
    monkeypatch.delenv("NEWS_RETENTION_DAYS", raising=False)
    return articles


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("최근 기준금리 관련 뉴스와 함께 설명해줘.", True),
        ("오늘 금통위 결과", True),
        ("요즘 환율 왜 이래", True),
        ("이번 주 금리 뉴스", True),
        ("기준금리가 뭐야?", False),
        ("반도체 수출 실적 전망", False),
    ],
)
def test_has_recency_intent(query, expected):
    assert nr.has_recency_intent(query) is expected


def test_default_recency_score_is_unchanged():
    """최근 의도가 없으면 기존 0~3점 선형 감소를 그대로 쓴다."""
    assert nr.calculate_recency_score(iso_hours_ago(0.01), 7) == pytest.approx(3.0, abs=0.01)
    assert nr.calculate_recency_score(iso_hours_ago(24 * 3.5), 7) == pytest.approx(1.5, abs=0.01)
    assert nr.calculate_recency_score(iso_hours_ago(24 * 8), 7) == -100.0


def test_recent_intent_bonus_scales_with_keywords_and_halves():
    fresh = nr.calculate_recency_score(iso_hours_ago(0.01), 7, prefer_recent=True, keyword_count=7)
    half = nr.calculate_recency_score(
        iso_hours_ago(24 * nr.RECENT_INTENT_HALF_LIFE_DAYS), 7, prefer_recent=True, keyword_count=7
    )
    few = nr.calculate_recency_score(iso_hours_ago(0.01), 7, prefer_recent=True, keyword_count=1)

    assert fresh == pytest.approx(7 * nr.RECENT_INTENT_BONUS_PER_KEYWORD, rel=0.01)
    assert half == pytest.approx(fresh / 2, rel=0.01)
    assert few == pytest.approx(nr.RECENT_INTENT_MIN_BONUS, rel=0.01)
    # 기간 밖은 최근 의도여도 제외
    assert nr.calculate_recency_score(iso_hours_ago(24 * 8), 7, prefer_recent=True, keyword_count=7) == -100.0


def test_without_recency_intent_keyword_heavy_old_article_stays_first(news_pool):
    results = nr.search_financial_news(NEWS_QUERY, top_k=2, prefer_recent=False)

    assert [r["article_id"] for r in results] == ["old-preview", "new-decision"]


def test_recency_intent_puts_same_day_decision_first(news_pool):
    results = nr.search_financial_news(NEWS_QUERY, top_k=2, prefer_recent=True)

    assert results[0]["article_id"] == "new-decision"


def test_recency_intent_is_inferred_from_query_when_not_given(news_pool):
    results = nr.search_financial_news("최근 " + NEWS_QUERY, top_k=1)

    assert results[0]["article_id"] == "new-decision"


def test_newest_candidates_are_added_when_keyword_pool_is_full(news_pool, monkeypatch):
    """키워드 점수 순 후보 한도가 차서 새 기사가 빠져도 최신순 후보로 들어온다."""
    monkeypatch.setattr(nr, "VECTOR_CANDIDATE_COUNT", 1)

    plain = nr.search_financial_news(NEWS_QUERY, top_k=5, prefer_recent=False)
    recent = nr.search_financial_news(NEWS_QUERY, top_k=5, prefer_recent=True)

    assert [r["article_id"] for r in plain] == ["old-preview"]
    assert recent[0]["article_id"] == "new-decision"


def test_rag_keeps_user_recency_intent_when_analysis_drops_it(monkeypatch):
    """질문 분석이 news_query에서 '최근'을 빼도 사용자 질문의 의도를 넘긴다."""
    captured = {}

    monkeypatch.setattr(
        rag,
        "analyze_user_query",
        lambda q: {
            "intent": "",
            "financial_term_query": "기준금리",
            "financial_term_queries": [],
            "news_query": NEWS_QUERY,  # '최근' 없음
            "keywords": [],
            "used_fallback": False,
        },
    )
    monkeypatch.setattr(rag, "search_financial_terms", lambda **k: [])
    monkeypatch.setattr(
        rag,
        "rerank_financial_terms",
        lambda **k: {"results": [], "reason": "", "used_fallback": False},
    )
    monkeypatch.setattr(
        rag, "search_financial_news", lambda **k: captured.update(k) or []
    )
    monkeypatch.setattr(rag, "generate_answer", lambda prompt: "ok")

    rag.ask_finflow("최근 기준금리 관련 뉴스와 함께 설명해줘.")
    assert captured["prefer_recent"] is True

    rag.ask_finflow("기준금리가 뭐야?")
    assert captured["prefer_recent"] is False
