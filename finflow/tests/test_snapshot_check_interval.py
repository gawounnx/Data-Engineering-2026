"""GCS 스냅샷 확인 간격 회귀 테스트.

배경 (2026-11-26 운영 재현):
Cloud Run 새 인스턴스에서는 time.monotonic()이 0에 가까운 값에서 시작한다.
`_last_checked_at = 0.0`으로 '미확인'을 표현하던 이전 코드는
`monotonic() - 0.0 < CHECK_INTERVAL_SECONDS`가 참이 되어, 시작 후 약 5분 동안
GCS를 한 번도 조회하지 않고 빈 상태(updated_at=None, error=None)를 반환했다.

여기서는 force=True 없이 실제 앱처럼 호출하고, 시계와 GCS를 모두 가짜로 바꿔
네트워크 없이 확인 간격 동작을 검증한다.
"""

from __future__ import annotations

import shutil
import tarfile
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from google.auth.exceptions import RefreshError

import src.retriever as retriever
from conftest import make_chroma

SNAPSHOT_UPDATED_AT = datetime(2026, 11, 26, 1, 23, 47, tzinfo=timezone.utc)


class FakeClock:
    """테스트에서 직접 움직이는 monotonic 시계."""

    def __init__(self, start: float) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_snapshot(tmp_path: Path, name: str, terms: int, news: int) -> Path:
    chroma_dir = make_chroma(tmp_path / name / "chroma", terms=terms, news=news)
    tar_path = tmp_path / f"{name}.tar.gz"

    with tarfile.open(tar_path, "w:gz") as archive:
        archive.add(chroma_dir, arcname="chroma")

    return tar_path


class FakeGcs:
    """storage.Client 대역. 확인(reload) 횟수와 현재 세대를 바꿀 수 있다."""

    def __init__(self, tar_path: Path, generation: str) -> None:
        self.blob = mock.MagicMock()
        self.blob.updated = SNAPSHOT_UPDATED_AT
        self.publish(tar_path, generation)
        self.client = mock.MagicMock()
        self.client.bucket.return_value.blob.return_value = self.blob

    def publish(self, tar_path: Path, generation: str) -> None:
        self.blob.generation = generation
        self.blob.download_to_filename.side_effect = (
            lambda destination: shutil.copy(tar_path, destination)
        )

    @property
    def checks(self) -> int:
        return self.blob.reload.call_count


@pytest.fixture
def gcs_mode(isolated, monkeypatch, tmp_path):
    """GCS 모드 + 가짜 시계 + 가짜 GCS. (모듈, 시계, GCS)를 돌려준다."""
    ns, _ = isolated
    monkeypatch.setenv("FINFLOW_BUCKET", "test-bucket")

    clock = FakeClock(start=1.0)
    # 전역 time.monotonic은 건드리지 않고 이 모듈이 보는 time만 바꾼다.
    monkeypatch.setattr(ns, "time", types.SimpleNamespace(monotonic=clock.monotonic))

    gcs = FakeGcs(make_snapshot(tmp_path, "gen1", terms=3, news=2), "1001")
    monkeypatch.setattr(ns.storage, "Client", lambda *a, **k: gcs.client)

    return ns, clock, gcs


@pytest.mark.parametrize("boot_monotonic", [0.0, 1.0, 60.0, 299.0])
def test_first_call_checks_gcs_immediately_even_with_small_monotonic(
    gcs_mode, boot_monotonic
):
    ns, clock, gcs = gcs_mode
    clock.now = boot_monotonic

    status = ns.sync_latest_news_snapshot()

    assert gcs.checks == 1
    assert status.updated_at == SNAPSHOT_UPDATED_AT
    assert status.generation == "1001"
    assert status.data_ready
    assert status.error is None


def test_calls_within_interval_do_not_recheck(gcs_mode):
    ns, clock, gcs = gcs_mode

    first = ns.sync_latest_news_snapshot()
    clock.advance(ns.CHECK_INTERVAL_SECONDS - 1)
    second = ns.sync_latest_news_snapshot()

    assert gcs.checks == 1
    assert second == first


def test_recheck_after_interval_picks_up_new_generation(gcs_mode, tmp_path):
    ns, clock, gcs = gcs_mode

    ns.sync_latest_news_snapshot()
    first_path = retriever.CHROMA_PATH

    gcs.publish(make_snapshot(tmp_path, "gen2", terms=3, news=5), "1002")
    clock.advance(ns.CHECK_INTERVAL_SECONDS)
    status = ns.sync_latest_news_snapshot()

    assert gcs.checks == 2
    assert status.generation == "1002"
    assert status.refreshed
    assert status.collection_counts == {"financial_terms": 3, "financial_news": 5}
    assert retriever.CHROMA_PATH != first_path


def test_failed_recheck_keeps_last_good_snapshot(gcs_mode, monkeypatch):
    ns, clock, gcs = gcs_mode

    good = ns.sync_latest_news_snapshot()
    good_path = retriever.CHROMA_PATH

    monkeypatch.setattr(
        ns.storage, "Client", mock.MagicMock(side_effect=RefreshError("expired"))
    )
    clock.advance(ns.CHECK_INTERVAL_SECONDS)
    failed = ns.sync_latest_news_snapshot()

    assert failed.error
    assert failed.data_ready
    assert failed.source == "gcs"
    assert failed.generation == good.generation
    assert failed.updated_at == good.updated_at
    assert failed.collection_counts == good.collection_counts
    assert retriever.CHROMA_PATH == good_path

    # 실패도 확인으로 기록되어, 간격 안에서는 다시 조회하지 않는다.
    clock.advance(1)
    assert ns.sync_latest_news_snapshot() == failed
