#!/usr/bin/env python3
"""FinFlow AI — 주식 시세 데이터 GCS 업로드 + BigQuery 적재"""

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

from data_quality import validate_stock_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--gcs-object", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--table", default="ingest_metadata")
    parser.add_argument("--stock-table", default="stock_prices")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def calculate_file_md5(path: Path) -> str:
    hasher = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def upload_to_gcs(source: Path, bucket_name: str, object_name: str, project_id: str) -> str:
    client = storage.Client(project=project_id)
    blob = client.bucket(bucket_name).blob(object_name)
    blob.upload_from_filename(str(source))
    gcs_uri = f"gs://{bucket_name}/{object_name}"
    print(f"[완료] GCS 업로드: {gcs_uri}")
    return gcs_uri


def ensure_stock_table(client: bigquery.Client, table_id: str) -> None:
    schema = [
        bigquery.SchemaField("bas_dt", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("srtn_cd", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("isin_cd", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("itms_nm", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("mrkt_ctg", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("clpr", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("vs", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("flt_rt", "FLOAT", mode="NULLABLE"),
        bigquery.SchemaField("mkp", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("hipr", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("lopr", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("trqu", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("tr_prc", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("mrkt_tot_amt", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("fetched_at_utc", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("ingested_at_utc", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("gcs_uri", "STRING", mode="REQUIRED"),
    ]
    client.create_table(bigquery.Table(table_id, schema=schema), exists_ok=True)


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
    client.create_table(bigquery.Table(table_id, schema=schema), exists_ok=True)


def build_stock_rows(payload: dict[str, Any], gcs_uri: str) -> list[dict[str, Any]]:
    records = payload.get("records", [])
    fetched_at = payload.get("fetched_at_utc")
    ingested_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in records:
        rows.append({
            "bas_dt": r.get("basDt"),
            "srtn_cd": r.get("srtnCd"),
            "isin_cd": r.get("isinCd"),
            "itms_nm": r.get("itmsNm"),
            "mrkt_ctg": r.get("mrktCtg"),
            "clpr": int(r["clpr"]) if r.get("clpr") else None,
            "vs": int(r["vs"]) if r.get("vs") else None,
            "flt_rt": float(r["fltRt"]) if r.get("fltRt") else None,
            "mkp": int(r["mkp"]) if r.get("mkp") else None,
            "hipr": int(r["hipr"]) if r.get("hipr") else None,
            "lopr": int(r["lopr"]) if r.get("lopr") else None,
            "trqu": int(r["trqu"]) if r.get("trqu") else None,
            "tr_prc": int(r["trPrc"]) if r.get("trPrc") else None,
            "mrkt_tot_amt": int(r["mrktTotAmt"]) if r.get("mrktTotAmt") else None,
            "fetched_at_utc": fetched_at,
            "ingested_at_utc": ingested_at,
            "gcs_uri": gcs_uri,
        })
    return rows


def load_to_bigquery(client: bigquery.Client, table_id: str, rows: list[dict]) -> int:
    job = client.load_table_from_json(
        rows, table_id,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND
        )
    )
    job.result()
    if job.errors:
        raise RuntimeError(f"BigQuery 적재 실패: {job.errors}")
    return len(rows)


def insert_metadata(client: bigquery.Client, table_id: str,
                    source: Path, gcs_uri: str, payload: dict) -> None:
    row = {
        "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": str(source),
        "gcs_uri": gcs_uri,
        "record_count": len(payload.get("records", [])),
        "file_size_bytes": source.stat().st_size,
        "file_md5": calculate_file_md5(source),
        "run_by": os.getenv("USER", "unknown"),
    }
    errors = client.insert_rows_json(table_id, [row])
    if errors:
        raise RuntimeError(f"메타데이터 적재 실패: {errors}")


def main() -> None:
    args = parse_args()
    payload = load_json(args.source)
    validate_stock_payload(payload)
    gcs_uri = upload_to_gcs(args.source, args.bucket, args.gcs_object, args.project_id)
    bq = bigquery.Client(project=args.project_id)
    stock_table_id = f"{args.project_id}.{args.dataset}.{args.stock_table}"
    metadata_table_id = f"{args.project_id}.{args.dataset}.{args.table}"
    ensure_stock_table(bq, stock_table_id)
    ensure_metadata_table(bq, metadata_table_id)
    rows = build_stock_rows(payload, gcs_uri)
    count = load_to_bigquery(bq, stock_table_id, rows)
    print(f"[완료] BigQuery 주식 시세 적재: {stock_table_id} ({count}건)")
    insert_metadata(bq, metadata_table_id, args.source, gcs_uri, payload)
    print(f"[완료] BigQuery 메타데이터 기록: {metadata_table_id}")


if __name__ == "__main__":
    main()
