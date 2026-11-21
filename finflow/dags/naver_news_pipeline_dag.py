"""네이버 금융뉴스를 수집하고 클라우드에 적재하는 Airflow DAG."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.exceptions import AirflowException


def resolve_project_root() -> Path:
    """FinFlow 프로젝트 루트를 결정한다.

    1. FINFLOW_PROJECT_ROOT 환경 변수
    2. 기본값: 이 DAG 파일의 상위 폴더 (finflow/dags/ → finflow/)
       DAG를 다른 dags 폴더로 복사해 쓰면 환경 변수를 지정해야 한다.
       심볼릭 링크는 resolve()로 원본 위치를 따라간다.
    """
    configured_root = os.getenv("FINFLOW_PROJECT_ROOT", "").strip()

    if configured_root:
        return Path(configured_root).expanduser().resolve()

    return Path(__file__).resolve().parents[1]


def resolve_finflow_python(project_root: Path) -> Path:
    """스크립트를 실행할 FinFlow 가상환경 Python을 결정한다.

    1. FINFLOW_PYTHON 환경 변수
    2. 기본값: <프로젝트 루트>/.venv의 Python (macOS·Linux, Windows 순)

    가상환경 Python은 심볼릭 링크를 따라가면 패키지 경로가 바뀌므로
    resolve()하지 않는다.
    """
    configured_python = os.getenv("FINFLOW_PYTHON", "").strip()

    if configured_python:
        return Path(configured_python).expanduser()

    candidates = [
        project_root / ".venv" / "bin" / "python",
        project_root / ".venv" / "Scripts" / "python.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


PROJECT_ROOT = resolve_project_root()

FINFLOW_PYTHON = resolve_finflow_python(PROJECT_ROOT)

NEWS_FILE_PATH = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "naver_financial_news.json"
)

COLLECTOR_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "collectors"
    / "collect_naver_news.py"
)

GCS_UPLOAD_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "loaders"
    / "upload_news_to_gcs.py"
)

BIGQUERY_LOAD_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "loaders"
    / "load_news_to_bigquery.py"
)

MINIMUM_ARTICLE_COUNT = 1
KST = pendulum.timezone("Asia/Seoul")


def run_finflow_script(
    script_path: Path,
    task_name: str,
) -> str:
    """FinFlow 가상환경으로 프로젝트 스크립트를 실행한다."""
    if not FINFLOW_PYTHON.exists():
        raise AirflowException(
            f"FinFlow Python을 찾을 수 없습니다: {FINFLOW_PYTHON}\n"
            "프로젝트 루트에 .venv를 만들거나 FINFLOW_PYTHON 환경 변수로 "
            "Python 경로를 지정하세요. "
            f"(현재 FINFLOW_PROJECT_ROOT: {PROJECT_ROOT})"
        )

    if not script_path.exists():
        raise AirflowException(
            f"{task_name} 스크립트를 찾을 수 없습니다: {script_path}"
        )

    command = [
        str(FINFLOW_PYTHON),
        str(script_path),
    ]

    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise AirflowException(
            f"{task_name} 작업에 실패했습니다.\n"
            f"표준 출력:\n{error.stdout}\n"
            f"오류 출력:\n{error.stderr}"
        ) from error

    if result.stdout:
        print(result.stdout)

    if result.stderr:
        print("[표준 오류 출력]")
        print(result.stderr)

    return result.stdout


@dag(
    dag_id="naver_financial_news_pipeline",
    start_date=pendulum.datetime(
        2026,
        8,
        1,
        tz=KST,
    ),
    schedule="0 * * * *",
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "finflow",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["finflow", "naver", "news", "gcs", "bigquery"],
)
def naver_financial_news_pipeline() -> None:
    """FinFlow 뉴스 수집·검증·클라우드 적재 파이프라인."""

    @task
    def collect_news() -> str:
        """네이버 금융뉴스를 수집하고 로컬 JSON으로 저장한다."""
        run_finflow_script(
            script_path=COLLECTOR_SCRIPT,
            task_name="네이버 금융뉴스 수집",
        )

        return str(NEWS_FILE_PATH)

    @task
    def validate_news_file(
        file_path: str,
    ) -> dict[str, Any]:
        """생성된 뉴스 JSON의 구조와 필수값을 검증한다."""
        path = Path(file_path)

        if not path.exists():
            raise AirflowException(
                f"뉴스 파일을 찾을 수 없습니다: {path}"
            )

        try:
            with path.open(
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)
        except json.JSONDecodeError as error:
            raise AirflowException(
                f"뉴스 JSON 형식이 올바르지 않습니다: {error}"
            ) from error

        articles = data.get("articles")

        if not isinstance(articles, list):
            raise AirflowException(
                "JSON에 articles 리스트가 없습니다."
            )

        if len(articles) < MINIMUM_ARTICLE_COUNT:
            raise AirflowException(
                "수집된 뉴스 기사가 없습니다."
            )

        required_fields = {
            "article_id",
            "title",
            "summary",
            "published_at",
            "category",
        }

        invalid_articles: list[int] = []

        for index, article in enumerate(articles):
            if not isinstance(article, dict):
                invalid_articles.append(index)
                continue

            missing_fields = [
                field
                for field in required_fields
                if not str(article.get(field, "")).strip()
            ]

            if missing_fields:
                invalid_articles.append(index)

        valid_count = len(articles) - len(invalid_articles)

        if valid_count < MINIMUM_ARTICLE_COUNT:
            raise AirflowException(
                "유효한 뉴스 기사가 없습니다."
            )

        result = {
            "file_path": str(path),
            "total_count": len(articles),
            "valid_count": valid_count,
            "invalid_count": len(invalid_articles),
        }

        print("[뉴스 품질 검증 결과]")
        print(f"- 전체 기사: {result['total_count']}건")
        print(f"- 유효 기사: {result['valid_count']}건")
        print(f"- 오류 기사: {result['invalid_count']}건")

        return result

    @task
    def upload_news_to_gcs(
        validation_result: dict[str, Any],
    ) -> dict[str, Any]:
        """검증이 끝난 뉴스 원본 JSON을 GCS에 업로드한다."""
        run_finflow_script(
            script_path=GCS_UPLOAD_SCRIPT,
            task_name="GCS 뉴스 업로드",
        )

        print(
            "[GCS 업로드 대상 검증 기사]",
            validation_result["valid_count"],
        )

        return validation_result

    @task
    def load_news_to_bigquery(
        validation_result: dict[str, Any],
    ) -> dict[str, Any]:
        """신규 금융뉴스만 BigQuery에 적재한다."""
        run_finflow_script(
            script_path=BIGQUERY_LOAD_SCRIPT,
            task_name="BigQuery 뉴스 적재",
        )

        return validation_result

    @task
    def check_result(
        validation_result: dict[str, Any],
    ) -> None:
        """전체 뉴스 파이프라인의 최종 결과를 출력한다."""
        print("[FinFlow 뉴스 클라우드 파이프라인 완료]")
        print(
            "- 파일:",
            validation_result["file_path"],
        )
        print(
            "- 전체 기사:",
            validation_result["total_count"],
        )
        print(
            "- 유효 기사:",
            validation_result["valid_count"],
        )
        print(
            "- 오류 기사:",
            validation_result["invalid_count"],
        )
        print("- GCS 원본 보관: 완료")
        print("- BigQuery 신규 기사 적재: 완료")

    collected_file = collect_news()

    validation_result = validate_news_file(
        collected_file
    )

    gcs_result = upload_news_to_gcs(
        validation_result
    )

    bigquery_result = load_news_to_bigquery(
        gcs_result
    )

    check_result(
        bigquery_result
    )


naver_financial_news_pipeline()
