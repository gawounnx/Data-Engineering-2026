"""로컬 ChromaDB를 압축하여 GCS에 업로드한다."""

from __future__ import annotations

import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage


CHROMA_PATH = Path("data/chroma")
ARCHIVE_PATH = Path("/tmp/chroma_snapshot.tar.gz")

DEFAULT_BUCKET_NAME = "dbai-2026-project"
DEFAULT_OBJECT_NAME = "finflow/vectorstore/chroma/latest.tar.gz"


def create_snapshot() -> None:
    """ChromaDB 폴더를 tar.gz 파일로 압축한다."""
    if not CHROMA_PATH.exists():
        raise FileNotFoundError(
            f"ChromaDB 폴더가 없습니다: {CHROMA_PATH}"
        )

    with tarfile.open(
        ARCHIVE_PATH,
        mode="w:gz",
    ) as archive:
        archive.add(
            CHROMA_PATH,
            arcname="chroma",
        )


def upload_snapshot(
    bucket_name: str,
    object_name: str,
) -> None:
    """최신 스냅샷과 실행 이력 스냅샷을 GCS에 저장한다."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    latest_blob = bucket.blob(object_name)
    latest_blob.upload_from_filename(
        str(ARCHIVE_PATH)
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y/%m/%d/%H%M%S")

    history_object_name = (
        "finflow/vectorstore/chroma/history/"
        f"{timestamp}.tar.gz"
    )

    history_blob = bucket.blob(
        history_object_name
    )
    history_blob.upload_from_filename(
        str(ARCHIVE_PATH)
    )

    print("[ChromaDB 스냅샷 업로드 완료]")
    print(
        f"- 최신본: gs://{bucket_name}/{object_name}"
    )
    print(
        f"- 이력본: gs://{bucket_name}/{history_object_name}"
    )
    print(
        f"- 크기: {ARCHIVE_PATH.stat().st_size:,} bytes"
    )


def main() -> None:
    """ChromaDB 스냅샷 생성 및 업로드를 실행한다."""
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

    create_snapshot()

    upload_snapshot(
        bucket_name=bucket_name,
        object_name=object_name,
    )


if __name__ == "__main__":
    main()