"""뉴스 스냅샷 갱신 상태를 운영 일정 기준으로 판단한다.

운영 일정: Cloud Scheduler `finflow-news-daily`가 매일 08·12·18시(KST)에 뉴스 Job을 실행한다.
각 예정 시각부터 GRACE_PERIOD(30분)가 지났는데도 그 시각 이후에 만들어진 스냅샷이
없으면 해당 회차가 누락된 것으로 보고 '지연'으로 표시한다.

화면 표시와 분리된 순수 함수만 두어, 시계를 고정한 테스트로 검증할 수 있게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from .news_snapshot import NewsSnapshotStatus

KST = ZoneInfo("Asia/Seoul")
# Cloud Scheduler finflow-news-daily: "0 8,12,18 * * *" (Asia/Seoul). 일정을 바꾸면 같이 바꾼다.
SCHEDULE_HOURS_KST: tuple[int, ...] = (8, 12, 18)
GRACE_PERIOD = timedelta(minutes=30)


class PipelineState(str, Enum):
    ON_SCHEDULE = "on_schedule"  # 정기 갱신 정상 (다음 회차 대기 중)
    DELAYED = "delayed"  # 예정 회차 + 30분이 지났는데 새 스냅샷 없음
    NO_DATA = "no_data"  # 검색에 쓸 데이터 자체가 없음
    CHECK_ERROR = "check_error"  # 데이터는 있으나 최신 스냅샷 확인 실패
    LOCAL = "local"  # 로컬 데이터 사용 중 (정기 갱신 대상 아님)


STATE_LABELS = {
    PipelineState.ON_SCHEDULE: "정기 갱신",
    PipelineState.DELAYED: "갱신 지연",
    PipelineState.NO_DATA: "데이터 없음",
    PipelineState.CHECK_ERROR: "스냅샷 확인 오류",
    PipelineState.LOCAL: "로컬 데이터",
}

STATE_METRICS = {
    PipelineState.ON_SCHEDULE: "Healthy",
    PipelineState.DELAYED: "Delayed",
    PipelineState.NO_DATA: "No data",
    PipelineState.CHECK_ERROR: "Check error",
    PipelineState.LOCAL: "Local",
}


@dataclass(frozen=True)
class PipelineHealth:
    state: PipelineState
    last_updated: datetime | None
    next_scheduled: datetime
    missed_run: datetime | None = None  # 지연일 때 반영되지 않은 예정 회차
    detail: str | None = None

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]

    @property
    def metric(self) -> str:
        return STATE_METRICS[self.state]


def _ensure_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("시각에는 시간대 정보가 있어야 합니다.")
    return moment


def _runs_on(day) -> list[datetime]:
    """KST 날짜 하루의 예정 실행 시각들(오름차순)."""
    return [
        datetime.combine(day, time(hour=hour), tzinfo=KST)
        for hour in sorted(SCHEDULE_HOURS_KST)
    ]


def latest_required_run(now: datetime) -> datetime:
    """허용 시간(30분)까지 지난 가장 최근 예정 회차. 스냅샷은 이 시각 이후 것이어야 한다."""
    now_kst = _ensure_aware(now).astimezone(KST)

    for day_offset in (0, 1, 2):
        day = (now_kst - timedelta(days=day_offset)).date()
        for run in reversed(_runs_on(day)):
            if run + GRACE_PERIOD <= now_kst:
                return run

    raise RuntimeError("예정 회차를 찾을 수 없습니다.")  # 일정이 비어 있을 때만 발생


def next_scheduled_run(now: datetime) -> datetime:
    """지금 이후 가장 가까운 예정 실행 시각."""
    now_kst = _ensure_aware(now).astimezone(KST)

    for day_offset in (0, 1):
        day = (now_kst + timedelta(days=day_offset)).date()
        for run in _runs_on(day):
            if run > now_kst:
                return run

    raise RuntimeError("다음 예정 회차를 찾을 수 없습니다.")


def evaluate_pipeline(
    status: NewsSnapshotStatus,
    now: datetime | None = None,
) -> PipelineHealth:
    """스냅샷 상태를 운영 일정 기준으로 판단한다."""
    now = _ensure_aware(now) if now is not None else datetime.now(timezone.utc)
    upcoming = next_scheduled_run(now)

    if not status.data_ready:
        return PipelineHealth(
            state=PipelineState.NO_DATA,
            last_updated=status.updated_at,
            next_scheduled=upcoming,
            detail=status.error,
        )

    if status.error:
        # 데이터는 서비스 중이지만 최신 스냅샷을 확인하지 못했다.
        return PipelineHealth(
            state=PipelineState.CHECK_ERROR,
            last_updated=status.updated_at,
            next_scheduled=upcoming,
            detail=status.error,
        )

    if status.source == "local":
        return PipelineHealth(
            state=PipelineState.LOCAL,
            last_updated=status.updated_at,
            next_scheduled=upcoming,
        )

    if status.updated_at is None:
        return PipelineHealth(
            state=PipelineState.CHECK_ERROR,
            last_updated=None,
            next_scheduled=upcoming,
            detail="스냅샷 갱신 시각을 확인할 수 없습니다.",
        )

    required = latest_required_run(now)

    if _ensure_aware(status.updated_at) >= required:
        return PipelineHealth(
            state=PipelineState.ON_SCHEDULE,
            last_updated=status.updated_at,
            next_scheduled=upcoming,
        )

    return PipelineHealth(
        state=PipelineState.DELAYED,
        last_updated=status.updated_at,
        next_scheduled=upcoming,
        missed_run=required,
    )


def format_kst(moment: datetime | None, with_date: bool = True) -> str:
    """KST 표시. 없으면 '확인할 수 없음'."""
    if moment is None:
        return "확인할 수 없음"
    pattern = "%Y.%m.%d %H:%M" if with_date else "%H:%M"
    return _ensure_aware(moment).astimezone(KST).strftime(pattern)
