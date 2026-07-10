#!/usr/bin/env python3
"""
2일차 실습: 로컬 원본 JSON -> Cloud Storage 업로드 + BigQuery 적재

적재 대상:
- GCS: 원본 JSON 파일 (데이터 레이크)
- BigQuery ingest_metadata: 업로드 이력(감사/추적용)
- BigQuery posts: records 배열의 실제 게시글 데이터 (분석용)

사전 준비:
- GCP 인증: gcloud auth application-default login
- GCS 버킷 생성
- BigQuery 데이터셋 생성 (테이블은 스크립트가 없으면 생성)

사용 예시:
python day2_upload_to_gcp.py \
  --source ../data/raw_posts.json \
  --bucket my-bootcamp-bucket \
  --gcs-object week1/raw_posts.json \
  --project-id my-gcp-project \
  --dataset bootcamp \
  --table ingest_metadata \
  --posts-table posts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.cloud import storage

from data_quality import validate_posts_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GCS 업로드 + BigQuery 적재 스크립트")
    parser.add_argument("--source", type=Path, required=True, help="업로드할 로컬 JSON 파일 경로")
    parser.add_argument("--bucket", required=True, help="대상 GCS 버킷명")
    parser.add_argument("--gcs-object", required=True, help="업로드될 GCS 오브젝트 경로")
    parser.add_argument("--project-id", required=True, help="GCP 프로젝트 ID")
    parser.add_argument("--dataset", required=True, help="BigQuery 데이터셋 ID")
    parser.add_argument(
        "--table",
        default="ingest_metadata",
        help="업로드 이력 메타데이터 테이블 ID (기본값: ingest_metadata)",
    )
    parser.add_argument(
        "--posts-table",
        default="posts",
        help="게시글 데이터 테이블 ID (기본값: posts)",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("원본 JSON 루트는 객체(dict)여야 합니다.")
    return payload


def calculate_file_md5(path: Path) -> str:
    hasher = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def upload_to_gcs(source: Path, bucket_name: str, object_name: str, project_id: str) -> str:
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(source))
    return f"gs://{bucket_name}/{object_name}"


def ensure_metadata_table(client: bigquery.Client, table_id: str) -> None:
    schema = [
        bigquery.SchemaField("ingested_at_utc", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("source_file", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("gcs_uri", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("record_count", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("file_size_bytes", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("file_md5", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("run_by", "STRING", mode="NULLABLE"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    client.create_table(table, exists_ok=True)


def ensure_posts_table(client: bigquery.Client, table_id: str) -> None:
    schema = [
        bigquery.SchemaField("user_id", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("post_id", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("title", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("body", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("data_source", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("fetched_at_utc", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("ingested_at_utc", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("gcs_uri", "STRING", mode="REQUIRED"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    client.create_table(table, exists_ok=True)


def insert_metadata(
    client: bigquery.Client,
    table_id: str,
    source_file: Path,
    gcs_uri: str,
    payload: dict[str, Any],
) -> None:
    records = payload.get("records", [])
    record_count = len(records) if isinstance(records, list) else 0

    row = {
        "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": str(source_file),
        "gcs_uri": gcs_uri,
        "record_count": record_count,
        "file_size_bytes": source_file.stat().st_size,
        "file_md5": calculate_file_md5(source_file),
        "run_by": os.getenv("USER", "unknown"),
    }

    errors = client.insert_rows_json(table_id, [row])
    if errors:
        raise RuntimeError(f"BigQuery 메타데이터 insert 실패: {errors}")


def build_post_rows(payload: dict[str, Any], gcs_uri: str) -> list[dict[str, Any]]:
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise ValueError("payload.records는 리스트여야 합니다.")

    ingested_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("records 항목은 객체(dict)여야 합니다.")
        rows.append(
            {
                "user_id": record["userId"],
                "post_id": record["id"],
                "title": record["title"],
                "body": record["body"],
                "data_source": payload.get("source"),
                "fetched_at_utc": payload.get("fetched_at_utc"),
                "ingested_at_utc": ingested_at,
                "gcs_uri": gcs_uri,
            }
        )
    return rows


def load_posts(client: bigquery.Client, table_id: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_json(rows, table_id, job_config=job_config)
    job.result()

    if job.errors:
        raise RuntimeError(f"BigQuery posts load 실패: {job.errors}")

    return len(rows)


def main() -> None:
    args = parse_args()
    if not args.source.exists():
        raise FileNotFoundError(f"소스 파일을 찾을 수 없습니다: {args.source}")

    payload = load_json(args.source)
    validate_posts_payload(payload, min_records=1)
    gcs_uri = upload_to_gcs(args.source, args.bucket, args.gcs_object, args.project_id)

    bq_client = bigquery.Client(project=args.project_id)
    metadata_table_id = f"{args.project_id}.{args.dataset}.{args.table}"
    posts_table_id = f"{args.project_id}.{args.dataset}.{args.posts_table}"

    ensure_metadata_table(bq_client, metadata_table_id)
    ensure_posts_table(bq_client, posts_table_id)

    post_rows = build_post_rows(payload, gcs_uri)
    loaded_count = load_posts(bq_client, posts_table_id, post_rows)

    insert_metadata(
        client=bq_client,
        table_id=metadata_table_id,
        source_file=args.source,
        gcs_uri=gcs_uri,
        payload=payload,
    )

    print(f"[완료] GCS 업로드: {gcs_uri}")
    print(f"[완료] BigQuery 게시글 적재: {posts_table_id} ({loaded_count}건)")
    print(f"[완료] BigQuery 메타데이터 적재: {metadata_table_id}")


if __name__ == "__main__":
    main()
