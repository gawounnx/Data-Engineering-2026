"""GCS의 최신 ChromaDB를 복원한 뒤 Streamlit 앱을 실행한다."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DOWNLOAD_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "vectorstores"
    / "download_chroma_snapshot.py"
)


def run_command(command: list[str]) -> None:
    """외부 명령을 실행하고 실패하면 프로그램을 종료한다."""
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    """최신 벡터 DB를 복원하고 Streamlit을 시작한다."""
    port = os.getenv(
        "PORT",
        "8080",
    ).strip()

    print("[시작] 최신 ChromaDB 스냅샷 복원")

    run_command(
        [
            sys.executable,
            str(DOWNLOAD_SCRIPT),
        ]
    )

    print("[완료] 최신 ChromaDB 스냅샷 복원")
    print(f"[시작] Streamlit 서버: 포트 {port}")

    run_command(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.address=0.0.0.0",
            f"--server.port={port}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ]
    )


if __name__ == "__main__":
    main()