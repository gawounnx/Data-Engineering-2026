"""ChromaDB 데이터(금융용어·뉴스 컬렉션)를 확인하고 GCS 최신 스냅샷을 앱에 반영한다.

동작 모드
- 로컬 모드: FINFLOW_BUCKET과 CHROMA_SNAPSHOT_OBJECT가 모두 비어 있으면
  data/chroma를 그대로 사용하고, 필수 컬렉션이 있는지만 점검한다.
- GCS 모드: 둘 중 하나라도 설정되면 GCS 스냅샷(data/chroma 전체를 묶은
  tar.gz, 금융용어·뉴스 컬렉션 모두 포함)을 내려받아 검증한 뒤 검색 경로를 바꾼다.

어떤 모드든 필수 컬렉션이 없거나 비어 있으면 data_ready=False로 보고하며,
빈 DB를 정상 상태로 취급하지 않는다.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from google.api_core.exceptions import GoogleAPIError, NotFound
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CHROMA_PATH = PROJECT_ROOT / "data" / "chroma"
LOCAL_NEWS_CHUNKS_PATH = (
    PROJECT_ROOT / "data" / "processed" / "financial_news_chunks.json"
)
DEFAULT_BUCKET_NAME = "dbai-2026-project"
DEFAULT_OBJECT_NAME = "finflow/vectorstore/chroma/latest.tar.gz"
SNAPSHOT_ROOT = Path(tempfile.gettempdir()) / "finflow_chroma_versions"
CHECK_INTERVAL_SECONDS = 300

CHROMA_DB_FILENAME = "chroma.sqlite3"
REQUIRED_COLLECTIONS: tuple[str, ...] = (
    "financial_terms",
    "financial_news",
)
DATA_SETUP_GUIDE = (
    "데이터 준비 방법: finflow/README.md의 '데이터 준비'를 참고해 "
    "`python scripts/vectorstores/download_chroma_snapshot.py`로 "
    "스냅샷을 내려받거나, FINFLOW_BUCKET을 설정해 앱이 GCS 스냅샷을 "
    "불러오게 하세요."
)


class SnapshotValidationError(Exception):
    """스냅샷에 필수 컬렉션이 없거나 비어 있을 때 발생한다."""


@dataclass(frozen=True)
class NewsSnapshotStatus:
    """앱이 확인한 검색 데이터와 최신 스냅샷 상태."""

    updated_at: datetime | None
    generation: str | None
    refreshed: bool = False
    error: str | None = None
    source: str = "none"
    collections: tuple[tuple[str, int], ...] = ()
    missing_collections: tuple[str, ...] = REQUIRED_COLLECTIONS

    @property
    def data_ready(self) -> bool:
        """금융용어·뉴스 컬렉션이 모두 존재하고 비어 있지 않은지 여부."""
        return not self.missing_collections

    @property
    def collection_counts(self) -> dict[str, int]:
        """컬렉션별 문서 수."""
        return dict(self.collections)


_sync_lock = threading.Lock()
_last_checked_at = 0.0
_current_generation: str | None = None
_current_status = NewsSnapshotStatus(
    updated_at=None,
    generation=None,
)


def inspect_chroma(chroma_path: Path) -> dict[str, int]:
    """ChromaDB 컬렉션별 문서 수를 반환한다.

    DB 파일이 없으면 빈 dict를 반환한다. PersistentClient는 빈 폴더에서
    새 DB를 만들어 버리므로, 파일이 있을 때만 연다.
    """
    if not (chroma_path / CHROMA_DB_FILENAME).exists():
        return {}

    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))

    return {
        collection.name: client.get_collection(collection.name).count()
        for collection in client.list_collections()
    }


def find_missing_collections(
    counts: dict[str, int],
) -> tuple[str, ...]:
    """필수 컬렉션 중 없거나 비어 있는 컬렉션 이름을 반환한다."""
    return tuple(
        name
        for name in REQUIRED_COLLECTIONS
        if counts.get(name, 0) <= 0
    )


def _missing_message(
    missing: tuple[str, ...],
    location: str,
) -> str:
    return (
        f"{location}에 필수 컬렉션이 없거나 비어 있습니다: "
        f"{', '.join(missing)}. {DATA_SETUP_GUIDE}"
    )


def _safe_extract(
    archive: tarfile.TarFile,
    destination: Path,
) -> None:
    """경로 탈출을 차단하며 스냅샷 압축을 해제한다."""
    resolved_destination = destination.resolve()

    for member in archive.getmembers():
        member_path = (
            resolved_destination / member.name
        ).resolve()

        if (
            member_path != resolved_destination
            and resolved_destination not in member_path.parents
        ):
            raise ValueError(
                f"안전하지 않은 스냅샷 경로입니다: {member.name}"
            )

    archive.extractall(resolved_destination)


def _activate_snapshot(chroma_path: Path) -> None:
    """검색 모듈이 새 ChromaDB 경로를 사용하도록 전환한다."""
    from . import news_retriever, retriever

    news_retriever.CHROMA_PATH = chroma_path
    retriever.CHROMA_PATH = chroma_path
    news_retriever.load_news_articles.cache_clear()
    retriever.load_collection.cache_clear()


def _download_snapshot(
    blob: storage.Blob,
    generation: str,
) -> Path:
    """세대별 임시 경로에 스냅샷을 내려받고 검증한다."""
    version_root = SNAPSHOT_ROOT / generation
    chroma_path = version_root / "chroma"

    if (chroma_path / CHROMA_DB_FILENAME).exists():
        return chroma_path

    version_root.mkdir(
        parents=True,
        exist_ok=True,
    )
    archive_path = version_root / "snapshot.tar.gz"
    blob.download_to_filename(str(archive_path))

    with tarfile.open(archive_path, mode="r:gz") as archive:
        _safe_extract(
            archive=archive,
            destination=version_root,
        )

    if not (chroma_path / CHROMA_DB_FILENAME).exists():
        raise FileNotFoundError(
            "스냅샷에 ChromaDB 파일이 없습니다."
        )

    archive_path.unlink(missing_ok=True)
    return chroma_path


def _local_snapshot_status() -> NewsSnapshotStatus:
    """클라우드 설정이 없는 로컬 환경의 데이터 상태를 반환한다."""
    db_path = LOCAL_CHROMA_PATH / CHROMA_DB_FILENAME

    try:
        counts = inspect_chroma(LOCAL_CHROMA_PATH)
    except Exception as error:  # chromadb 내부 오류 유형이 다양함
        return NewsSnapshotStatus(
            updated_at=None,
            generation=None,
            source="local",
            error=(
                "로컬 ChromaDB를 열 수 없습니다 "
                f"({type(error).__name__}). {DATA_SETUP_GUIDE}"
            ),
        )

    missing = find_missing_collections(counts)

    timestamp_source = next(
        (
            path
            for path in (LOCAL_NEWS_CHUNKS_PATH, db_path)
            if path.exists()
        ),
        None,
    )
    updated_at = (
        datetime.fromtimestamp(
            timestamp_source.stat().st_mtime,
            tz=timezone.utc,
        )
        if timestamp_source and not missing
        else None
    )

    return NewsSnapshotStatus(
        updated_at=updated_at,
        generation="local",
        source="local",
        collections=tuple(sorted(counts.items())),
        missing_collections=missing,
        error=(
            _missing_message(missing, "로컬 data/chroma")
            if missing
            else None
        ),
    )


def _describe_sync_error(
    error: Exception,
    bucket_name: str,
    object_name: str,
) -> str:
    """스냅샷 동기화 실패 원인을 사용자용 문장으로 만든다."""
    location = f"gs://{bucket_name}/{object_name}"

    if isinstance(error, NotFound):
        return f"GCS 스냅샷을 찾을 수 없습니다: {location}"

    if isinstance(error, GoogleAuthError):
        return (
            "GCP 인증에 실패했습니다 "
            f"({type(error).__name__}). 로컬에서는 "
            "`gcloud auth application-default login`, Cloud Run에서는 "
            "서비스 계정의 버킷 읽기 권한을 확인하세요."
        )

    if isinstance(error, SnapshotValidationError):
        return str(error)

    return (
        f"스냅샷 동기화에 실패했습니다 ({type(error).__name__}): "
        f"{error}"
    )


def sync_latest_news_snapshot(
    force: bool = False,
) -> NewsSnapshotStatus:
    """최신 GCS 스냅샷을 확인하고 필요할 때 검색 경로를 갱신한다."""
    global _current_generation
    global _current_status
    global _last_checked_at

    bucket_name = os.getenv("FINFLOW_BUCKET", "").strip()
    object_name = os.getenv("CHROMA_SNAPSHOT_OBJECT", "").strip()

    if not bucket_name and not object_name:
        return _local_snapshot_status()

    bucket_name = bucket_name or DEFAULT_BUCKET_NAME
    object_name = object_name or DEFAULT_OBJECT_NAME
    now = time.monotonic()

    if (
        not force
        and now - _last_checked_at < CHECK_INTERVAL_SECONDS
    ):
        return _current_status

    with _sync_lock:
        now = time.monotonic()

        if (
            not force
            and now - _last_checked_at < CHECK_INTERVAL_SECONDS
        ):
            return _current_status

        try:
            client = storage.Client()
            blob = client.bucket(bucket_name).blob(object_name)
            blob.reload()

            generation = str(blob.generation or "")
            updated_at = blob.updated
            refreshed = False
            collections = _current_status.collections
            missing = _current_status.missing_collections

            if generation and generation != _current_generation:
                chroma_path = _download_snapshot(
                    blob=blob,
                    generation=generation,
                )
                try:
                    counts = inspect_chroma(chroma_path)
                except Exception as error:  # chromadb 내부 오류 유형이 다양함
                    raise SnapshotValidationError(
                        "내려받은 스냅샷의 ChromaDB를 열 수 없습니다 "
                        f"({type(error).__name__})."
                    ) from error

                new_missing = find_missing_collections(counts)

                if new_missing:
                    raise SnapshotValidationError(
                        _missing_message(
                            new_missing,
                            f"GCS 스냅샷(generation {generation})",
                        )
                    )

                _activate_snapshot(chroma_path)
                _current_generation = generation
                collections = tuple(sorted(counts.items()))
                missing = ()
                refreshed = True

            if not generation:
                raise SnapshotValidationError(
                    "GCS 스냅샷의 generation 정보를 확인할 수 없습니다."
                )

            _current_status = NewsSnapshotStatus(
                updated_at=updated_at,
                generation=generation,
                refreshed=refreshed,
                source="gcs",
                collections=collections,
                missing_collections=missing,
            )
        except (
            GoogleAPIError,
            GoogleAuthError,
            OSError,
            ValueError,
            tarfile.TarError,
            SnapshotValidationError,
        ) as error:
            message = _describe_sync_error(
                error,
                bucket_name,
                object_name,
            )

            if _current_generation is not None:
                # 이전에 검증·적용한 스냅샷으로 계속 서비스한다.
                _current_status = NewsSnapshotStatus(
                    updated_at=_current_status.updated_at,
                    generation=_current_status.generation,
                    source="gcs",
                    collections=_current_status.collections,
                    missing_collections=(
                        _current_status.missing_collections
                    ),
                    error=message,
                )
            else:
                # 적용된 스냅샷이 없으면 검색은 로컬 data/chroma를 쓰므로
                # 그 상태를 그대로 보고한다 (빈 DB면 data_ready=False).
                local_status = _local_snapshot_status()
                _current_status = NewsSnapshotStatus(
                    updated_at=local_status.updated_at,
                    generation=local_status.generation,
                    source="local-fallback",
                    collections=local_status.collections,
                    missing_collections=(
                        local_status.missing_collections
                    ),
                    error=(
                        message
                        if local_status.data_ready
                        else f"{message} {local_status.error}"
                    ),
                )
        finally:
            _last_checked_at = time.monotonic()

        return _current_status
