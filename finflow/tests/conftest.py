"""FinFlow 테스트 공통 설정.

- 외부 네트워크(GCS, 로그 수집 서버, Gemini)를 호출하지 않는다.
- ChromaDB는 테스트마다 tmp_path에 새로 만든다 (실제 데이터·원본 경로 불필요).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# src.llm은 import 시 load_dotenv()로 .env를 읽는다. 먼저 import해 두고
# 각 테스트에서 스냅샷 관련 환경 변수를 지워 로컬 .env의 영향을 막는다.
import src.llm  # noqa: E402,F401
import src.news_retriever as news_retriever  # noqa: E402
import src.news_snapshot as news_snapshot  # noqa: E402
import src.retriever as retriever  # noqa: E402

EMBEDDING_DIM = 4


def make_chroma(
    path: Path,
    terms: int = 0,
    news: int = 0,
    create_terms: bool = True,
    create_news: bool = True,
) -> Path:
    """지정한 문서 수로 financial_terms / financial_news 컬렉션을 만든다."""
    import chromadb

    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(path))

    for name, count, create in (
        ("financial_terms", terms, create_terms),
        ("financial_news", news, create_news),
    ):
        if not create:
            continue

        collection = client.get_or_create_collection(name)

        if count:
            collection.add(
                ids=[f"{name}-{i}" for i in range(count)],
                documents=[f"{name} 문서 {i}" for i in range(count)],
                embeddings=[[0.1 * (i + 1)] * EMBEDDING_DIM for i in range(count)],
            )

    return path


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """데이터 경로·모듈 상태를 테스트별 임시 경로로 격리한다.

    반환값: (news_snapshot 모듈, 로컬 chroma 경로)
    """
    for name in ("FINFLOW_BUCKET", "CHROMA_SNAPSHOT_OBJECT"):
        monkeypatch.delenv(name, raising=False)

    local_chroma = tmp_path / "data" / "chroma"
    local_chroma.mkdir(parents=True)
    (local_chroma / ".gitkeep").touch()

    monkeypatch.setattr(news_snapshot, "LOCAL_CHROMA_PATH", local_chroma)
    monkeypatch.setattr(
        news_snapshot,
        "LOCAL_NEWS_CHUNKS_PATH",
        tmp_path / "data" / "processed" / "financial_news_chunks.json",
    )
    monkeypatch.setattr(news_snapshot, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(news_snapshot, "_current_generation", None)
    monkeypatch.setattr(
        news_snapshot,
        "_current_status",
        news_snapshot.NewsSnapshotStatus(updated_at=None, generation=None),
    )
    monkeypatch.setattr(news_snapshot, "_last_checked_at", 0.0)
    monkeypatch.setattr(retriever, "CHROMA_PATH", local_chroma)
    monkeypatch.setattr(news_retriever, "CHROMA_PATH", local_chroma)
    monkeypatch.setattr(
        news_retriever,
        "DEFAULT_NEWS_PATH",
        tmp_path / "data" / "raw" / "naver_financial_news.json",
    )

    retriever.load_collection.cache_clear()
    news_retriever.load_news_articles.cache_clear()

    yield news_snapshot, local_chroma

    retriever.load_collection.cache_clear()
    news_retriever.load_news_articles.cache_clear()
