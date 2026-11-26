"""화면(AppTest)에서 갱신 상태 문구가 상태별로 구분되어 표시되는지 확인한다.

스냅샷 상태는 대역으로, 현재 시각은 고정 시계로 바꾼다. 네트워크·Gemini 호출 없음.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

import src.news_snapshot as news_snapshot
import src.pipeline_status as pipeline_status
import utils.event_logger as event_logger
from conftest import PROJECT_ROOT
from test_pipeline_status import gcs_status, kst

APP_PATH = str(PROJECT_ROOT / "app_ui.py")


def fixed_clock(moment: datetime) -> type[datetime]:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    return FixedDateTime


@pytest.fixture
def render(isolated, monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setattr(event_logger, "send_event", lambda *a, **k: False)

    def run(status, now):
        monkeypatch.setattr(news_snapshot, "sync_latest_news_snapshot", lambda *a, **k: status)
        monkeypatch.setattr(pipeline_status, "datetime", fixed_clock(now))
        at = AppTest.from_file(APP_PATH, default_timeout=120).run()
        assert not at.exception
        return at

    return run


def texts(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [e.value for e in at.success] + [e.value for e in at.warning]
    parts += [e.value for e in at.error] + [e.value for e in at.info]
    return "\n".join(map(str, parts))


def status_metric(at) -> list[str]:
    return [m.value for m in at.metric if m.label == "Status"]


def test_on_schedule_shows_regular_update_and_next_run(render):
    at = render(gcs_status(kst(11, 26, 18, 1, 26)), now=kst(11, 27, 2, 0))
    shown = texts(at)

    assert "**정기 갱신**" in shown
    assert "정기 갱신 정상 (매일 08·12·18시)" in shown
    assert "최근 갱신: 2026.11.26 18:01 · 다음 예정: 2026.11.27 08:00" in shown
    assert "지연" not in shown
    assert status_metric(at) == ["Healthy"]


def test_delayed_names_the_missed_run(render):
    at = render(gcs_status(kst(11, 26, 8, 1)), now=kst(11, 26, 12, 30))
    shown = texts(at)

    assert "뉴스 갱신 지연: 2026.11.26 12:00 회차 미반영" in shown
    assert "다음 예정: 2026.11.26 18:00" in shown
    assert status_metric(at) == ["Delayed"]


def test_check_error_is_shown_separately_and_search_stays_available(render):
    status = gcs_status(kst(11, 26, 12, 1), error="스냅샷 동기화에 실패했습니다 (RefreshError)")
    at = render(status, now=kst(11, 26, 13, 0))
    shown = texts(at)

    assert "최신 스냅샷 확인 오류 (기존 데이터로 서비스 중)" in shown
    assert "검색 데이터 준비 필요" not in shown
    assert "RefreshError" in shown
    assert status_metric(at) == ["Check error"]
    assert len(at.text_input) > 0  # 기존 데이터로 검색 가능


def test_no_data_is_shown_separately(render):
    status = news_snapshot.NewsSnapshotStatus(updated_at=None, generation=None)
    at = render(status, now=kst(11, 26, 13, 0))
    shown = texts(at)

    assert "검색 데이터 준비 필요" in shown
    assert "스냅샷 확인 오류" not in shown
    assert status_metric(at) == ["No data"]
    assert len(at.text_input) == 0
