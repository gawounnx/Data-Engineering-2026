"""FinFlow 뉴스 클라우드 파이프라인을 순서대로 실행한다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

PIPELINE_SCRIPTS = [
    PROJECT_ROOT
    / "scripts"
    / "vectorstores"
    / "download_chroma_snapshot.py",
    PROJECT_ROOT
    / "scripts"
    / "collectors"
    / "collect_naver_news.py",
    PROJECT_ROOT
    / "scripts"
    / "loaders"
    / "upload_news_to_gcs.py",
    PROJECT_ROOT
    / "scripts"
    / "loaders"
    / "load_news_to_bigquery.py",
    PROJECT_ROOT
    / "scripts"
    / "processors"
    / "chunk_financial_news.py",
    PROJECT_ROOT
    / "scripts"
    / "embeddings"
    / "create_news_embeddings.py",
    PROJECT_ROOT
    / "scripts"
    / "vectorstores"
    / "load_news_to_chromadb.py",
    PROJECT_ROOT
    / "scripts"
    / "vectorstores"
    / "upload_chroma_snapshot.py",
]


def run_script(script_path: Path) -> None:
    """하나의 파이프라인 스크립트를 실행한다."""
    if not script_path.exists():
        raise FileNotFoundError(
            f"스크립트를 찾을 수 없습니다: {script_path}"
        )

    print(f"\n[실행 시작] {script_path.name}")

    subprocess.run(
        [sys.executable, str(script_path)],
        cwd=PROJECT_ROOT,
        check=True,
    )

    print(f"[실행 완료] {script_path.name}")


def main() -> None:
    """뉴스 수집부터 클라우드 적재까지 실행한다."""
    for script_path in PIPELINE_SCRIPTS:
        run_script(script_path)

    print("\n[FinFlow 뉴스 파이프라인 전체 완료]")


if __name__ == "__main__":
    main()