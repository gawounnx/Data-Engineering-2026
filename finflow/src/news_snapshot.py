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
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

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


class UnsafeSnapshotError(SnapshotValidationError):
    """스냅샷 압축 파일에 허용하지 않는 항목이 있을 때 발생한다."""


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
# 마지막으로 GCS를 확인한 time.monotonic() 값. None이면 아직 한 번도 확인하지 않음.
# monotonic()의 기준점은 플랫폼마다 달라 새 인스턴스에서는 0에 가까울 수 있으므로,
# 0.0 같은 숫자로 '미확인'을 표현하면 시작 직후 확인 간격 동안 조회를 건너뛴다.
_last_checked_at: float | None = None
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


def _describe_member_type(member: tarfile.TarInfo) -> str:
    """허용하지 않는 tar 항목의 종류를 설명한다."""
    if member.issym():
        return "심볼릭 링크"
    if member.islnk():
        return "하드 링크"
    if member.ischr():
        return "문자 장치 파일"
    if member.isblk():
        return "블록 장치 파일"
    if member.isfifo():
        return "FIFO"
    return f"지원하지 않는 항목 유형({member.type!r})"


def _validate_member(
    member: tarfile.TarInfo,
    destination: Path,
) -> None:
    """tar 항목 하나가 대상 폴더 안의 일반 파일·디렉터리인지 확인한다."""
    name = member.name

    if not (member.isfile() or member.isdir()):
        raise UnsafeSnapshotError(
            "스냅샷에 허용하지 않는 항목이 있어 거부했습니다: "
            f"{name} ({_describe_member_type(member)})"
        )

    posix_path = PurePosixPath(name)

    if (
        not name
        or posix_path.is_absolute()
        or PureWindowsPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or ".." in posix_path.parts
        or ".." in PureWindowsPath(name).parts
    ):
        raise UnsafeSnapshotError(
            f"스냅샷에 안전하지 않은 경로가 있어 거부했습니다: {name}"
        )

    member_path = (destination / name).resolve()

    if member_path == destination:
        if member.isdir():
            return
        raise UnsafeSnapshotError(
            f"스냅샷에 안전하지 않은 경로가 있어 거부했습니다: {name}"
        )

    if destination not in member_path.parents:
        raise UnsafeSnapshotError(
            f"스냅샷에 안전하지 않은 경로가 있어 거부했습니다: {name}"
        )


def _safe_extract(
    archive: tarfile.TarFile,
    destination: Path,
) -> None:
    """일반 파일·디렉터리만 대상 폴더 안에 압축 해제한다.

    - 모든 항목을 먼저 검사하고, 하나라도 위반하면 아무것도 풀지 않는다.
    - 절대 경로, '..', 대상 폴더 밖으로 해석되는 경로를 거부한다.
    - 심볼릭 링크, 하드 링크, 장치 파일, FIFO 등은 거부한다.
    - 지원되는 Python에서는 tarfile 'data' 필터를 함께 적용한다.
    """
    resolved_destination = destination.resolve()
    members = archive.getmembers()

    for member in members:
        _validate_member(member, resolved_destination)

    if hasattr(tarfile, "data_filter"):
        archive.extractall(
            resolved_destination,
            members=members,
            filter="data",
        )
    else:  # pragma: no cover - data 필터가 없는 오래된 Python
        archive.extractall(
            resolved_destination,
            members=members,
        )


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
    # GCS generation은 정수다. 경로로 쓰기 전에 확인한다.
    if not generation.isdigit():
        raise UnsafeSnapshotError(
            f"스냅샷 generation 값이 올바르지 않습니다: {generation!r}"
        )

    version_root = SNAPSHOT_ROOT / generation
    chroma_path = version_root / "chroma"

    if (chroma_path / CHROMA_DB_FILENAME).exists():
        return chroma_path

    version_root.mkdir(
        parents=True,
        exist_ok=True,
    )
    archive_path = version_root / "snapshot.tar.gz"

    try:
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
    except Exception:
        # 일부만 풀렸거나 거부된 스냅샷을 남기지 않는다.
        shutil.rmtree(version_root, ignore_errors=True)
        raise

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


def _is_check_due(now: float) -> bool:
    """GCS 스냅샷을 다시 확인할 때가 됐는지 판단한다.

    첫 호출(아직 확인한 적 없음)은 monotonic 값과 관계없이 즉시 확인하고,
    그 이후에는 마지막 확인 시점부터 CHECK_INTERVAL_SECONDS가 지나야 확인한다.
    """
    if _last_checked_at is None:
        return True

    return now - _last_checked_at >= CHECK_INTERVAL_SECONDS


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
        and not _is_check_due(now)
    ):
        return _current_status

    with _sync_lock:
        now = time.monotonic()

        if (
            not force
            and not _is_check_due(now)
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
