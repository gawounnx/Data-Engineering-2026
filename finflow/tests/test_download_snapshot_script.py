"""scripts/vectorstores/download_chroma_snapshot.py 회귀 테스트.

- 실패한 갱신이 기존 data/chroma를 손상시키지 않는지
- 안전하지 않은 tar 항목을 거부하는지
- 검증된 스냅샷만 교체하는지
GCS는 mock으로 대체한다.
"""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path
from unittest import mock

import pytest
from google.api_core.exceptions import NotFound

from conftest import PROJECT_ROOT, make_chroma

SCRIPT = PROJECT_ROOT / "scripts" / "vectorstores" / "download_chroma_snapshot.py"


@pytest.fixture
def dl(tmp_path, monkeypatch):
    """스크립트 모듈을 불러오고, 작업 디렉터리를 임시 프로젝트로 바꾼다."""
    spec = importlib.util.spec_from_file_location("download_chroma_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    for name in ("FINFLOW_BUCKET", "CHROMA_SNAPSHOT_OBJECT"):
        monkeypatch.delenv(name, raising=False)
    return module


@pytest.fixture(autouse=True)
def clear_chroma_cache():
    """chromadb는 경로별 클라이언트를 프로세스 안에 캐시하므로 테스트마다 비운다."""
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    yield
    SharedSystemClient.clear_system_cache()


def existing_counts(dl) -> dict[str, int]:
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()  # 교체 후 새 파일을 읽도록
    return dl.inspect_collections(Path("data/chroma").resolve())


def leftovers() -> list[str]:
    return sorted(p.name for p in Path("data").iterdir() if p.name.startswith("."))


def client_returning(payload: bytes | Exception) -> mock.MagicMock:
    blob = mock.MagicMock()
    if isinstance(payload, Exception):
        blob.download_to_filename.side_effect = payload
    else:
        blob.download_to_filename.side_effect = lambda dst: Path(dst).write_bytes(payload)
    client = mock.MagicMock()
    client.bucket.return_value.blob.return_value = blob
    return client


def tar_of_chroma(chroma_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        archive.add(chroma_dir, arcname="chroma")
    return buf.getvalue()


def tar_with(entries) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for info, payload in entries:
            archive.addfile(info, payload)
    return buf.getvalue()


def _dir(name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info, None


def _file(name, data=b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, io.BytesIO(data)


def _symlink(name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info, None


@pytest.fixture
def existing(dl):
    """기존 정상 데이터: 용어 3, 뉴스 2."""
    make_chroma(Path("data/chroma").resolve(), terms=3, news=2)
    return {"financial_terms": 3, "financial_news": 2}


# ---------- 실패해도 기존 데이터 보존 ----------


def test_corrupt_archive_keeps_existing_data(dl, existing):
    with mock.patch.object(dl.storage, "Client", return_value=client_returning(b"not a tar")):
        with pytest.raises(tarfile.TarError):
            dl.download_snapshot("bucket", "obj")

    assert existing_counts(dl) == existing
    assert leftovers() == []


def test_download_failure_keeps_existing_data(dl, existing):
    with mock.patch.object(dl.storage, "Client", return_value=client_returning(OSError("network"))):
        with pytest.raises(OSError):
            dl.download_snapshot("bucket", "obj")

    assert existing_counts(dl) == existing
    assert leftovers() == []


def test_missing_snapshot_keeps_existing_data(dl, existing):
    with mock.patch.object(dl.storage, "Client", return_value=client_returning(NotFound("none"))):
        assert dl.download_snapshot("bucket", "obj") is False

    assert existing_counts(dl) == existing
    assert leftovers() == []


def test_snapshot_without_terms_is_rejected_and_existing_kept(dl, existing, tmp_path):
    partial = make_chroma(tmp_path / "partial" / "chroma", news=5, create_terms=False)

    with mock.patch.object(dl.storage, "Client", return_value=client_returning(tar_of_chroma(partial))):
        with pytest.raises(dl.InvalidSnapshotError, match="financial_terms"):
            dl.download_snapshot("bucket", "obj")

    assert existing_counts(dl) == existing
    assert leftovers() == []


# ---------- 안전하지 않은 항목 거부 ----------


def test_symlink_write_through_is_rejected(dl, existing, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = tar_with([
        _dir("chroma"),
        _symlink("chroma/escape", str(outside)),
        _file("chroma/escape/pwned.txt"),
    ])

    with mock.patch.object(dl.storage, "Client", return_value=client_returning(payload)):
        with pytest.raises(dl.UnsafeSnapshotError):
            dl.download_snapshot("bucket", "obj")

    assert not (outside / "pwned.txt").exists()
    assert existing_counts(dl) == existing


@pytest.mark.parametrize(
    "name",
    ["../evil.txt", "/tmp/finflow-evil.txt", "raw/naver_financial_news.json", "chroma/../raw/x.json"],
    ids=["parent", "absolute", "outside-chroma", "nested-parent"],
)
def test_paths_outside_chroma_are_rejected(dl, existing, name):
    payload = tar_with([_dir("chroma"), _file("chroma/chroma.sqlite3"), _file(name)])

    with mock.patch.object(dl.storage, "Client", return_value=client_returning(payload)):
        with pytest.raises(dl.UnsafeSnapshotError):
            dl.download_snapshot("bucket", "obj")

    assert not Path("data/raw").exists()
    assert existing_counts(dl) == existing


# ---------- 정상 교체 ----------


def test_valid_snapshot_replaces_existing(dl, existing, tmp_path):
    new = make_chroma(tmp_path / "new" / "chroma", terms=7, news=4)

    with mock.patch.object(dl.storage, "Client", return_value=client_returning(tar_of_chroma(new))):
        assert dl.download_snapshot("bucket", "obj") is True

    assert existing_counts(dl) == {"financial_terms": 7, "financial_news": 4}
    assert leftovers() == []


def test_valid_snapshot_creates_data_when_absent(dl, tmp_path):
    new = make_chroma(tmp_path / "new" / "chroma", terms=1, news=1)

    with mock.patch.object(dl.storage, "Client", return_value=client_returning(tar_of_chroma(new))):
        assert dl.download_snapshot("bucket", "obj") is True

    assert existing_counts(dl) == {"financial_terms": 1, "financial_news": 1}


def test_failed_swap_restores_existing(dl, existing, tmp_path):
    new = make_chroma(tmp_path / "new" / "chroma", terms=7, news=4)
    real_rename = Path.rename
    calls = {"n": 0}

    def flaky_rename(self, target):
        calls["n"] += 1
        if calls["n"] == 2:  # 1: 기존→백업, 2: 새 폴더→data/chroma
            raise OSError("rename failed")
        return real_rename(self, target)

    with mock.patch.object(dl.storage, "Client", return_value=client_returning(tar_of_chroma(new))), \
            mock.patch.object(Path, "rename", flaky_rename):
        with pytest.raises(OSError):
            dl.download_snapshot("bucket", "obj")

    assert existing_counts(dl) == existing
    assert leftovers() == []


# ---------- 빈 환경 변수 ----------


@pytest.mark.parametrize("env", [{}, {"FINFLOW_BUCKET": "", "CHROMA_SNAPSHOT_OBJECT": ""}, {"FINFLOW_BUCKET": "  "}])
def test_empty_env_uses_defaults(dl, monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with mock.patch.object(dl, "download_snapshot") as download:
        dl.main()

    download.assert_called_once_with(
        bucket_name=dl.DEFAULT_BUCKET_NAME,
        object_name=dl.DEFAULT_OBJECT_NAME,
    )
