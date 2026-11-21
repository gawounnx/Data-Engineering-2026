"""네이버 금융뉴스 원본 JSON을 GCS에 업로드한다."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.cloud import storage


PROJECT_ID = "melodic-furnace-501500-g4"
BUCKET_NAME = "dbai-2026-project"

INPUT_PATH = Path(
    "data/raw/naver_financial_news.json"
)

KST = ZoneInfo("Asia/Seoul")


def validate_input_file(path: Path) -> None:
    """업로드할 뉴스 파일이 존재하고 비어 있지 않은지 확인한다."""
    if not path.exists():
        raise FileNotFoundError(
            f"뉴스 파일을 찾을 수 없습니다: {path}"
        )

    if path.stat().st_size == 0:
        raise ValueError(
            f"뉴스 파일이 비어 있습니다: {path}"
        )


def create_blob_name(now: datetime) -> str:
    """실행 시각을 기준으로 GCS 객체 경로를 생성한다."""
    return (
        "finflow/news/raw/"
        f"{now:%Y/%m/%d}/"
        f"naver_financial_news_{now:%H%M%S}.json"
    )


def upload_file(
    source_path: Path,
    bucket_name: str,
    blob_name: str,
) -> str:
    """로컬 JSON 파일을 GCS에 업로드한다."""
    client = storage.Client(
        project=PROJECT_ID,
    )

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    blob.upload_from_filename(
        str(source_path),
        content_type="application/json",
    )

    return f"gs://{bucket_name}/{blob_name}"


def main() -> None:
    """뉴스 원본을 GCS에 업로드하고 결과를 출력한다."""
    validate_input_file(INPUT_PATH)

    now = datetime.now(KST)
    blob_name = create_blob_name(now)

    gcs_uri = upload_file(
        source_path=INPUT_PATH,
        bucket_name=BUCKET_NAME,
        blob_name=blob_name,
    )

    print("\n[GCS 뉴스 원본 업로드 완료]")
    print(f"- 로컬 파일: {INPUT_PATH.resolve()}")
    print(f"- 파일 크기: {INPUT_PATH.stat().st_size:,} bytes")
    print(f"- GCS 경로: {gcs_uri}")


if __name__ == "__main__":
    main()