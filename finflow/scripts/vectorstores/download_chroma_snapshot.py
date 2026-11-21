"""GCS에 저장된 ChromaDB 스냅샷을 내려받아 복원한다."""

from __future__ import annotations

import os
import shutil
import tarfile
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import storage


CHROMA_PATH = Path("data/chroma")
ARCHIVE_PATH = Path("/tmp/chroma_snapshot.tar.gz")

DEFAULT_BUCKET_NAME = "dbai-2026-project"
DEFAULT_OBJECT_NAME = "finflow/vectorstore/chroma/latest.tar.gz"


def safe_extract(
    archive: tarfile.TarFile,
    destination: Path,
) -> None:
    """경로 탈출 공격을 막으면서 압축 파일을 해제한다."""
    destination = destination.resolve()

    for member in archive.getmembers():
        member_path = (
            destination / member.name
        ).resolve()

        if (
            destination != member_path
            and destination not in member_path.parents
        ):
            raise ValueError(
                f"안전하지 않은 압축 경로입니다: {member.name}"
            )

    archive.extractall(destination)


def download_snapshot(
    bucket_name: str,
    object_name: str,
) -> bool:
    """GCS에서 스냅샷을 내려받는다."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    try:
        blob.download_to_filename(
            str(ARCHIVE_PATH)
        )
    except NotFound:
        print(
            "[정보] 기존 ChromaDB 스냅샷이 없습니다. "
            "새 컬렉션을 생성합니다."
        )
        return False

    if CHROMA_PATH.exists():
        shutil.rmtree(CHROMA_PATH)

    CHROMA_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tarfile.open(
        ARCHIVE_PATH,
        mode="r:gz",
    ) as archive:
        safe_extract(
            archive=archive,
            destination=CHROMA_PATH.parent,
        )

    print("[ChromaDB 스냅샷 복원 완료]")
    print(f"- GCS: gs://{bucket_name}/{object_name}")
    print(f"- 로컬: {CHROMA_PATH.resolve()}")

    return True


def main() -> None:
    """환경변수를 읽고 ChromaDB 스냅샷을 복원한다."""
    bucket_name = os.getenv(
        "FINFLOW_BUCKET",
        DEFAULT_BUCKET_NAME,
    ).strip()

    object_name = os.getenv(
        "CHROMA_SNAPSHOT_OBJECT",
        DEFAULT_OBJECT_NAME,
    ).strip()

    if not bucket_name:
        raise ValueError(
            "FINFLOW_BUCKET이 비어 있습니다."
        )

    download_snapshot(
        bucket_name=bucket_name,
        object_name=object_name,
    )


if __name__ == "__main__":
    main()