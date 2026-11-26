"""검색 폼(st.form) 제출 동작 회귀 테스트.

검색 함수(src.rag.ask_finflow)는 호출 기록용 대역으로 바꾸고,
혹시 대역을 우회해 실제 Gemini 경로에 닿으면 바로 실패하도록 막아 둔다.
이벤트 로그 전송도 네트워크 없이 기록만 한다.

AppTest에는 키보드 입력이 없어 Enter 키 자체를 누를 수는 없다. 대신
- 폼에 enter_to_submit=True가 설정되어 있고 입력창과 같은 폼에 제출 버튼이 있는지
  (Streamlit 프런트엔드는 Enter를 받으면 이 폼의 제출 버튼으로 제출한다)
- 값만 바뀌고 제출되지 않은 실행에서는 검색하지 않는지
를 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import src.llm as llm
import src.rag as rag
import utils.event_logger as event_logger
from conftest import PROJECT_ROOT, make_chroma

APP_PATH = str(PROJECT_ROOT / "app_ui.py")
SUGGESTION = "금리와 은행주의 관계"


@dataclass
class SearchSpy:
    """ask_finflow 대역. 질문별 응답 또는 예외를 정할 수 있다."""

    calls: list[str] = field(default_factory=list)
    responses: dict[str, Any] = field(default_factory=dict)

    def __call__(self, question: str) -> Any:
        self.calls.append(question)
        response = self.responses.get(
            question,
            {"answer": f"{question}에 대한 답변", "references": []},
        )

        if isinstance(response, Exception):
            raise response

        return response


def _no_real_llm(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("테스트에서 실제 Gemini 경로가 호출되었습니다.")


@pytest.fixture
def search_app(isolated, monkeypatch):
    """검색 데이터가 준비된 앱 + 검색 대역 + 이벤트 기록."""
    _, local_chroma = isolated
    make_chroma(local_chroma, terms=3, news=2)
    monkeypatch.chdir(PROJECT_ROOT)

    events: list[str] = []
    monkeypatch.setattr(
        event_logger,
        "send_event",
        lambda *a, **k: events.append(k.get("event_type")) or False,
    )
    monkeypatch.setattr(llm, "generate_answer", _no_real_llm)
    monkeypatch.setattr(llm, "load_gemini_client", _no_real_llm)

    spy = SearchSpy()
    monkeypatch.setattr(rag, "ask_finflow", spy)

    at = AppTest.from_file(APP_PATH, default_timeout=120).run()
    assert not at.exception
    return at, spy, events


def page_text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [t.label for t in at.tabs]
    return "\n".join(str(p) for p in parts)


def test_search_form_submits_on_enter_with_its_own_button(search_app):
    at, _, _ = search_app

    button = at.button(key="analyze_button")
    assert button.proto.is_form_submitter
    assert button.proto.form_id == "search_form"
    assert at.text_input(key="search_question").proto.form_id == "search_form"
    assert _form_enter_to_submit(at, "search_form") is True


def _form_enter_to_submit(at: AppTest, form_id: str) -> bool | None:
    stack = [at._tree]

    while stack:
        node = stack.pop()

        if getattr(node, "type", None) == "form" and node.proto.form.form_id == form_id:
            return node.proto.form.enter_to_submit

        children = getattr(node, "children", None)

        if isinstance(children, dict):
            stack.extend(children.values())

    return None


def test_typing_without_submit_does_not_search(search_app):
    at, spy, _ = search_app

    at.text_input(key="search_question").input("환율 상승과 물가").run()

    assert spy.calls == []


def test_click_right_after_typing_searches_exactly_once(search_app):
    at, spy, events = search_app

    at.text_input(key="search_question").input("  금리 인상 영향  ")
    at.button(key="analyze_button").click().run()

    assert not at.exception
    assert spy.calls == ["금리 인상 영향"]
    assert events.count("record_search") == 1
    assert "금리 인상 영향에 대한 답변" in page_text(at)

    # 다른 위젯 조작으로 다시 실행돼도 같은 질문을 다시 검색하지 않는다.
    at.run()
    assert spy.calls == ["금리 인상 영향"]


def test_enter_submission_searches_exactly_once(search_app):
    """Enter 제출 = 프런트엔드가 입력값과 함께 폼 제출 버튼을 트리거한 실행."""
    at, spy, _ = search_app

    at.text_input(key="search_question").input("환율 상승과 물가")
    at.button(key="analyze_button").click()
    at.run()

    assert spy.calls == ["환율 상승과 물가"]

    at.run()
    assert spy.calls == ["환율 상승과 물가"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_question_warns_without_calling_search(search_app, blank):
    at, spy, events = search_app

    at.text_input(key="search_question").input(blank)
    at.button(key="analyze_button").click().run()

    assert not at.exception
    assert spy.calls == []
    assert "record_search" not in events
    assert [w.value for w in at.warning] == ["질문을 입력해 주세요."]


def test_suggestion_fills_question_without_searching(search_app):
    at, spy, _ = search_app

    at.button(key=f"suggestion_{SUGGESTION}").click().run()

    assert not at.exception
    assert at.text_input(key="search_question").value == SUGGESTION
    assert spy.calls == []

    at.button(key="analyze_button").click().run()

    assert spy.calls == [SUGGESTION]
