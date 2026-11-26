"""새 질문이 실패했을 때 이전 질문의 답변·출처가 남지 않는지 검증한다.

질문 A 성공 → 질문 B 실패 순서로 제출했을 때, 화면에는 B의 오류만 있어야 하고
A의 답변·금융용어 근거·뉴스 출처가 B의 결과처럼 보이면 안 된다.
검색 함수는 대역이며 실제 Gemini는 호출하지 않는다.
"""

from __future__ import annotations

from test_search_form import page_text, search_app  # noqa: F401  (fixture 재사용)

QUESTION_A = "기준금리 인상 영향"
QUESTION_B = "환율 급등 원인"
ANSWER_A = "A 질문에 대한 고유 답변 문장"
TERM_A = "A전용금융용어"
NEWS_A = "A전용 뉴스 제목"
ERROR_MESSAGE = "답변을 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def submit(at, question: str):
    at.text_input(key="search_question").input(question)
    return at.button(key="analyze_button").click().run()


def test_failed_question_does_not_show_previous_answer(search_app):  # noqa: F811
    at, spy, _ = search_app
    spy.responses[QUESTION_A] = {
        "answer": ANSWER_A,
        "references": [{"term": TERM_A, "document": "A 원문"}],
        "news_sources": [{"title": NEWS_A, "summary": "A 뉴스 요약"}],
    }
    spy.responses[QUESTION_B] = RuntimeError("upstream timeout")

    submit(at, QUESTION_A)

    assert not at.exception
    shown_a = page_text(at)
    assert ANSWER_A in shown_a
    assert TERM_A in shown_a
    assert NEWS_A in shown_a

    submit(at, QUESTION_B)

    assert not at.exception
    assert spy.calls == [QUESTION_A, QUESTION_B]
    assert [e.value for e in at.error] == [ERROR_MESSAGE]

    shown_b = page_text(at)
    assert ANSWER_A not in shown_b
    assert TERM_A not in shown_b
    assert NEWS_A not in shown_b
    assert "AI 분석 결과" not in shown_b
    assert not any(t.label.startswith("금융용어 근거") for t in at.tabs)

    # 다른 조작으로 다시 실행돼도 A의 결과가 되살아나지 않는다.
    at.run()
    assert ANSWER_A not in page_text(at)
    assert len(spy.calls) == 2


def test_success_after_failure_shows_only_new_answer(search_app):  # noqa: F811
    at, spy, _ = search_app
    spy.responses[QUESTION_A] = {"answer": ANSWER_A, "references": []}
    spy.responses[QUESTION_B] = RuntimeError("upstream timeout")

    submit(at, QUESTION_A)
    submit(at, QUESTION_B)
    submit(at, "세 번째 질문")

    shown = page_text(at)
    assert "세 번째 질문에 대한 답변" in shown
    assert ANSWER_A not in shown
    assert len(at.error) == 0
