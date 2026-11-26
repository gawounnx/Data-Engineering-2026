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

REQUIRED_COLLECTIONS = ("financial_terms", "financial_news")


class InvalidSnapshotError(ValueError):
    """업로드하면 안 되는 ChromaDB 상태."""


def validate_chroma(chroma_path: Path = CHROMA_PATH) -> dict[str, int]:
    """필수 컬렉션이 모두 있고 비어 있지 않은지 확인한다.

    앱과 download_chroma_snapshot.py는 이 조건을 만족하지 않는 스냅샷을
    거부하므로, 올리기 전에 같은 조건으로 막는다.
    """
    if not (chroma_path / "chroma.sqlite3").exists():
        raise InvalidSnapshotError(
            f"ChromaDB 파일이 없습니다: {chroma_path / 'chroma.sqlite3'}"
        )

    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))
    counts: dict[str, int] = {}

    for name in REQUIRED_COLLECTIONS:
        try:
            counts[name] = client.get_collection(name).count()
        except Exception as error:
            raise InvalidSnapshotError(
                f"필수 컬렉션이 없습니다: {name}"
            ) from error

        if counts[name] == 0:
            raise InvalidSnapshotError(
                f"필수 컬렉션이 비어 있습니다: {name}"
            )

    return counts


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
    # 환경 변수가 없거나 빈 값이면 기본값을 사용한다.
    bucket_name = (
        os.getenv("FINFLOW_BUCKET", "").strip()
        or DEFAULT_BUCKET_NAME
    )

    object_name = (
        os.getenv("CHROMA_SNAPSHOT_OBJECT", "").strip()
        or DEFAULT_OBJECT_NAME
    )

    counts = validate_chroma()
    print(
        "[정보] 업로드 전 검증 통과: "
        + ", ".join(f"{name} {count:,}건" for name, count in counts.items())
    )

    create_snapshot()

    upload_snapshot(
        bucket_name=bucket_name,
        object_name=object_name,
    )


if __name__ == "__main__":
    main()