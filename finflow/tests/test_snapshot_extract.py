"""스냅샷 압축 해제(_safe_extract) 보안 테스트.

악성 tar 항목은 TarInfo로 직접 만들기 때문에 root 권한이나 실제 장치 파일이 필요 없다.
"""

from __future__ import annotations

import io
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import src.news_snapshot as news_snapshot
import src.retriever as retriever
from conftest import make_chroma


def _file(name: str, data: bytes = b"data") -> tuple[tarfile.TarInfo, io.BytesIO]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, io.BytesIO(data)


def _dir(name: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info, None


def _special(name: str, kind: bytes, linkname: str = "") -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info, None


def build_tar(path: Path, entries) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for info, payload in entries:
            archive.addfile(info, payload)
    return path


def extract(tar_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as archive:
        news_snapshot._safe_extract(archive, destination)


def valid_prefix():
    return [_dir("chroma"), _file("chroma/chroma.sqlite3")]


def test_regular_files_and_directories_are_extracted(tmp_path):
    tar_path = build_tar(
        tmp_path / "ok.tar.gz",
        [*valid_prefix(), _dir("chroma/uuid"), _file("chroma/uuid/data_level0.bin", b"x" * 10)],
    )
    dest = tmp_path / "dest"

    extract(tar_path, dest)

    assert (dest / "chroma" / "chroma.sqlite3").read_bytes() == b"data"
    assert (dest / "chroma" / "uuid" / "data_level0.bin").stat().st_size == 10


def test_dot_slash_prefix_is_allowed(tmp_path):
    tar_path = build_tar(tmp_path / "dot.tar.gz", [_dir("./chroma"), _file("./chroma/chroma.sqlite3")])
    dest = tmp_path / "dest"

    extract(tar_path, dest)

    assert (dest / "chroma" / "chroma.sqlite3").exists()


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil.txt",
        "chroma/../../evil.txt",
        "/tmp/finflow-evil.txt",
        "..\\evil.txt",
        "C:\\evil.txt",
    ],
    ids=["parent", "nested-parent", "absolute", "windows-parent", "windows-drive"],
)
def test_path_traversal_is_rejected(tmp_path, bad_name):
    tar_path = build_tar(tmp_path / "bad.tar.gz", [*valid_prefix(), _file(bad_name)])
    dest = tmp_path / "dest"

    with pytest.raises(news_snapshot.UnsafeSnapshotError, match="경로"):
        extract(tar_path, dest)

    assert not (tmp_path / "evil.txt").exists()
    assert list(dest.iterdir()) == []  # 하나라도 위반하면 아무것도 풀지 않음


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        (_special("chroma/link", tarfile.SYMTYPE, "/etc/passwd"), "심볼릭 링크"),
        (_special("chroma/inner-link", tarfile.SYMTYPE, "chroma.sqlite3"), "심볼릭 링크"),
        (_special("chroma/hard", tarfile.LNKTYPE, "chroma/chroma.sqlite3"), "하드 링크"),
        (_special("chroma/tty", tarfile.CHRTYPE), "문자 장치 파일"),
        (_special("chroma/disk", tarfile.BLKTYPE), "블록 장치 파일"),
        (_special("chroma/pipe", tarfile.FIFOTYPE), "FIFO"),
    ],
    ids=["symlink-outside", "symlink-inside", "hardlink", "char-device", "block-device", "fifo"],
)
def test_links_and_special_files_are_rejected(tmp_path, entry, kind):
    tar_path = build_tar(tmp_path / "bad.tar.gz", [*valid_prefix(), entry])
    dest = tmp_path / "dest"

    with pytest.raises(news_snapshot.UnsafeSnapshotError, match=kind):
        extract(tar_path, dest)

    assert list(dest.iterdir()) == []


def test_symlink_then_write_through_it_is_rejected(tmp_path):
    """링크를 먼저 만들고 그 경로로 파일을 쓰는 고전적 우회도 막는다."""
    outside = tmp_path / "outside"
    outside.mkdir()
    tar_path = build_tar(
        tmp_path / "bad.tar.gz",
        [
            *valid_prefix(),
            _special("chroma/escape", tarfile.SYMTYPE, str(outside)),
            _file("chroma/escape/pwned.txt"),
        ],
    )
    dest = tmp_path / "dest"

    with pytest.raises(news_snapshot.UnsafeSnapshotError):
        extract(tar_path, dest)

    assert not (outside / "pwned.txt").exists()


def test_rejected_snapshot_is_not_applied_and_is_cleaned_up(isolated, monkeypatch, tmp_path):
    ns, local_chroma = isolated
    monkeypatch.setenv("FINFLOW_BUCKET", "test-bucket")

    good = make_chroma(tmp_path / "src" / "chroma", terms=2, news=2)
    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        archive.add(good, arcname="chroma")
        info, _ = _special("chroma/link", tarfile.SYMTYPE, "/etc/passwd")
        archive.addfile(info)

    blob = mock.MagicMock()
    blob.generation = "333"
    blob.updated = datetime.now(timezone.utc)
    blob.download_to_filename.side_effect = lambda d: shutil.copy(tar_path, d)
    client = mock.MagicMock()
    client.bucket.return_value.blob.return_value = blob

    with mock.patch.object(ns.storage, "Client", return_value=client):
        status = ns.sync_latest_news_snapshot(force=True)

    assert not status.data_ready
    assert "심볼릭 링크" in status.error
    assert retriever.CHROMA_PATH == local_chroma
    assert not (ns.SNAPSHOT_ROOT / "333").exists()


@pytest.mark.parametrize("generation", ["..", "../x", "abc"])
def test_non_numeric_generation_is_rejected(isolated, generation):
    ns, _ = isolated
    blob = mock.MagicMock()

    with pytest.raises(news_snapshot.UnsafeSnapshotError, match="generation"):
        ns._download_snapshot(blob=blob, generation=generation)

    blob.download_to_filename.assert_not_called()
