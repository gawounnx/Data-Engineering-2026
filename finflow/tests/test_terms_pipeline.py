"""금융용어 생성 파이프라인(scripts/terms/) 오프라인 테스트.

실제 PDF·BigQuery·임베딩 모델 없이, 운영 데이터 보호 규칙과
단계 간 입출력 형식을 확인한다.
- BigQuery 적재: 기본 dry-run, 확인 문자열이 맞을 때만 WRITE_TRUNCATE
- 청킹: BigQuery는 읽기만, 오프라인 모드는 적재와 같은 변환
- ChromaDB 적재: 서비스 DB 보호, 기존 컬렉션은 --replace일 때만 초기화
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import PROJECT_ROOT

TERMS_DIR = PROJECT_ROOT / "scripts" / "terms"
DIM = 4


@pytest.fixture(autouse=True)
def terms_env(tmp_path, monkeypatch):
    """작업 폴더를 tmp_path로 돌리고 BigQuery 설정을 가짜 값으로 둔다."""
    monkeypatch.setenv("FINFLOW_TERMS_DATA_DIR", str(tmp_path / "terms"))
    monkeypatch.setenv("GCP_PROJECT_ID", "example-project")
    monkeypatch.setenv("BQ_DATASET_ID", "example_dataset")
    for name in ("TERMS_BQ_TABLE", "TERMS_PDF_PATH", "BQ_LOCATION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.syspath_prepend(str(TERMS_DIR))
    for name in ("terms_config", "load_terms_to_bigquery"):
        sys.modules.pop(name, None)
    yield tmp_path / "terms"
    for name in ("terms_config", "load_terms_to_bigquery"):
        sys.modules.pop(name, None)


def load_script(name: str):
    """스크립트를 새로 불러온다 (경로 상수는 import 시점 환경 변수로 정해진다)."""
    path = TERMS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"terms_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_cleaned(data_dir: Path, terms: list[dict]) -> Path:
    path = data_dir / "processed" / "financial_terms_800_v4_cleaned.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"metadata": {}, "terms": terms}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


SAMPLE_TERMS = [
    {"term_id": "bok_term_0002", "term": "기준금리", "description": "한국은행이 정하는 정책금리이다. 금융통화위원회가 결정한다."},
    {"term_id": "bok_term_0001", "term": "가계부채", "description": "가계가 진 빚을 말한다. 주택담보대출이 큰 비중을 차지한다."},
]


# ---------------- terms_config ----------------


def test_default_paths_are_separate_from_service_data(terms_env, monkeypatch):
    monkeypatch.delenv("FINFLOW_TERMS_DATA_DIR")
    config = load_script("terms_config")

    assert config.data_dir() == PROJECT_ROOT / "data" / "terms"
    assert config.default_chroma_path() == PROJECT_ROOT / "data" / "terms" / "chroma"
    assert config.pdf_path().name == "2026_경제금융용어 800선.pdf"
    assert not config.is_service_chroma_path(config.default_chroma_path())
    assert config.is_service_chroma_path(PROJECT_ROOT / "data" / "chroma")


def test_table_id_requires_project_and_dataset(monkeypatch):
    config = load_script("terms_config")
    assert config.bigquery_table_id() == "example-project.example_dataset.financial_terms"

    monkeypatch.setenv("TERMS_BQ_TABLE", "financial_terms_test")
    assert config.bigquery_table_id().endswith(".financial_terms_test")

    monkeypatch.setenv("BQ_DATASET_ID", " ")
    with pytest.raises(ValueError, match="BQ_DATASET_ID"):
        config.bigquery_table_id()


def test_display_path_does_not_leak_absolute_paths(terms_env):
    config = load_script("terms_config")

    assert config.display_path(PROJECT_ROOT / "data" / "terms" / "x.json") == "data/terms/x.json"
    assert config.display_path(Path("/somewhere/else/x.json")) == "x.json"


# ---------------- load_terms_to_bigquery ----------------


def test_bigquery_default_is_dry_run_without_client(terms_env, capsys):
    write_cleaned(terms_env, SAMPLE_TERMS)
    module = load_script("load_terms_to_bigquery")

    with mock.patch.object(module.bigquery, "Client") as client:
        module.main([])

    client.assert_not_called()
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "WRITE_TRUNCATE" in out
    assert "example-project.example_dataset.financial_terms" in out


@pytest.mark.parametrize("confirm", ["", "example-project.finflow.financial_terms"])
def test_bigquery_execute_requires_exact_confirmation(terms_env, confirm):
    write_cleaned(terms_env, SAMPLE_TERMS)
    module = load_script("load_terms_to_bigquery")

    with mock.patch.object(module.bigquery, "Client") as client:
        with pytest.raises(SystemExit):
            module.main(["--execute", "--confirm-table", confirm])

    client.assert_not_called()


def test_bigquery_execute_truncates_confirmed_table(terms_env):
    write_cleaned(terms_env, SAMPLE_TERMS)
    module = load_script("load_terms_to_bigquery")
    table_id = "example-project.example_dataset.financial_terms"

    with mock.patch.object(module.bigquery, "Client") as client:
        client.return_value.get_table.return_value.num_rows = 2
        module.main(["--execute", "--confirm-table", table_id])

    client.assert_called_once_with(project="example-project")
    call = client.return_value.load_table_from_json.call_args
    rows, target = call.args
    assert target == table_id
    assert call.kwargs["job_config"].write_disposition == "WRITE_TRUNCATE"
    assert call.kwargs["location"] == "asia-northeast3"
    assert {row["term_id"] for row in rows} == {"bok_term_0001", "bok_term_0002"}
    assert {row["data_version"] for row in rows} == {"v4"}


# ---------------- chunk_terms ----------------


def test_chunk_offline_mode_matches_bigquery_rows(terms_env):
    write_cleaned(terms_env, SAMPLE_TERMS)
    module = load_script("chunk_terms")

    records = module.load_records_from_cleaned_json(module.CLEANED_JSON_PATH)

    # BigQuery SELECT와 같은 열, ORDER BY term_id와 같은 순서
    assert [r["term_id"] for r in records] == ["bok_term_0001", "bok_term_0002"]
    assert set(records[0]) == set(module.SOURCE_COLUMNS)
    assert records[0]["definition"].startswith("가계가 진 빚")
    assert records[0]["source_document"] == "경제금융용어"

    with mock.patch.object(module.bigquery, "Client") as client:
        module.main(["--from-cleaned-json"])

    client.assert_not_called()
    payload = json.loads(module.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert payload["metadata"]["source"].startswith("cleaned JSON")
    assert [c["chunk_id"] for c in payload["chunks"]] == [
        "bok_term_0001_chunk_000",
        "bok_term_0002_chunk_000",
    ]
    assert payload["chunks"][1]["text"].startswith("금융용어: 기준금리\n설명:")


def test_chunk_bigquery_mode_only_reads(terms_env, monkeypatch):
    monkeypatch.setenv("BQ_LOCATION", "asia-northeast3")
    module = load_script("chunk_terms")
    rows = [
        {
            "term_id": "bok_term_0001", "term": "가계부채",
            "definition": "가계가 진 빚을 말한다.", "source": "한국은행",
            "source_document": "경제금융용어", "data_version": "v4",
        }
    ]

    with mock.patch.object(module.bigquery, "Client") as client:
        client.return_value.query.return_value.result.return_value = [
            mock.Mock(items=lambda r=row: r.items()) for row in rows
        ]
        module.main([])

    sql = client.return_value.query.call_args.args[0]
    assert "FROM `example-project.example_dataset.financial_terms`" in sql
    assert sql.strip().upper().startswith("SELECT")
    assert client.return_value.query.call_args.kwargs["location"] == "asia-northeast3"
    client.return_value.load_table_from_json.assert_not_called()
    payload = json.loads(module.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert payload["metadata"]["source"] == "BigQuery financial_terms"


# ---------------- load_terms_to_chromadb ----------------


def write_embeddings(data_dir: Path, ids: list[str]) -> None:
    path = data_dir / "processed" / "financial_terms_embeddings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "chunk_id": chunk_id, "term_id": chunk_id.split("_chunk")[0], "term": f"용어{i}",
            "chunk_index": 0, "total_chunks": 1, "text": f"금융용어: 용어{i}\n설명: 설명 {i}",
            "text_length": 10, "source": "한국은행", "source_document": "경제금융용어",
            "data_version": "v4", "embedding": [0.1 * (i + 1)] * DIM,
        }
        for i, chunk_id in enumerate(ids)
    ]
    path.write_text(
        json.dumps({"metadata": {"model_name": "test-model"}, "embeddings": records}, ensure_ascii=False),
        encoding="utf-8",
    )


def collection_ids(path: Path, name: str) -> set[str]:
    import chromadb
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    return set(chromadb.PersistentClient(path=str(path)).get_collection(name).get(include=[])["ids"])


def test_chroma_default_path_is_terms_build_db(terms_env):
    write_embeddings(terms_env, ["t_0001_chunk_000", "t_0002_chunk_000"])
    module = load_script("load_terms_to_chromadb")

    module.main([])

    assert collection_ids(terms_env / "chroma", "financial_terms") == {"t_0001_chunk_000", "t_0002_chunk_000"}


def test_chroma_refuses_service_db_without_flag(terms_env):
    module = load_script("load_terms_to_chromadb")

    with pytest.raises(ValueError, match="서비스 ChromaDB"):
        module.resolve_chroma_path(Path("data/chroma"), allow_service_db=False)

    assert module.resolve_chroma_path(Path("data/chroma"), allow_service_db=True) == PROJECT_ROOT / "data" / "chroma"


def test_chroma_existing_collection_requires_replace(terms_env):
    chroma = terms_env / "chroma"
    write_embeddings(terms_env, ["t_0001_chunk_000"])
    load_script("load_terms_to_chromadb").main([])

    write_embeddings(terms_env, ["t_0009_chunk_000"])
    with pytest.raises(SystemExit):
        load_script("load_terms_to_chromadb").main([])

    assert collection_ids(chroma, "financial_terms") == {"t_0001_chunk_000"}


def test_chroma_replace_resets_only_terms_collection(terms_env):
    import chromadb
    from chromadb.api.client import SharedSystemClient

    chroma = terms_env / "chroma"
    chroma.mkdir(parents=True)
    client = chromadb.PersistentClient(path=str(chroma))
    client.create_collection("financial_news").add(ids=["news-1"], embeddings=[[0.2] * DIM], documents=["뉴스"])
    client.create_collection("financial_terms").add(ids=["old"], embeddings=[[0.3] * DIM], documents=["옛 용어"])
    SharedSystemClient.clear_system_cache()

    write_embeddings(terms_env, ["t_0001_chunk_000", "t_0002_chunk_000"])
    load_script("load_terms_to_chromadb").main(["--replace"])

    assert collection_ids(chroma, "financial_terms") == {"t_0001_chunk_000", "t_0002_chunk_000"}
    assert collection_ids(chroma, "financial_news") == {"news-1"}
