"""검색 화면 표시 규칙 테스트 (리디자인).

검색·RAG·프롬프트 로직은 다루지 않고, 결과를 화면에 옮기는 도우미와 화면 구성만 확인한다.
검색 함수는 고정 예시 결과를 돌려주는 대역이며 Gemini는 호출하지 않는다.
"""

from __future__ import annotations

import re

import pytest

import app_ui
from test_search_form import page_text, search_app  # noqa: F401  (fixture 재사용)

TITLE_WITH_COMMA = "한은, 기준금리 3.00→3.25% 인상…성장률 올해 3.9·내년 4.1% 상향"
EXAMPLE_RESULT = {
    "query": "최근 기준금리 관련 뉴스와 함께 설명해줘.",
    "answer": (
        "한국은행 금융통화위원회는 기준금리를 3.25%로 인상했습니다.\n\n"
        "기준금리는 정책금리입니다.\n\n"
        "참고한 금융용어: 기준금리, 금융통화위원회\n"
        f"참고한 뉴스: {TITLE_WITH_COMMA}"
    ),
    "sources": [
        {"term": "기준금리", "document": "금융용어: 기준금리\n설명: 한국은행 금융통화위원회에서 결정하는 정책금리를 말한다."},
        {"term": "통화정책·파급경로", "document": "금융용어: 통화정책·파급경로\n설명: 통화정책이 실물경제에 파급되는 경로."},
        {"term": "금융통화위원회", "document": "금융용어: 금융통화위원회\n설명: 통화신용정책 최고 의사결정기구."},
    ],
    "news_sources": [
        {
            "title": TITLE_WITH_COMMA,
            "summary": "뉴스 제목: …\n뉴스 내용: 한국은행이 기준금리를 인상했다.",
            "published_at": "2026-11-26T01:20:00+00:00",
            "category": "통화정책",
            "original_url": "http://www.newstomato.com/ReadNews.aspx?no=1321923",
        },
        {
            "title": "<script>alert(1)</script> 다음주 금융시장",
            "summary": "다음 주 결정",
            "published_at": "2026-11-21T00:27:00+00:00",
            "category": "통화정책",
            "original_url": "javascript:alert(1)",
        },
    ],
    "query_analysis": {"intent": "최근 기준금리 동향", "news_query": "기준금리 인상"},
    "rerank_info": {"reason": "정의가 직접적", "candidate_count": 10, "used_fallback": False},
}


# ---------- 도우미 ----------

def test_split_answer_citations_keeps_titles_with_commas():
    body, terms, titles = app_ui.split_answer_citations(
        EXAMPLE_RESULT["answer"],
        terms=["기준금리", "통화정책·파급경로", "금융통화위원회"],
        news_titles=[TITLE_WITH_COMMA, "다른 기사"],
    )

    assert "참고한" not in body
    assert body.endswith("기준금리는 정책금리입니다.")
    assert terms == {"기준금리", "금융통화위원회"}
    assert titles == {TITLE_WITH_COMMA}


def test_unmatched_citation_line_stays_in_body():
    body, terms, titles = app_ui.split_answer_citations(
        "본문\n참고한 뉴스: 검색 결과에 없는 기사",
        terms=[],
        news_titles=["다른 기사"],
    )

    assert "참고한 뉴스: 검색 결과에 없는 기사" in body
    assert titles == set()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-26T01:20:00+00:00", "2026.11.26 10:20"),
        ("2026-11-26T01:20:00Z", "2026.11.26 10:20"),
        ("2026-11-26", "2026-11-26"),  # 시간대가 없으면 원문 그대로
        ("", ""),
    ],
)
def test_format_published_at_in_kst(value, expected):
    assert app_ui.format_published_at(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://news.example.com/a?b=1", "https://news.example.com/a?b=1"),
        ("http://news.example.com", "http://news.example.com"),
        ("javascript:alert(1)", ""),
        ("//no-scheme.example.com", ""),
        ("", ""),
    ],
)
def test_safe_external_url_allows_only_http(value, expected):
    assert app_ui.safe_external_url(value) == expected


def test_compact_html_keeps_word_spaces_and_has_no_indented_lines():
    markup = app_ui.compact_html(
        """
        <p>
            함께 찾아
            쉽게 설명해요
        </p>
        <p>b</p>
        """
    )

    assert "\n" not in markup
    assert "찾아 쉽게" in markup
    assert "</p><p>" in markup


def test_answer_paragraphs_keep_highlight_tags_intact():
    html = app_ui.render_answer_html(
        "첫 문단\n\n기준금리는 정책금리입니다.", terms=["기준금리"]
    )

    assert html.startswith("<p>첫 문단</p><p><mark")
    assert "mark class" not in re.sub(r"<mark class=", "", html)


def test_news_item_escapes_text_and_drops_unsafe_link():
    unsafe = app_ui.render_news_article(EXAMPLE_RESULT["news_sources"][1], index=2)

    assert "<script>" not in unsafe
    assert "&lt;script&gt;" in unsafe
    assert "href" not in unsafe  # javascript: 링크는 만들지 않는다

    safe = app_ui.render_news_article(EXAMPLE_RESULT["news_sources"][0], index=1, cited=True)
    assert 'href="http://www.newstomato.com/ReadNews.aspx?no=1321923"' in safe
    assert 'rel="noopener noreferrer"' in safe
    assert "2026.11.26 10:20" in safe
    assert "newstomato.com" in safe
    assert "답변에 사용" in safe


# ---------- 화면 구성 ----------

def test_first_screen_has_no_fixed_stats_or_tech_badges(search_app):  # noqa: F811
    at, _, _ = search_app
    shown = page_text(at)

    assert len(at.metric) == 0
    for badge in ("HYBRID SEARCH", "FINANCIAL TERMS", "RECENT NEWS", "GEMINI AI"):
        assert badge not in shown


def test_result_screen_shows_answer_then_sources(search_app):  # noqa: F811
    at, spy, _ = search_app
    spy.responses["기준금리 질문"] = EXAMPLE_RESULT

    at.text_input(key="search_question").input("기준금리 질문")
    at.button(key="analyze_button").click().run()

    assert not at.exception
    shown = page_text(at)

    assert "AI 답변" in shown
    assert "최근 기준금리 관련 뉴스와 함께 설명해줘." in shown
    assert "참고한 금융용어:" not in shown  # 출처 줄은 본문에서 분리
    assert [t.label for t in at.tabs] == ["금융용어 근거 3", "관련 뉴스 2"]
    # 답변이 인용한 용어 2개 + 기사 1개에 표시
    assert shown.count("답변에 사용") == 3
    assert any(e.label == "검색 과정 자세히 보기" for e in at.expander)
