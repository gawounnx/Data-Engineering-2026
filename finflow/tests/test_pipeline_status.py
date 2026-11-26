"""운영 일정(매일 08·12·18시 KST, 각 30분 허용) 기준 갱신 상태 판단 테스트.

모든 시각을 고정해서 넣는다. 네트워크·Gemini 호출 없음.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.news_snapshot import NewsSnapshotStatus
from src.pipeline_status import (
    KST,
    PipelineState,
    evaluate_pipeline,
    format_kst,
    latest_required_run,
    next_scheduled_run,
)


def kst(month: int, day: int, hour: int, minute: int = 0, second: int = 0, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def gcs_status(updated_at: datetime | None, error: str | None = None) -> NewsSnapshotStatus:
    return NewsSnapshotStatus(
        updated_at=updated_at,
        generation="1795662086698268",
        source="gcs",
        collections=(("financial_news", 5510), ("financial_terms", 1121)),
        missing_collections=(),
        error=error,
    )


# ---------- 야간 대기 ----------

@pytest.mark.parametrize(
    "now",
    [kst(11, 26, 18, 31), kst(11, 26, 23, 59), kst(11, 27, 2, 0), kst(11, 27, 7, 59)],
    ids=["18:31", "23:59", "02:00", "07:59"],
)
def test_overnight_wait_after_18h_run_is_on_schedule(now):
    health = evaluate_pipeline(gcs_status(kst(11, 26, 18, 1, 26)), now=now)

    assert health.state is PipelineState.ON_SCHEDULE
    assert health.label == "정기 갱신"
    assert health.next_scheduled == kst(11, 27, 8, 0)
    assert health.missed_run is None


# ---------- 예정 시각 전후 ----------

@pytest.mark.parametrize(
    ("now", "updated", "expected", "next_run"),
    [
        (kst(11, 26, 11, 59), kst(11, 26, 8, 1), PipelineState.ON_SCHEDULE, kst(11, 26, 12)),
        (kst(11, 26, 12, 0), kst(11, 26, 8, 1), PipelineState.ON_SCHEDULE, kst(11, 26, 18)),
        (kst(11, 26, 12, 29, 59), kst(11, 26, 8, 1), PipelineState.ON_SCHEDULE, kst(11, 26, 18)),
        (kst(11, 26, 12, 30, 0), kst(11, 26, 8, 1), PipelineState.DELAYED, kst(11, 26, 18)),
        (kst(11, 26, 12, 30, 0), kst(11, 26, 12, 1, 26), PipelineState.ON_SCHEDULE, kst(11, 26, 18)),
        # 정각 직전 수동 실행은 그 회차로 치지 않는다
        (kst(11, 26, 12, 31), kst(11, 26, 11, 55), PipelineState.DELAYED, kst(11, 26, 18)),
    ],
    ids=["before-12", "at-12", "grace-end-1s", "grace-end", "12h-run-landed", "manual-before-slot"],
)
def test_around_scheduled_time(now, updated, expected, next_run):
    health = evaluate_pipeline(gcs_status(updated), now=now)

    assert health.state is expected
    assert health.next_scheduled == next_run

    if expected is PipelineState.DELAYED:
        assert health.missed_run == kst(11, 26, 12)


# ---------- 갱신 누락 ----------

def test_missed_noon_run_stays_delayed_until_next_run_lands():
    last = kst(11, 26, 8, 1)

    at_17 = evaluate_pipeline(gcs_status(last), now=kst(11, 26, 17, 0))
    assert at_17.state is PipelineState.DELAYED
    assert at_17.missed_run == kst(11, 26, 12)
    assert at_17.label == "갱신 지연"

    # 18시 회차도 반영되지 않으면 미반영 회차가 18시로 바뀐다
    at_1840 = evaluate_pipeline(gcs_status(last), now=kst(11, 26, 18, 40))
    assert at_1840.state is PipelineState.DELAYED
    assert at_1840.missed_run == kst(11, 26, 18)

    # 18시 회차가 반영되면 정상으로 돌아온다
    recovered = evaluate_pipeline(gcs_status(kst(11, 26, 18, 5)), now=kst(11, 26, 18, 40))
    assert recovered.state is PipelineState.ON_SCHEDULE


# ---------- 날짜 변경 ----------

def test_day_change_requires_next_morning_run():
    last = kst(11, 26, 18, 1)

    assert evaluate_pipeline(gcs_status(last), now=kst(11, 27, 0, 10)).state is PipelineState.ON_SCHEDULE
    assert evaluate_pipeline(gcs_status(last), now=kst(11, 27, 8, 10)).state is PipelineState.ON_SCHEDULE

    missed = evaluate_pipeline(gcs_status(last), now=kst(11, 27, 8, 31))
    assert missed.state is PipelineState.DELAYED
    assert missed.missed_run == kst(11, 27, 8)


def test_utc_input_is_judged_in_kst():
    # 2026-11-26T23:30Z == 2026-11-27 08:30 KST (허용 시간 끝)
    now_utc = datetime(2026, 11, 26, 23, 30, tzinfo=timezone.utc)

    assert latest_required_run(now_utc) == kst(11, 27, 8)
    assert evaluate_pipeline(gcs_status(kst(11, 26, 18, 1)), now=now_utc).state is PipelineState.DELAYED


def test_year_end_rollover():
    now = kst(12, 31, 23, 0)

    assert next_scheduled_run(now) == kst(1, 1, 8, year=2027)
    assert latest_required_run(kst(1, 1, 7, 0, year=2027)) == kst(12, 31, 18)


def test_snapshot_from_two_days_ago_is_delayed_with_latest_missed_run():
    health = evaluate_pipeline(gcs_status(kst(11, 24, 18, 1)), now=kst(11, 26, 13, 0))

    assert health.state is PipelineState.DELAYED
    assert health.missed_run == kst(11, 26, 12)


# ---------- 데이터 없음 / 확인 오류 / 로컬 ----------

def test_no_data_is_separate_from_check_error():
    no_data = NewsSnapshotStatus(updated_at=None, generation=None, error="GCS 인증 실패")
    health = evaluate_pipeline(no_data, now=kst(11, 26, 13))

    assert health.state is PipelineState.NO_DATA
    assert health.metric == "No data"
    assert health.detail == "GCS 인증 실패"


def test_check_error_keeps_last_good_snapshot_time():
    status = gcs_status(kst(11, 26, 12, 1), error="스냅샷 동기화에 실패했습니다 (RefreshError)")
    health = evaluate_pipeline(status, now=kst(11, 26, 13))

    assert health.state is PipelineState.CHECK_ERROR
    assert health.label == "스냅샷 확인 오류"
    assert health.metric == "Check error"
    assert health.last_updated == kst(11, 26, 12, 1)
    assert "RefreshError" in health.detail


def test_gcs_snapshot_without_time_is_check_error():
    health = evaluate_pipeline(gcs_status(None), now=kst(11, 26, 13))

    assert health.state is PipelineState.CHECK_ERROR


def test_local_data_is_not_judged_by_schedule():
    status = NewsSnapshotStatus(
        updated_at=kst(10, 1, 9),
        generation="local",
        source="local",
        collections=(("financial_news", 2), ("financial_terms", 3)),
        missing_collections=(),
    )

    assert evaluate_pipeline(status, now=kst(11, 26, 13)).state is PipelineState.LOCAL


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        evaluate_pipeline(gcs_status(kst(11, 26, 12, 1)), now=datetime(2026, 11, 26, 13))


def test_format_kst():
    assert format_kst(datetime(2026, 11, 26, 3, 1, 26, tzinfo=timezone.utc)) == "2026.11.26 12:01"
    assert format_kst(None) == "확인할 수 없음"
