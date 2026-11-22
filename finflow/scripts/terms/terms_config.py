"""금융용어 생성 파이프라인 공통 설정.

경로·환경 변수·운영 데이터 보호 규칙을 한곳에 모은다.
각 단계 스크립트는 `python scripts/terms/<스크립트>.py`로 실행하며,
이 파일은 같은 폴더에 있어 그대로 import된다.

기본 출력은 서비스 데이터(data/chroma, data/processed)와 분리된
data/terms/ 아래에 만든다.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 서비스(앱·뉴스 Job)가 쓰는 ChromaDB. GCS 스냅샷과 동기화되는 경로다.
SERVICE_CHROMA_PATH = PROJECT_ROOT / "data" / "chroma"

PDF_FILENAME = "2026_경제금융용어 800선.pdf"
TERMS_COLLECTION_NAME = "financial_terms"
DEFAULT_BQ_TABLE = "financial_terms"
DEFAULT_BQ_LOCATION = "asia-northeast3"
EMBEDDING_MODEL_NAME = "kekeappa/kor-static-embedding-128"


def env_or_default(name: str, default: str) -> str:
    """환경 변수가 없거나 빈 값이면 기본값을 사용한다."""
    return os.getenv(name, "").strip() or default


def data_dir() -> Path:
    """금융용어 작업 폴더. FINFLOW_TERMS_DATA_DIR로 바꿀 수 있다."""
    value = os.getenv("FINFLOW_TERMS_DATA_DIR", "").strip()
    path = Path(value) if value else PROJECT_ROOT / "data" / "terms"
    return path if path.is_absolute() else PROJECT_ROOT / path


def raw_dir() -> Path:
    return data_dir() / "raw"


def processed_dir() -> Path:
    return data_dir() / "processed"


def pdf_path() -> Path:
    """입력 PDF 경로. TERMS_PDF_PATH로 바꿀 수 있다."""
    value = os.getenv("TERMS_PDF_PATH", "").strip()
    return Path(value) if value else raw_dir() / PDF_FILENAME


def default_chroma_path() -> Path:
    """금융용어 전용 빌드 ChromaDB. 서비스 DB와 분리된 경로다."""
    return data_dir() / "chroma"


def is_service_chroma_path(path: Path) -> bool:
    """path가 서비스 ChromaDB(data/chroma)를 가리키면 True."""
    return path.resolve() == SERVICE_CHROMA_PATH.resolve()


def display_path(path: Path) -> str:
    """산출물 메타데이터용 경로. 프로젝트 안이면 상대 경로로 기록한다."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def bigquery_table_id() -> str:
    """금융용어 BigQuery 테이블 ID. 필수 값이 없으면 ValueError."""
    project_id = os.getenv("GCP_PROJECT_ID", "").strip()
    dataset_id = os.getenv("BQ_DATASET_ID", "").strip()
    table = env_or_default("TERMS_BQ_TABLE", DEFAULT_BQ_TABLE)

    if not project_id:
        raise ValueError("GCP_PROJECT_ID가 설정되지 않았습니다.")

    if not dataset_id:
        raise ValueError("BQ_DATASET_ID가 설정되지 않았습니다.")

    return f"{project_id}.{dataset_id}.{table}"


def bigquery_location() -> str:
    return env_or_default("BQ_LOCATION", DEFAULT_BQ_LOCATION)
