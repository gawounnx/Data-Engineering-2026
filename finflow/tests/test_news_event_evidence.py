"""뉴스 결과 보정의 대상, 관측 시각과 후보 제한을 오프라인으로 검증한다."""

from datetime import datetime
from unittest import TestCase
from unittest.mock import patch

from src import news_retriever as news
from src import rag


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        current = datetime.fromisoformat("2026-10-16T12:00:00+09:00")
        return current.astimezone(tz) if tz else current.replace(tzinfo=None)


def article(article_id, title, published_at="2026-10-14T02:00:00+00:00", summary=""):
    return {
        "article_id": article_id,
        "title": title,
        "published_at": published_at,
        "summary": summary,
    }


class NewsEventEvidenceTest(TestCase):
    def setUp(self):
        clock = patch.object(news, "datetime", FixedClock)
        clock.start()
        self.addCleanup(clock.stop)

    def test_reworded_policy_questions(self):
        articles = [article("decision", "금통위, 정책금리 2.625%로 유지")]
        for question in (
            "한국은행에서 정책 금리를 어떻게 정했나요?",
            "금통위가 기준 금리를 얼마로 정했나요?",
            "한은이 결정한 정책 금리 알려줘",
        ):
            with self.subTest(question=question):
                self.assertEqual(news.find_event_evidence(articles, question, 30), {"decision"})

    def test_dates_use_korean_time_and_common_notation(self):
        articles = [
            article("on-date", "한국은행, 기준금리 2.625%로 동결", "2026-10-13T15:10:00Z"),
            article("other-date", "한국은행, 기준금리 2.875%로 인상", "2026-10-15T02:10:00Z"),
        ]
        for day in ("2026년 10월 14일", "2026-10-14", "2026.10.14", "10월 14일"):
            with self.subTest(day=day):
                question = f"{day} 한국은행 기준금리 결정 알려줘"
                self.assertEqual(news.find_event_evidence(articles, question, 30), {"on-date"})
        self.assertEqual(
            news.find_event_evidence(articles, "어제 한국은행 기준금리 발표 알려줘", 30),
            {"other-date"},
        )
        self.assertFalse(news.find_event_evidence(articles, "2026-10-32 한국은행 기준금리 결정", 30))

    def test_fx_requires_matching_pair_policy_event_and_day(self):
        articles = [
            article("direct", "원-달러 환율, 금통위 금리 인하 후 1,512원대로 올랐다"),
            article("wrong-pair", "엔·달러 환율, 한은 금리 인하 후 154엔으로 올랐다"),
            article("wrong-bank", "원·달러 환율, 한은 인용한 연준 금리 인하 후 1,510원대로 올랐다"),
            article("wrong-event", "원·달러 환율 올라, 한은 금리 일정은 다음 주"),
            article("wrong-day", "원·달러 환율, 금통위 금리 인하에 1,527원 위로", "2026-10-15T02:00:00Z"),
        ]
        question = "2026-10-14 한국은행의 정책 금리 인하 뒤 원-달러 환율 흐름은?"
        self.assertEqual(news.find_event_evidence(articles, question, 30), {"direct"})
        for unrelated in (
            "2026-10-14 한국은행 금리 인하 직전 원·달러 환율은?",
            "2026-10-14 한국은행 금리 인하 이후 엔·달러 환율은?",
            "미 연준이 발표한 기준금리 결과는?",
            "유가 선물 가격 확정 내용을 알려줘",
        ):
            with self.subTest(question=unrelated):
                self.assertFalse(news.find_event_evidence(articles, unrelated, 30))

    def test_forecasts_and_irrelevant_confirmations_do_not_qualify(self):
        articles = [
            article("reported", "한은, 기준금리 2.625%로 인하…추가 인하 전망"),
            article("forecast", "한은, 기준금리 2.875%로 인상…가능성 커져"),
            article("unconfirmed", "한국은행, 기준금리 3.125%로 인상…당국 ‘확정 아냐’"),
            article("rumor", "한은, 기준금리 3.375%로 인상 확정?"),
            article("wrong-actor", "한은, 미 연준 기준금리 4.625%로 동결 평가"),
            article("unrelated", "한은 연구원, 금융 박람회 일정 확정"),
        ]
        question = "한국은행의 기준금리 결정 내용을 알려줘"
        self.assertEqual(news.find_event_evidence(articles, question, 30), {"reported"})
        for upcoming in ("다음 한국은행 기준금리 결정은?", "한국은행의 다음 기준금리 결정은?"):
            self.assertFalse(news.find_event_evidence(articles, upcoming, 30))

    def test_no_confirmed_report_keeps_hybrid_ranking(self):
        articles = [
            article("forecast", "한은, 기준금리 2.875%로 인상…가능성 확대"),
            article("background", "한국은행과 기준금리 결정의 기존 통화정책 배경"),
            article("other", "대출 우대금리 인상 확정"),
        ]
        query = "한국은행 기준금리 결정 통화정책 배경"
        with (
            patch.object(news, "load_news_articles", return_value=articles),
            patch.object(news, "search_vector_candidates", return_value=[]),
            patch.object(news, "find_event_evidence", return_value=frozenset()),
        ):
            expected = news.search_financial_news(query, original_query=query)
        with (
            patch.object(news, "load_news_articles", return_value=articles),
            patch.object(news, "search_vector_candidates", return_value=[]),
        ):
            self.assertEqual(news.search_financial_news(query, original_query=query), expected)

    def test_direct_evidence_is_admitted_before_truncation(self):
        articles = [
            article("direct", "금통위, 기준금리 2.625%로 동결"),
            article("forecast", "한국은행 금리 결정 전망과 경기 동향"),
            article("decoy", "경기 동향과 전망 발표 확정"),
        ]
        with (
            patch.object(news, "load_news_articles", return_value=articles),
            patch.object(news, "search_vector_candidates", return_value=[]),
            patch.object(news, "VECTOR_CANDIDATE_COUNT", 1),
        ):
            results = news.search_financial_news(
                "경기 동향 전망", top_k=1, original_query="한국은행 기준금리 결정 알려줘"
            )
        self.assertEqual([row["article_id"] for row in results], ["direct"])

    def test_rag_preserves_original_date_when_rewritten_query_drops_it(self):
        articles = [
            article("correct", "한은, 기준금리 2.625%로 동결"),
            article("other-date", "한은, 기준금리 2.875%로 인상", "2026-10-15T02:00:00Z"),
        ]
        analysis = {
            "financial_term_query": "기준금리",
            "news_query": "최근 한국은행 금리 결정",
            "used_fallback": False,
        }
        with (
            patch.object(rag, "analyze_user_query", return_value=analysis),
            patch.object(rag, "search_financial_terms", return_value=[]),
            patch.object(rag, "generate_answer", return_value="오프라인 응답"),
            patch.object(news, "load_news_articles", return_value=articles),
            patch.object(news, "search_vector_candidates", return_value=[]),
        ):
            result = rag.ask_finflow("2026-10-14 한국은행 기준금리 결정 알려줘")
        self.assertEqual(result["news_sources"][0]["title"], articles[0]["title"])

    def test_two_prioritized_slots_preserve_other_score_order(self):
        articles = [
            article("economic", "한국은행 기준금리 결정과 통화정책 경제 성장 분석"),
            article("confirmed-first", "한국은행, 기준금리 2.625%로 인상…경제 배경"),
            article("confirmed-second", "한국은행, 기준금리 2.625%로 인상"),
            article("confirmed-third", "금통위, 기준금리 2.625%로 인상"),
        ]
        with (
            patch.object(news, "load_news_articles", return_value=articles),
            patch.object(news, "search_vector_candidates", return_value=[]),
        ):
            results = news.search_financial_news(
                "한국은행 기준금리 결정 통화정책 경제 성장",
                top_k=4,
                original_query="한국은행 기준금리 결정 알려줘",
            )
        self.assertEqual(
            [row["article_id"] for row in results],
            ["confirmed-first", "confirmed-second", "economic", "confirmed-third"],
        )
