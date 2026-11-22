"""빈 값으로 정의된 환경 변수가 코드 기본값을 덮어쓰지 않는지 검증한다.

.env.example을 그대로 복사하면 GCP_PROJECT_ID= 처럼 빈 값이 정의된다.
외부 호출은 mock으로 막는다.
"""

from __future__ import annotations

import importlib
import importlib.util
from unittest import mock

import pytest

from conftest import PROJECT_ROOT


def load_script(relative_path: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EMPTY_BQ_ENV = {"GCP_PROJECT_ID": "", "BQ_DATASET_ID": "", "BQ_LOCATION": ""}


def test_chunker_uses_defaults_for_empty_bigquery_env(monkeypatch):
    chunker = load_script("scripts/processors/chunk_financial_news.py")
    for key, value in EMPTY_BQ_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(chunker, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(chunker.bigquery, "Client", mock.MagicMock())
    monkeypatch.setattr(chunker, "read_ingest_state", lambda path: chunker.IngestState())
    captured = {}

    def stop_after_capture(client, project_id, dataset_id, location, **kwargs):
        captured.update(project_id=project_id, dataset_id=dataset_id, location=location)
        raise ValueError("stop")

    monkeypatch.setattr(chunker, "fetch_financial_news", stop_after_capture)

    with pytest.raises(SystemExit):
        chunker.main()

    assert captured == {
        "project_id": "melodic-furnace-501500-g4",
        "dataset_id": "finflow",
        "location": "asia-northeast3",
    }


def test_upload_uses_defaults_for_empty_env(monkeypatch):
    uploader = load_script("scripts/vectorstores/upload_chroma_snapshot.py")
    monkeypatch.setenv("FINFLOW_BUCKET", "")
    monkeypatch.setenv("CHROMA_SNAPSHOT_OBJECT", "")
    monkeypatch.setattr(uploader, "create_snapshot", lambda: None)
    monkeypatch.setattr(uploader, "validate_chroma", lambda *a, **k: {})

    with mock.patch.object(uploader, "upload_snapshot") as upload:
        uploader.main()

    upload.assert_called_once_with(
        bucket_name=uploader.DEFAULT_BUCKET_NAME,
        object_name=uploader.DEFAULT_OBJECT_NAME,
    )


def test_start_streamlit_uses_default_port_for_empty_env(monkeypatch):
    launcher = load_script("scripts/start_streamlit.py")
    monkeypatch.setenv("PORT", "")
    commands = []
    monkeypatch.setattr(launcher, "run_command", lambda command: commands.append(command))

    launcher.main()

    assert "--server.port=8080" in commands[-1]


def test_event_logger_uses_default_url_for_empty_env(monkeypatch):
    import utils.event_logger as event_logger

    monkeypatch.setenv("LOG_COLLECTOR_URL", "")
    try:
        reloaded = importlib.reload(event_logger)
        assert reloaded.LOG_COLLECTOR_URL == "http://127.0.0.1:8080"
    finally:
        monkeypatch.delenv("LOG_COLLECTOR_URL")
        importlib.reload(event_logger)
