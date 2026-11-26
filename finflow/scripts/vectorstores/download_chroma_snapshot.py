"""GCS에 저장된 ChromaDB 스냅샷을 내려받아 복원한다.

기존 data/chroma는 새 스냅샷이 다운로드·압축 해제·검증을 모두 통과한 뒤에만
교체한다. 어느 단계에서 실패하든 기존 데이터는 그대로 남는다.

뉴스 Job 이미지는 scripts/만 포함하므로 src/를 import하지 않고
필요한 검증을 이 파일 안에서 수행한다.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from google.api_core.exceptions import NotFound
from google.cloud import storage


CHROMA_PATH = Path("data/chroma")
SNAPSHOT_TOP_DIR = "chroma"
CHROMA_DB_FILENAME = "chroma.sqlite3"
REQUIRED_COLLECTIONS: tuple[str, ...] = (
    "financial_terms",
    "financial_news",
)

DEFAULT_BUCKET_NAME = "dbai-2026-project"
DEFAULT_OBJECT_NAME = "finflow/vectorstore/chroma/latest.tar.gz"


class UnsafeSnapshotError(ValueError):
    """스냅샷에 허용하지 않는 항목이나 경로가 있을 때 발생한다."""


class InvalidSnapshotError(ValueError):
    """스냅샷 내용이 ChromaDB로서 올바르지 않을 때 발생한다."""


def env_or_default(name: str, default: str) -> str:
    """환경 변수가 없거나 빈 값이면 기본값을 사용한다."""
    return os.getenv(name, "").strip() or default


def _validate_member(
    member: tarfile.TarInfo,
    destination: Path,
) -> None:
    """tar 항목이 chroma/ 아래의 일반 파일·디렉터리인지 확인한다."""
    name = member.name

    if not (member.isfile() or member.isdir()):
        raise UnsafeSnapshotError(
            "스냅샷에 일반 파일·디렉터리가 아닌 항목이 있어 거부했습니다: "
            f"{name} (type={member.type!r})"
        )

    posix_path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)

    if (
        not name
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        raise UnsafeSnapshotError(
            f"스냅샷에 안전하지 않은 경로가 있어 거부했습니다: {name}"
        )

    parts = [part for part in posix_path.parts if part != "."]

    if not parts or parts[0] != SNAPSHOT_TOP_DIR:
        raise UnsafeSnapshotError(
            f"스냅샷 항목은 {SNAPSHOT_TOP_DIR}/ 아래에 있어야 합니다: {name}"
        )

    member_path = (destination / name).resolve()

    if destination not in member_path.parents:
        raise UnsafeSnapshotError(
            f"스냅샷에 안전하지 않은 경로가 있어 거부했습니다: {name}"
        )


def safe_extract(
    archive: tarfile.TarFile,
    destination: Path,
) -> None:
    """모든 항목을 먼저 검사한 뒤 일반 파일·디렉터리만 압축 해제한다."""
    destination = destination.resolve()
    members = archive.getmembers()

    for member in members:
        _validate_member(member, destination)

    if hasattr(tarfile, "data_filter"):
        archive.extractall(
            destination,
            members=members,
            filter="data",
        )
    else:  # pragma: no cover - data 필터가 없는 오래된 Python
        archive.extractall(destination, members=members)


def inspect_collections(chroma_path: Path) -> dict[str, int]:
    """ChromaDB 컬렉션별 문서 수를 반환한다."""
    if not (chroma_path / CHROMA_DB_FILENAME).exists():
        raise InvalidSnapshotError(
            f"스냅샷에 {CHROMA_DB_FILENAME} 파일이 없습니다."
        )

    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))

    return {
        collection.name: client.get_collection(collection.name).count()
        for collection in client.list_collections()
    }


def validate_snapshot(chroma_path: Path) -> dict[str, int]:
    """필수 컬렉션이 모두 있고 비어 있지 않은지 확인한다."""
    counts = inspect_collections(chroma_path)
    missing = [
        name
        for name in REQUIRED_COLLECTIONS
        if counts.get(name, 0) <= 0
    ]

    if missing:
        raise InvalidSnapshotError(
            "스냅샷에 필수 컬렉션이 없거나 비어 있습니다: "
            f"{', '.join(missing)}"
        )

    return counts


def replace_chroma_directory(
    new_chroma: Path,
    target: Path,
) -> None:
    """검증된 새 폴더로 target을 교체한다. 실패하면 기존 폴더를 되돌린다."""
    backup: Path | None = None

    if target.exists():
        backup = target.with_name(f".{target.name}.backup")

        if backup.exists():
            shutil.rmtree(backup)

        target.rename(backup)

    try:
        new_chroma.rename(target)
    except Exception:
        if backup is not None and not target.exists():
            backup.rename(target)
        raise

    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def download_snapshot(
    bucket_name: str,
    object_name: str,
) -> bool:
    """GCS 스냅샷을 내려받아 검증한 뒤 data/chroma를 교체한다.

    반환값: 스냅샷을 적용했으면 True, GCS에 스냅샷이 없으면 False.
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    CHROMA_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # 같은 파일 시스템에서 rename으로 교체하기 위해 data/ 아래에 작업 폴더를 만든다.
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=".chroma_download_",
            dir=CHROMA_PATH.parent,
        )
    )

    try:
        archive_path = staging_root / "snapshot.tar.gz"

        try:
            blob.download_to_filename(str(archive_path))
        except NotFound:
            print(
                "[정보] 기존 ChromaDB 스냅샷이 없습니다. "
                "새 컬렉션을 생성합니다."
            )
            return False

        extract_root = staging_root / "extract"
        extract_root.mkdir()

        with tarfile.open(archive_path, mode="r:gz") as archive:
            safe_extract(
                archive=archive,
                destination=extract_root,
            )

        new_chroma = extract_root / SNAPSHOT_TOP_DIR
        counts = validate_snapshot(new_chroma)

        replace_chroma_directory(
            new_chroma=new_chroma,
            target=CHROMA_PATH,
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    print("[ChromaDB 스냅샷 복원 완료]")
    print(f"- GCS: gs://{bucket_name}/{object_name}")
    print(f"- 로컬: {CHROMA_PATH.resolve()}")
    print(f"- 컬렉션: {counts}")

    return True


def main() -> None:
    """환경변수를 읽고 ChromaDB 스냅샷을 복원한다."""
    bucket_name = env_or_default(
        "FINFLOW_BUCKET",
        DEFAULT_BUCKET_NAME,
    )

    object_name = env_or_default(
        "CHROMA_SNAPSHOT_OBJECT",
        DEFAULT_OBJECT_NAME,
    )

    download_snapshot(
        bucket_name=bucket_name,
        object_name=object_name,
    )


if __name__ == "__main__":
    main()
