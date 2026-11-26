"""Streamlit AppTest로 app_ui.py의 데이터 준비 상태 화면을 검증한다.

질문 제출·Gemini 호출은 하지 않는다. 이벤트 로그 전송은 막는다.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

import utils.event_logger as event_logger
from conftest import PROJECT_ROOT, make_chroma

APP_PATH = str(PROJECT_ROOT / "app_ui.py")


@pytest.fixture
def app(isolated, monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)  # styles/main.css 상대 경로
    monkeypatch.setattr(event_logger, "send_event", lambda *a, **k: False)
    return isolated


def test_app_without_data_shows_setup_notice(app):
    _, local_chroma = app

    at = AppTest.from_file(APP_PATH, default_timeout=120).run()

    assert not at.exception
    errors = [element.value for element in at.error]
    assert "검색 데이터 준비 필요" in errors
    assert any("financial_terms" in message for message in errors)
    assert len(at.text_input) == 0  # 검색 입력 대신 안내
    assert [m.value for m in at.metric if m.label == "Status"] == ["No data"]
    assert not (local_chroma / "chroma.sqlite3").exists()


def test_app_with_data_shows_search(app):
    _, local_chroma = app
    make_chroma(local_chroma, terms=3, news=2)

    at = AppTest.from_file(APP_PATH, default_timeout=120).run()

    assert not at.exception
    assert len(at.error) == 0
    assert len(at.text_input) > 0
    # 로컬 데이터는 정기 갱신(08·12·18시) 판단 대상이 아니다.
    assert [m.value for m in at.metric if m.label == "Status"] == ["Local"]
