"""뉴스 파이프라인 증분 처리·7일 보관·업로드 전 검증 테스트 (외부 호출 없음).

- chunk_financial_news.py: (collected_at, article_id) 진행 위치, 최근 7일, 최대 처리량,
  최신 기사 우선 + 남은 구간 채우기. 실제 SQL을 SQLite에서 실행해 검증한다.
- create_news_embeddings.py: 새 뉴스가 없으면 모델을 불러오지 않음
- load_news_to_chromadb.py: 7일 보관, 금융용어 보존, 실패 시 기존 컬렉션 유지
- upload_chroma_snapshot.py: 빈 컬렉션·누락 컬렉션 업로드 거부
- 앱: 뉴스 검색 범위와 화면 안내가 최근 7일
- 파이프라인: 실패 후 재실행 시 누락 없음, 교체·정리·스냅샷 복원 후 새 프로세스에서
  금융용어와 검색 결과 보존
BigQuery·GCS·임베딩 모델은 가짜 객체로 대체한다.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from streamlit.testing.v1 import AppTest

import src.news_retriever as news_retriever
import utils.event_logger as event_logger
from conftest import PROJECT_ROOT, make_chroma

SCRIPTS = PROJECT_ROOT / "scripts"
NOW = datetime.now(timezone.utc)
DIM = 4


def load_script(relative: str):
    path = SCRIPTS / relative
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clear_chroma_cache():
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    yield
    SharedSystemClient.clear_system_cache()


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """스크립트는 상대 경로(data/...)를 쓰므로 임시 프로젝트 폴더에서 실행한다."""
    monkeypatch.chdir(tmp_path)
    for name in ("NEWS_RETENTION_DAYS", "NEWS_MAX_ARTICLES_PER_RUN"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "data" / "processed").mkdir(parents=True)
    return tmp_path


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def chroma_client(path: Path):
    import chromadb
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    return chromadb.PersistentClient(path=str(path))


def add_news(path: Path, items: list[tuple[str, float, float]]) -> None:
    """(chunk_id, 발행 n일 전, 수집 n일 전)으로 뉴스 청크를 넣는다."""
    client = chroma_client(path)
    collection = client.get_or_create_collection("financial_news")
    collection.add(
        ids=[item[0] for item in items],
        embeddings=[[0.1 * (i + 1)] * DIM for i in range(len(items))],
        documents=[f"뉴스 {item[0]}" for item in items],
        metadatas=[
            {"article_id": item[0], "published_at": iso(item[1]), "collected_at": iso(item[2])}
            for item in items
        ],
    )


def news_ids(path: Path) -> set[str]:
    return set(chroma_client(path).get_collection("financial_news").get(include=[])["ids"])


def collection_names(path: Path) -> set[str]:
    return {c if isinstance(c, str) else c.name for c in chroma_client(path).list_collections()}


# ======================= chunk_financial_news.py =======================


@pytest.fixture
def chunker(workdir):
    return load_script("processors/chunk_financial_news.py")


@pytest.mark.parametrize("value", ["0", "-3", "seven"])
def test_invalid_limits_rejected(chunker, monkeypatch, value):
    monkeypatch.setenv("NEWS_MAX_ARTICLES_PER_RUN", value)

    with pytest.raises(ValueError, match="NEWS_MAX_ARTICLES_PER_RUN"):
        chunker.read_positive_int_env("NEWS_MAX_ARTICLES_PER_RUN", 2000)


def ts(value: datetime) -> str:
    """SQLite에서 문자열 비교로 시각 순서가 맞도록 UTC 고정 형식으로 바꾼다."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class SqliteBigQuery:
    """chunk_financial_news의 실제 SQL을 SQLite에서 실행하는 가짜 BigQuery.

    WHERE·ORDER BY·LIMIT 의미를 그대로 검증하기 위해 SQL 문자열을 바꾸지 않는다.
    (SQLite는 `백틱` 식별자와 @이름 파라미터를 지원한다.)
    """

    TABLE = "p.d.financial_news"
    COLUMNS = (
        "article_id", "title", "summary", "published_at", "original_url", "naver_url",
        "category", "financial_relevance_score", "search_queries", "collected_at", "source_type",
    )

    def __init__(self):
        import sqlite3

        self.db = sqlite3.connect(":memory:")
        self.db.execute(f"CREATE TABLE `{self.TABLE}` ({', '.join(self.COLUMNS)})")
        self.queries: list[dict] = []

    def insert(self, rows: list[dict]) -> None:
        self.db.executemany(
            f"INSERT INTO `{self.TABLE}` VALUES ({', '.join('?' * len(self.COLUMNS))})",
            [
                tuple(
                    ts(r[c]) if isinstance(r[c], datetime)
                    else json.dumps(r[c]) if isinstance(r[c], list)
                    else r[c]
                    for c in self.COLUMNS
                )
                for r in rows
            ],
        )

    def query(self, sql, job_config=None, location=None):
        params = {}
        for p in job_config.query_parameters:
            value = p.value
            if isinstance(value, datetime):
                value = ts(value)
            elif isinstance(value, bool):
                value = int(value)
            params[p.name] = value
        self.queries.append(params)
        cursor = self.db.execute(sql, params)
        names = [d[0] for d in cursor.description]
        rows = []
        for raw in cursor.fetchall():
            row = dict(zip(names, raw))
            for c in ("published_at", "collected_at"):
                row[c] = datetime.strptime(row[c], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
            rows.append(row)
        job = mock.MagicMock()
        job.result.side_effect = lambda page_size=None: [dict(r) for r in rows]
        return job


def article(article_id: str, published: datetime, collected: datetime, score: int = 3) -> dict:
    return {
        "article_id": article_id,
        "title": f"제목 {article_id}",
        "summary": "기준금리가 동결됐다. 시장은 다음 회의를 주목한다.",
        "published_at": published,
        "collected_at": collected,
        "original_url": "",
        "naver_url": "",
        "category": "통화정책",
        "financial_relevance_score": score,
        "search_queries": ["금리"],
        "source_type": "naver",
    }


def batch(prefix: str, count: int, hours_ago: float, published_hours_ago: float | None = None) -> list[dict]:
    """수집기 1회 실행분: 같은 collected_at을 공유한다."""
    collected = NOW - timedelta(hours=hours_ago)
    published = NOW - timedelta(hours=published_hours_ago if published_hours_ago is not None else hours_ago)
    return [article(f"{prefix}{i:05d}", published, collected) for i in range(count)]


def run_fetch(chunker, bq, state, max_articles, retention_days=7):
    records, new_state, counts = chunker.fetch_financial_news(
        client=bq, project_id="p", dataset_id="d", location="asia-northeast3",
        published_since=NOW - timedelta(days=retention_days),
        state=state, max_articles=max_articles,
    )
    # 상태는 컬렉션 메타데이터를 거쳐 다음 실행에 전달되므로 같은 경로로 왕복시킨다.
    restored = chunker.state_from_metadata(chunker.state_to_metadata(new_state))
    assert restored == new_state
    return [r["article_id"] for r in records], restored, counts


def drain(chunker, bq, state, max_articles, max_runs=50):
    """새 기사 없이 gap이 닫힐 때까지 실행한다."""
    seen: list[str] = []
    for _ in range(max_runs):
        ids, state, _ = run_fetch(chunker, bq, state, max_articles)
        seen += ids
        if not state.has_gap:
            return seen, state
    raise AssertionError("gap이 닫히지 않음(진행 정체)")


def test_state_roundtrip_and_legacy_metadata(chunker):
    key = (NOW, "a1")
    state = chunker.IngestState(cursor=key, gap_lower=None, gap_upper=(NOW - timedelta(hours=1), "b2"))

    assert chunker.state_from_metadata(chunker.state_to_metadata(state)) == state
    # 이전 형식 스냅샷(상태 없음) → 첫 실행과 같은 빈 상태
    assert chunker.state_from_metadata({"retention_policy": "recent_7_days"}) == chunker.IngestState()
    assert chunker.state_from_metadata(None) == chunker.IngestState()


def test_read_state_without_db_does_not_create_one(chunker, workdir):
    chroma = workdir / "data" / "chroma"
    chroma.mkdir(parents=True)

    assert chunker.read_ingest_state(chroma) == chunker.IngestState()
    assert not (chroma / "chroma.sqlite3").exists()


def test_query_is_parameterized_and_bounded(chunker):
    bq = SqliteBigQuery()
    bq.insert(batch("a", 3, hours_ago=1))

    run_fetch(chunker, bq, chunker.IngestState(), max_articles=50)

    sql = chunker.FETCH_QUERY_TEMPLATE
    assert "INTERVAL 1 YEAR" not in sql
    assert "LIMIT @row_limit" in sql
    assert "ORDER BY collected_at DESC, article_id DESC" in sql
    assert bq.queries[0]["row_limit"] == 51  # 남은 기사 여부 확인용 +1


def test_same_collected_at_across_2000_boundary_is_not_skipped_or_stuck(chunker):
    """한 번의 수집(같은 collected_at)이 2,500건이어도 2회에 걸쳐 빠짐·중복 없이 처리한다.

    이전 방식(collected_at >= 워터마크 + LIMIT)은 같은 2,000건만 반복해 읽었다.
    """
    bq = SqliteBigQuery()
    rows = batch("same", 2500, hours_ago=2)
    bq.insert(rows)

    first, state, counts = run_fetch(chunker, bq, chunker.IngestState(), 2000)
    assert len(first) == 2000 and counts["new_rows"] == 2000
    assert state.has_gap

    second, state, _ = run_fetch(chunker, bq, state, 2000)

    assert len(second) == 500
    assert not set(first) & set(second)
    assert set(first) | set(second) == {r["article_id"] for r in rows}
    assert not state.has_gap

    third, state, _ = run_fetch(chunker, bq, state, 2000)
    assert third == []


def test_boundary_inside_mixed_batches(chunker):
    """경계가 수집 배치 중간에 걸려도 같은 시각 기사가 빠지지 않는다."""
    bq = SqliteBigQuery()
    rows = batch("a", 1500, hours_ago=5) + batch("b", 1500, hours_ago=3) + batch("c", 700, hours_ago=1)
    bq.insert(rows)

    seen, _ = drain(chunker, bq, chunker.IngestState(), 2000)

    assert len(seen) == len(set(seen)) == 3700


def test_first_recovery_serves_newest_first(chunker):
    """이전 형식 스냅샷(상태 없음)에서 시작하면 최신 수집 기사부터 처리한다."""
    bq = SqliteBigQuery()
    rows = []
    for hour in range(7 * 24, 0, -1):  # 7일치, 시간마다 수집 1회
        rows += batch(f"h{hour:03d}_", 20, hours_ago=hour)
    rows += batch("too_old_", 30, hours_ago=1, published_hours_ago=8 * 24)  # 보관 기간 밖
    bq.insert(rows)

    first, state, _ = run_fetch(chunker, bq, chunker.IngestState(), 2000)

    newest_hours = {f"h{h:03d}_" for h in range(1, 101)}  # 100시간 × 20건 = 2,000건
    assert {i[:5] for i in first} == newest_hours
    assert state.cursor[1].startswith("h001_")
    assert state.has_gap

    rest, state = drain(chunker, bq, state, 2000)
    in_window = {r["article_id"] for r in rows if not r["article_id"].startswith("too_old_")}
    assert set(first) | set(rest) == in_window
    assert len(first) + len(rest) == len(in_window)  # 중복 처리 없음


def test_new_arrivals_take_priority_during_backfill(chunker):
    """채우는 중에도 새로 수집된 기사가 먼저 처리되고, 결국 모두 처리된다."""
    bq = SqliteBigQuery()
    bq.insert(batch("old", 600, hours_ago=50))
    state = chunker.IngestState()

    ids1, state, _ = run_fetch(chunker, bq, state, 250)
    bq.insert(batch("new", 100, hours_ago=0.5))
    ids2, state, counts = run_fetch(chunker, bq, state, 250)

    assert set(ids2) >= {f"new{i:05d}" for i in range(100)}
    assert counts["new_rows"] == 100 and counts["backfill_rows"] == 150

    rest, _ = drain(chunker, bq, state, 250)
    all_ids = ids1 + ids2 + rest
    assert len(all_ids) == len(set(all_ids)) == 700


def test_overflow_while_gap_open_merges_without_loss(chunker):
    """채우는 중에 처리량보다 많은 새 기사가 와도(재장애) 빠지는 기사가 없다."""
    bq = SqliteBigQuery()
    bq.insert(batch("a", 500, hours_ago=60))
    state = chunker.IngestState()
    processed: set[str] = set()

    ids, state, _ = run_fetch(chunker, bq, state, 200)
    processed |= set(ids)
    bq.insert(batch("b", 450, hours_ago=10))  # 처리량(200)보다 많음 → gap 합침
    ids, state, _ = run_fetch(chunker, bq, state, 200)
    processed |= set(ids)
    assert ids[0].startswith("b")  # 최신 기사 우선

    rest, _ = drain(chunker, bq, state, 200)
    processed |= set(rest)
    assert processed == {f"a{i:05d}" for i in range(500)} | {f"b{i:05d}" for i in range(450)}


@pytest.mark.parametrize("seed", range(8))
def test_random_arrivals_never_skip_articles(chunker, seed):
    """무작위 수집량·처리량에서도 보관 기간 안 기사는 모두 처리된다."""
    import random

    rng = random.Random(seed)
    bq = SqliteBigQuery()
    state = chunker.IngestState()
    inserted: set[str] = set()
    processed: set[str] = set()

    for run in range(30):
        if rng.random() < 0.7:
            size = rng.choice([0, 1, 5, 40, 120, 400])
            rows = batch(f"r{run:02d}_", size, hours_ago=40 - run)
            bq.insert(rows)
            inserted |= {r["article_id"] for r in rows}
        ids, state, _ = run_fetch(chunker, bq, state, rng.choice([1, 7, 50, 150]))
        processed |= set(ids)

    rest, _ = drain(chunker, bq, state, 150, max_runs=500)
    assert processed | set(rest) == inserted


def run_chunker_main(chunker, bq, monkeypatch, max_articles=None):
    if max_articles:
        monkeypatch.setenv("NEWS_MAX_ARTICLES_PER_RUN", str(max_articles))
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("BQ_DATASET_ID", "d")
    monkeypatch.setattr(chunker, "load_dotenv", lambda *a, **k: None)
    with mock.patch.object(chunker.bigquery, "Client", return_value=bq):
        chunker.main()
    return json.loads(Path("data/processed/financial_news_chunks.json").read_text())


def test_main_with_no_new_articles_writes_empty_result(chunker, monkeypatch):
    payload = run_chunker_main(chunker, SqliteBigQuery(), monkeypatch)

    window = payload["metadata"]["fetch_window"]
    assert payload["chunks"] == []
    assert window["retention_days"] == 7
    assert window["max_articles"] == 2000
    assert window["ingest_state_after"]["ingest_state_version"] == 1


def test_main_reads_state_from_snapshot(chunker, workdir, monkeypatch):
    chroma = workdir / "data" / "chroma"
    make_chroma(chroma, terms=1, create_news=False)
    cursor = (NOW - timedelta(hours=2), "seen00001")
    chroma_client(chroma).create_collection(
        "financial_news",
        metadata=chunker.state_to_metadata(chunker.IngestState(cursor=cursor)),
    )
    bq = SqliteBigQuery()
    bq.insert(batch("seen", 3, hours_ago=2) + batch("next", 2, hours_ago=1))

    payload = run_chunker_main(chunker, bq, monkeypatch)

    # seen00000~00001은 이미 처리, 같은 시각의 seen00002는 아직 → 포함
    assert {c["article_id"] for c in payload["chunks"]} == {"seen00002", "next00000", "next00001"}


# ======================= create_news_embeddings.py =======================


def test_embeddings_skip_model_when_no_chunks(workdir):
    embedder = load_script("embeddings/create_news_embeddings.py")
    Path("data/processed/financial_news_chunks.json").write_text(
        json.dumps({"metadata": {}, "chunks": []}), encoding="utf-8"
    )

    with mock.patch.object(embedder, "load_model", side_effect=AssertionError("모델을 불러오면 안 됨")):
        embedder.main()

    payload = json.loads(Path("data/processed/financial_news_embeddings.json").read_text())
    assert payload["embeddings"] == []


# ======================= load_news_to_chromadb.py =======================


@pytest.fixture
def loader(workdir):
    return load_script("vectorstores/load_news_to_chromadb.py")


def write_embeddings(records: list[tuple[str, float]]) -> None:
    """(chunk_id, 발행 n일 전) 목록으로 임베딩 JSON을 만든다."""
    payload = {
        "metadata": {"model_name": "test-model"},
        "embeddings": [
            {
                "chunk_id": chunk_id,
                "article_id": chunk_id,
                "title": f"제목 {chunk_id}",
                "text": f"새 뉴스 {chunk_id}",
                "embedding": [0.5] * DIM,
                "published_at": iso(days_ago),
                "collected_at": iso(0),
            }
            for chunk_id, days_ago in records
        ],
    }
    Path("data/processed/financial_news_embeddings.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def existing_chroma(workdir):
    """금융용어 3건 + 뉴스(10일 전 2건, 2일 전 2건)."""
    chroma = workdir / "data" / "chroma"
    make_chroma(chroma, terms=3, create_news=False)
    add_news(chroma, [("old1", 10, 10), ("old2", 8, 8), ("keep1", 2, 2), ("keep2", 2, 2)])
    return chroma


def terms_count(path: Path) -> int:
    return chroma_client(path).get_collection("financial_terms").count()


def test_loader_keeps_7_days_adds_new_and_preserves_terms(loader, existing_chroma):
    write_embeddings([("keep2", 1), ("new1", 0.1), ("stale_new", 9)])

    loader.main()

    assert news_ids(existing_chroma) == {"keep1", "keep2", "new1"}
    assert terms_count(existing_chroma) == 3
    assert collection_names(existing_chroma) == {"financial_terms", "financial_news"}
    updated = chroma_client(existing_chroma).get_collection("financial_news").get(ids=["keep2"])
    assert updated["documents"] == ["새 뉴스 keep2"]  # 새 값으로 갱신


def test_loader_prunes_even_without_new_articles(loader, existing_chroma):
    write_embeddings([])

    loader.main()

    assert news_ids(existing_chroma) == {"keep1", "keep2"}
    assert terms_count(existing_chroma) == 3


def test_loader_retention_env(loader, existing_chroma, monkeypatch):
    monkeypatch.setenv("NEWS_RETENTION_DAYS", "9")
    write_embeddings([])

    loader.main()

    assert news_ids(existing_chroma) == {"old2", "keep1", "keep2"}


def test_loader_refuses_to_empty_news_and_keeps_existing(loader, workdir):
    chroma = workdir / "data" / "chroma"
    make_chroma(chroma, terms=3, create_news=False)
    add_news(chroma, [("old1", 10, 10)])
    write_embeddings([])

    with pytest.raises(SystemExit):
        loader.main()

    assert news_ids(chroma) == {"old1"}
    assert terms_count(chroma) == 3


def test_loader_failure_midway_keeps_existing_collection(loader, existing_chroma):
    write_embeddings([("new1", 0.1)])

    with mock.patch.object(loader, "upsert_records", side_effect=RuntimeError("boom")):
        with pytest.raises(SystemExit):
            loader.main()

    assert news_ids(existing_chroma) == {"old1", "old2", "keep1", "keep2"}
    assert terms_count(existing_chroma) == 3

    # 다음 실행은 남은 임시 컬렉션을 정리하고 정상 반영한다.
    loader.main()
    assert news_ids(existing_chroma) == {"keep1", "keep2", "new1"}
    assert collection_names(existing_chroma) == {"financial_terms", "financial_news"}


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def test_repeated_runs_do_not_grow_storage(loader, workdir):
    """매 실행 일부를 지우고 새로 넣어도 저장 공간이 계속 커지지 않는다."""
    chroma = workdir / "data" / "chroma"
    make_chroma(chroma, terms=3, create_news=False)

    # 초기 300건도 같은 형식(파이프라인 메타데이터)으로 넣는다. 발행 6일 전.
    write_embeddings([(f"c0_{i}", 6) for i in range(300)])
    loader.main()

    sizes = []
    for cycle in range(1, 7):
        # 가장 오래된 50건이 보관 기간을 넘기고 새 50건이 들어오는 상황
        client = chroma_client(chroma)
        collection = client.get_collection("financial_news")
        page = collection.get(include=["metadatas"])
        oldest = sorted(page["ids"], key=lambda i: page["metadatas"][page["ids"].index(i)]["published_at"])[:50]
        collection.update(
            ids=oldest,
            metadatas=[
                {**page["metadatas"][page["ids"].index(i)], "published_at": iso(30)} for i in oldest
            ],
        )
        write_embeddings([(f"c{cycle}_{i}", 6 - cycle * 0.5) for i in range(50)])

        loader.main()

        assert chroma_client(chroma).get_collection("financial_news").count() == 300
        sizes.append(dir_size(chroma))

    assert terms_count(chroma) == 3
    assert len([p for p in chroma.iterdir() if p.is_dir()]) == 2  # 용어·뉴스 인덱스만
    # 실행을 반복해도 크기가 계속 늘지 않아야 한다 (10% 여유)
    assert max(sizes) <= sizes[0] * 1.10, sizes


# ======================= upload_chroma_snapshot.py =======================


@pytest.fixture
def uploader(workdir, monkeypatch):
    module = load_script("vectorstores/upload_chroma_snapshot.py")
    monkeypatch.setattr(module, "ARCHIVE_PATH", workdir / "snapshot.tar.gz")
    return module


@pytest.mark.parametrize(
    ("terms", "news", "create_news", "message"),
    [(3, 0, True, "비어"), (3, 0, False, "financial_news"), (0, 2, True, "financial_terms")],
    ids=["empty-news", "missing-news", "empty-terms"],
)
def test_upload_refuses_invalid_chroma(uploader, workdir, terms, news, create_news, message):
    make_chroma(workdir / "data" / "chroma", terms=terms, news=news, create_news=create_news)

    with mock.patch.object(uploader.storage, "Client") as storage_client:
        with pytest.raises(uploader.InvalidSnapshotError, match=message):
            uploader.main()

    storage_client.assert_not_called()


def test_upload_refuses_missing_db(uploader, workdir):
    (workdir / "data" / "chroma").mkdir(parents=True)

    with mock.patch.object(uploader.storage, "Client") as storage_client:
        with pytest.raises(uploader.InvalidSnapshotError):
            uploader.main()

    storage_client.assert_not_called()


def test_upload_valid_chroma(uploader, workdir):
    make_chroma(workdir / "data" / "chroma", terms=3, news=2)

    with mock.patch.object(uploader.storage, "Client") as storage_client:
        uploader.main()

    blob = storage_client.return_value.bucket.return_value.blob.return_value
    assert blob.upload_from_filename.call_count == 2  # latest + history


# ======================= 앱: 뉴스 제공 범위 =======================


@pytest.mark.parametrize(
    ("question", "expected"),
    [("기준금리가 뭐야?", 7), ("올해 환율 흐름", 7), ("오늘 코스피", 3)],
)
def test_search_days_capped_at_retention(monkeypatch, question, expected):
    monkeypatch.delenv("NEWS_RETENTION_DAYS", raising=False)

    assert news_retriever.infer_search_days(question) == expected


@pytest.mark.parametrize(("value", "expected"), [("", 7), ("abc", 7), ("0", 7), ("14", 14)])
def test_retention_env_in_app(monkeypatch, value, expected):
    monkeypatch.setenv("NEWS_RETENTION_DAYS", value)

    assert news_retriever.get_news_retention_days() == expected


def test_app_shows_news_range(isolated, monkeypatch):
    _, local_chroma = isolated
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setattr(event_logger, "send_event", lambda *a, **k: False)
    monkeypatch.delenv("NEWS_RETENTION_DAYS", raising=False)
    make_chroma(local_chroma, terms=3, news=2)

    at = AppTest.from_file(str(PROJECT_ROOT / "app_ui.py"), default_timeout=120).run()

    assert not at.exception
    assert any("뉴스 제공 범위: 최근 7일" in c.value for c in at.caption)


# ======================= 파이프라인 재실행·스냅샷 복원 =======================
# Cloud Run Job 한 번 = 스냅샷 내려받기 → 청킹 → 임베딩 → ChromaDB 반영 → 업로드.
# 여기서 스냅샷(GCS)은 snapshot 폴더로, BigQuery는 SqliteBigQuery로 대신한다.

import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

import numpy as np  # noqa: E402

E2E_DIM = 8


def fake_vector(text: str, dim: int = E2E_DIM) -> np.ndarray:
    import hashlib

    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    vector = np.random.default_rng(seed).normal(size=dim).astype(np.float32)
    return vector / np.linalg.norm(vector)


class FakeModel:
    def encode(self, sentences, **kwargs):
        return np.stack([fake_vector(text) for text in sentences])


def publish_dir(src: Path, dst: Path) -> None:
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def run_job(workdir: Path, snapshot: Path, bq, monkeypatch, fail_at: str | None = None) -> None:
    """Job 1회를 흉내 낸다. 실패하면 스냅샷은 바뀌지 않는다."""
    chroma = workdir / "data" / "chroma"
    publish_dir(snapshot, chroma)  # download_chroma_snapshot

    chunker = load_script("processors/chunk_financial_news.py")
    embedder = load_script("embeddings/create_news_embeddings.py")
    loader = load_script("vectorstores/load_news_to_chromadb.py")
    uploader = load_script("vectorstores/upload_chroma_snapshot.py")

    run_chunker_main(chunker, bq, monkeypatch)
    with mock.patch.object(embedder, "load_model", return_value=FakeModel()):
        embedder.main()

    if fail_at == "loader":
        with mock.patch.object(loader, "upsert_records", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                loader.main()
        return

    loader.main()
    if fail_at == "upload":
        return  # 로컬 ChromaDB는 바뀌었지만 GCS 업로드 전에 실패

    uploader.validate_chroma(chroma)
    publish_dir(chroma, snapshot)  # upload_chroma_snapshot


def snapshot_articles(snapshot: Path) -> set[str]:
    page = chroma_client(snapshot).get_collection("financial_news").get(include=["metadatas"])
    return {m["article_id"] for m in page["metadatas"]}


@pytest.fixture
def legacy_snapshot(workdir):
    """현재 GCS 스냅샷과 같은 형태: 용어 + 진행 상태가 없는 오래된 뉴스."""
    snapshot = workdir / "snapshot"
    make_chroma(snapshot, terms=3, create_news=False)
    add_news(snapshot, [("legacy1", 100, 100), ("legacy2", 99, 99)])
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    return snapshot


def test_failed_runs_do_not_skip_unpublished_articles(workdir, legacy_snapshot, monkeypatch):
    monkeypatch.setenv("NEWS_MAX_ARTICLES_PER_RUN", "25")
    bq = SqliteBigQuery()
    bq.insert(batch("a", 30, hours_ago=3) + batch("b", 20, hours_ago=1))

    run_job(workdir, legacy_snapshot, bq, monkeypatch, fail_at="loader")
    assert snapshot_articles(legacy_snapshot) == {"legacy1", "legacy2"}

    run_job(workdir, legacy_snapshot, bq, monkeypatch, fail_at="upload")
    assert snapshot_articles(legacy_snapshot) == {"legacy1", "legacy2"}

    run_job(workdir, legacy_snapshot, bq, monkeypatch)  # 첫 성공: 최신 25건
    first = snapshot_articles(legacy_snapshot)
    assert first >= {f"b{i:05d}" for i in range(20)}
    assert len(first) == 25  # 오래된 legacy 뉴스는 보관 기간 밖이라 정리됨

    bq.insert(batch("c", 10, hours_ago=0.5))
    run_job(workdir, legacy_snapshot, bq, monkeypatch, fail_at="upload")
    for _ in range(5):
        run_job(workdir, legacy_snapshot, bq, monkeypatch)

    expected = (
        {f"a{i:05d}" for i in range(30)} | {f"b{i:05d}" for i in range(20)} | {f"c{i:05d}" for i in range(10)}
    )
    assert snapshot_articles(legacy_snapshot) == expected
    assert terms_count(legacy_snapshot) == 3


VERIFY_SCRIPT = r"""
import json, sys
import chromadb
path, n = sys.argv[1], int(sys.argv[2])
client = chromadb.PersistentClient(path=path)
terms = client.get_collection("financial_terms")
news = client.get_collection("financial_news")
dump = terms.get(include=["documents", "metadatas", "embeddings"])
order = sorted(range(len(dump["ids"])), key=lambda i: dump["ids"][i])
probe = [dump["embeddings"][i] for i in order[:n]]
hits = terms.query(query_embeddings=probe, n_results=1, include=[])["ids"]
news_dump = news.get(include=["embeddings"], limit=5)
news_hits = news.query(query_embeddings=news_dump["embeddings"], n_results=1, include=[])["ids"]
print(json.dumps({
    "ids": [dump["ids"][i] for i in order],
    "documents": [dump["documents"][i] for i in order],
    "metadatas": [dump["metadatas"][i] for i in order],
    "embeddings": [list(map(float, dump["embeddings"][i])) for i in order],
    "term_hits": [h[0] for h in hits],
    "news_ids": news_dump["ids"],
    "news_hits": [h[0] for h in news_hits],
    "news_metadata": news.metadata,
    "collections": sorted(c if isinstance(c, str) else c.name for c in client.list_collections()),
}, ensure_ascii=False))
"""


def run_in_new_process(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEWS_")}
    env["ANONYMIZED_TELEMETRY"] = "False"
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


# 실제 용어 수(1,121)처럼 HNSW 동기화 기준(1,000)을 넘겨 인덱스가 디스크에 저장되게 한다.
TERMS_N = 1200


def test_terms_and_search_survive_swap_vacuum_and_restore_in_new_process(workdir, monkeypatch):
    """임시 컬렉션 교체·VACUUM·인덱스 폴더 삭제 → 스냅샷 압축·복원 후,
    새 프로세스에서 금융용어 ID·내용·벡터와 실제 검색 결과가 그대로인지 확인한다."""
    import tarfile

    chroma = workdir / "data" / "chroma"
    rng = np.random.default_rng(7)
    terms_vectors = rng.normal(size=(TERMS_N, E2E_DIM)).astype(np.float32)
    client = chroma_client(chroma)
    terms = client.create_collection("financial_terms", metadata={"description": "금융용어"})
    for start in range(0, TERMS_N, 300):
        span = range(start, min(start + 300, TERMS_N))
        terms.add(
            ids=[f"term-{i:04d}" for i in span],
            embeddings=terms_vectors[start:span.stop].tolist(),
            documents=[f"금융용어 {i} 설명: 기준금리·환율 관련" for i in span],
            metadatas=[{"term": f"용어{i}", "category": ["금리", "환율", "주식"][i % 3]} for i in span],
        )
    add_news(chroma, [("legacy1", 100, 100)])
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    probe = run_in_new_process([sys.executable, "-c", VERIFY_SCRIPT, str(chroma), "1"], workdir)
    assert probe.returncode == 0, probe.stderr
    before = json.loads(probe.stdout)

    # 새 뉴스 40건 + 진행 위치를 담은 임베딩 JSON → 실제 스크립트를 새 프로세스로 실행
    state_after = {
        "ingest_state_version": 1,
        "ingest_cursor_collected_at": iso(0), "ingest_cursor_article_id": "n039",
        "ingest_gap_lower_collected_at": "", "ingest_gap_lower_article_id": "",
        "ingest_gap_upper_collected_at": "", "ingest_gap_upper_article_id": "",
    }
    payload = {
        "metadata": {"model_name": "fake", "chunking_metadata": {"fetch_window": {"ingest_state_after": state_after}}},
        "embeddings": [
            {
                "chunk_id": f"n{i:03d}_chunk_000", "article_id": f"n{i:03d}", "title": f"뉴스 {i}",
                "text": f"새 뉴스 본문 {i}", "embedding": fake_vector(f"news {i}").tolist(),
                "published_at": iso(0.5), "collected_at": iso(0),
            }
            for i in range(40)
        ],
    }
    Path("data/processed/financial_news_embeddings.json").write_text(json.dumps(payload, ensure_ascii=False))
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()

    loaded = run_in_new_process([sys.executable, str(SCRIPTS / "vectorstores/load_news_to_chromadb.py")], workdir)
    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    assert sorted(p.name for p in chroma.iterdir() if p.is_dir()).__len__() == 2  # 용어·뉴스 인덱스만

    # 업로드용 압축 → 내려받기용 안전 해제·검증 (GCS 대신 로컬 파일)
    uploader = load_script("vectorstores/upload_chroma_snapshot.py")
    downloader = load_script("vectorstores/download_chroma_snapshot.py")
    monkeypatch.setattr(uploader, "ARCHIVE_PATH", workdir / "snapshot.tar.gz")
    uploader.create_snapshot()
    restored_root = workdir / "restored"
    restored_root.mkdir()
    with tarfile.open(workdir / "snapshot.tar.gz", "r:gz") as archive:
        downloader.safe_extract(archive, restored_root)
    SharedSystemClient.clear_system_cache()

    result = run_in_new_process(
        [sys.executable, "-c", VERIFY_SCRIPT, str(restored_root / "chroma"), str(TERMS_N)], workdir
    )
    assert result.returncode == 0, result.stderr
    after = json.loads(result.stdout)

    assert after["collections"] == ["financial_news", "financial_terms"]
    assert after["ids"] == before["ids"]
    assert after["documents"] == before["documents"]
    assert after["metadatas"] == before["metadatas"]
    np.testing.assert_allclose(after["embeddings"], before["embeddings"], rtol=0, atol=1e-6)
    assert after["term_hits"] == after["ids"]  # 용어 전체가 자기 자신을 1위로 찾음
    assert after["news_hits"] == after["news_ids"]
    assert all(i.startswith("n") for i in after["news_ids"])
    assert {k: v for k, v in after["news_metadata"].items() if k.startswith("ingest_")} == state_after
