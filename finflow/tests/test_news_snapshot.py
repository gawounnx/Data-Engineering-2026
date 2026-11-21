"""데이터 준비 상태 판단 시나리오 테스트 (GCS는 mock, 네트워크 호출 없음)."""

from __future__ import annotations

import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from google.api_core.exceptions import NotFound
from google.auth.exceptions import DefaultCredentialsError, RefreshError

import src.news_retriever as news_retriever
import src.retriever as retriever
from conftest import make_chroma


def make_snapshot_tar(chroma_dir: Path, out: Path) -> Path:
    with tarfile.open(out, "w:gz") as archive:
        archive.add(chroma_dir, arcname="chroma")
    return out


def fake_storage_client(tar_path: Path, generation: str) -> mock.MagicMock:
    blob = mock.MagicMock()
    blob.generation = generation
    blob.updated = datetime.now(timezone.utc)
    blob.download_to_filename.side_effect = (
        lambda destination: shutil.copy(tar_path, destination)
    )
    client = mock.MagicMock()
    client.bucket.return_value.blob.return_value = blob
    return client


# ---------- 버킷 미설정 (로컬 모드) ----------


def test_empty_clone_is_not_ready_and_creates_no_db(isolated):
    ns, local_chroma = isolated

    status = ns.sync_latest_news_snapshot()

    assert not status.data_ready
    assert set(status.missing_collections) == {"financial_terms", "financial_news"}
    assert "download_chroma_snapshot.py" in status.error
    assert not (local_chroma / "chroma.sqlite3").exists()


def test_retrievers_fail_without_creating_empty_db(isolated):
    _, local_chroma = isolated

    with pytest.raises(FileNotFoundError):
        retriever.load_collection()

    with pytest.raises(FileNotFoundError):
        news_retriever.open_chroma_client()

    assert not (local_chroma / "chroma.sqlite3").exists()


def test_empty_sqlite_without_collections_is_not_ready(isolated):
    ns, local_chroma = isolated
    make_chroma(local_chroma, create_terms=False, create_news=False)

    status = ns.sync_latest_news_snapshot()

    assert (local_chroma / "chroma.sqlite3").exists()
    assert not status.data_ready
    assert set(status.missing_collections) == {"financial_terms", "financial_news"}


def test_empty_terms_collection_is_not_ready(isolated):
    ns, local_chroma = isolated
    make_chroma(local_chroma, terms=0, news=1)

    status = ns.sync_latest_news_snapshot()

    assert not status.data_ready
    assert status.missing_collections == ("financial_terms",)
    assert status.collection_counts == {"financial_terms": 0, "financial_news": 1}


def test_local_data_with_both_collections_is_ready(isolated):
    ns, local_chroma = isolated
    make_chroma(local_chroma, terms=3, news=2)

    status = ns.sync_latest_news_snapshot()

    assert status.data_ready
    assert status.error is None
    assert status.updated_at is not None
    assert status.collection_counts == {"financial_terms": 3, "financial_news": 2}
    assert retriever.load_collection().count() == 3


# ---------- 버킷 설정 (GCS 모드) ----------


@pytest.mark.parametrize(
    "auth_error",
    [DefaultCredentialsError("no adc"), RefreshError("expired")],
    ids=["no-adc", "refresh-failed"],
)
def test_auth_failure_does_not_crash_and_is_not_ready(isolated, monkeypatch, auth_error):
    ns, _ = isolated
    monkeypatch.setenv("FINFLOW_BUCKET", "test-bucket")

    with mock.patch.object(ns.storage, "Client", side_effect=auth_error):
        status = ns.sync_latest_news_snapshot(force=True)

    assert not status.data_ready
    assert status.source == "local-fallback"
    assert "인증" in status.error


def test_missing_snapshot_is_not_ready(isolated, monkeypatch):
    ns, _ = isolated
    monkeypatch.setenv("FINFLOW_BUCKET", "test-bucket")
    client = mock.MagicMock()
    client.bucket.return_value.blob.return_value.reload.side_effect = NotFound("nope")

    with mock.patch.object(ns.storage, "Client", return_value=client):
        status = ns.sync_latest_news_snapshot(force=True)

    assert not status.data_ready
    assert "찾을 수 없습니다" in status.error


def test_snapshot_without_terms_is_rejected(isolated, monkeypatch, tmp_path):
    ns, local_chroma = isolated
    monkeypatch.setenv("FINFLOW_BUCKET", "test-bucket")
    partial = make_chroma(tmp_path / "partial" / "chroma", terms=0, news=1, create_terms=False)
    tar_path = make_snapshot_tar(partial, tmp_path / "partial.tar.gz")

    with mock.patch.object(
        ns.storage, "Client", return_value=fake_storage_client(tar_path, "111")
    ):
        status = ns.sync_latest_news_snapshot(force=True)

    assert not status.data_ready
    assert "financial_terms" in status.error
    assert retriever.CHROMA_PATH == local_chroma  # 적용하지 않음


def test_valid_snapshot_restores_both_collections_and_switches_path(
    isolated, monkeypatch, tmp_path
):
    ns, local_chroma = isolated
    monkeypatch.setenv("FINFLOW_BUCKET", "test-bucket")
    full = make_chroma(tmp_path / "full" / "chroma", terms=3, news=2)
    tar_path = make_snapshot_tar(full, tmp_path / "full.tar.gz")

    with mock.patch.object(
        ns.storage, "Client", return_value=fake_storage_client(tar_path, "222")
    ):
        status = ns.sync_latest_news_snapshot(force=True)

    assert status.data_ready
    assert status.source == "gcs"
    assert status.collection_counts == {"financial_terms": 3, "financial_news": 2}
    assert retriever.CHROMA_PATH != local_chroma
    assert news_retriever.CHROMA_PATH == retriever.CHROMA_PATH
    assert retriever.load_collection().count() == 3

    # 이후 확인이 실패해도 검증된 기존 스냅샷으로 계속 서비스한다.
    with mock.patch.object(ns.storage, "Client", side_effect=RefreshError("expired")):
        later = ns.sync_latest_news_snapshot(force=True)

    assert later.data_ready
    assert later.generation == "222"
    assert later.error
