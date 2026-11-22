"""[4/7] 정제된 금융용어 JSON을 BigQuery에 적재한다.

입력: data/terms/processed/financial_terms_800_v4_cleaned.json
출력: {GCP_PROJECT_ID}.{BQ_DATASET_ID}.{TERMS_BQ_TABLE(기본 financial_terms)}

주의: 실제 적재는 WRITE_TRUNCATE로 **테이블 전체를 덮어쓴다.**
기본 실행은 dry-run이다. BigQuery에 접속하지 않고 대상과 건수만 출력한다.
실제로 쓰려면 대상 테이블 ID를 그대로 다시 입력해야 한다.

    python scripts/terms/load_terms_to_bigquery.py                 # dry-run
    python scripts/terms/load_terms_to_bigquery.py --execute \
        --confirm-table <project>.<dataset>.<table>                 # 덮어쓰기
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery

import terms_config


INPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_800_v4_cleaned.json"
)
WRITE_DISPOSITION = bigquery.WriteDisposition.WRITE_TRUNCATE


def load_json(path: Path) -> list[dict[str, Any]]:
    """JSON 파일에서 금융용어 목록을 읽는다."""
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in (
            "terms",
            "financial_terms",
            "items",
            "records",
            "data",
            "results",
        ):
            value = payload.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):
                for nested_value in value.values():
                    if isinstance(nested_value, list):
                        return nested_value

    raise ValueError("JSON에서 금융용어 목록을 찾지 못했습니다.")


def get_first_value(
    record: dict[str, Any],
    keys: tuple[str, ...],
) -> Any:
    """후보 키 중 값이 존재하는 첫 번째 필드를 반환한다."""
    for key in keys:
        value = record.get(key)

        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()

        if value not in ("", None):
            return value

    return None


def create_hash(term: str, definition: str) -> str:
    """용어와 정의를 이용해 중복 확인용 해시를 생성한다."""
    text = f"{term}\n{definition}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_record(
    record: dict[str, Any],
    index: int,
    loaded_at: str,
) -> dict[str, Any]:
    """원본 레코드를 BigQuery 스키마에 맞게 변환한다."""
    term_id = get_first_value(
        record,
        ("term_id", "id", "용어ID"),
    )

    term = get_first_value(
        record,
        ("term", "name", "title", "용어", "금융용어"),
    )

    definition = get_first_value(
        record,
        (
            "definition",
            "description",
            "content",
            "text",
            "설명",
            "정의",
            "본문",
        ),
    )

    if not term:
        raise ValueError(f"{index}번째 레코드에 용어명이 없습니다.")

    if not definition:
        raise ValueError(f"{term}의 정의가 없습니다.")

    return {
        "term_id": str(term_id or f"bok_term_{index:04d}"),
        "term": str(term).strip(),
        "definition": str(definition).strip(),
        "source": "한국은행",
        "source_document": "경제금융용어",
        "data_version": "v4",
        "content_hash": create_hash(
            str(term).strip(),
            str(definition).strip(),
        ),
        "loaded_at": loaded_at,
    }


def normalize_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """전체 레코드를 변환하고 중복 용어를 제거한다."""
    loaded_at = datetime.now(timezone.utc).isoformat()
    normalized: list[dict[str, Any]] = []
    seen_terms: set[str] = set()

    for index, record in enumerate(records, start=1):
        item = normalize_record(record, index, loaded_at)
        duplicate_key = item["term"].lower()

        if duplicate_key in seen_terms:
            continue

        seen_terms.add(duplicate_key)
        normalized.append(item)

    return normalized


def build_schema() -> list[bigquery.SchemaField]:
    """BigQuery 테이블 스키마를 정의한다."""
    return [
        bigquery.SchemaField(
            "term_id",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "term",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "definition",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "source",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "source_document",
            "STRING",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "data_version",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "content_hash",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "loaded_at",
            "TIMESTAMP",
            mode="REQUIRED",
        ),
    ]


def load_to_bigquery(
    records: list[dict[str, Any]],
    table_id: str,
    location: str,
) -> int:
    """금융용어 목록으로 BigQuery 테이블을 덮어쓴다(WRITE_TRUNCATE)."""
    project_id = table_id.split(".", 1)[0]
    client = bigquery.Client(project=project_id)

    job_config = bigquery.LoadJobConfig(
        schema=build_schema(),
        write_disposition=WRITE_DISPOSITION,
    )

    load_job = client.load_table_from_json(
        records,
        table_id,
        job_config=job_config,
        location=location,
    )

    load_job.result()

    table = client.get_table(table_id)
    return table.num_rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="정제된 금융용어를 BigQuery에 적재한다 (기본: dry-run)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제로 BigQuery 테이블을 덮어쓴다(WRITE_TRUNCATE).",
    )
    parser.add_argument(
        "--confirm-table",
        default="",
        help="--execute와 함께 대상 테이블 ID(project.dataset.table)를 그대로 입력한다.",
    )
    return parser.parse_args(argv)


def check_write_confirmation(
    table_id: str,
    execute: bool,
    confirm_table: str,
) -> None:
    """덮어쓰기 대상이 명시적으로 확인됐는지 검사한다."""
    if not execute:
        return

    if confirm_table.strip() != table_id:
        raise ValueError(
            "덮어쓸 테이블을 확인할 수 없습니다. "
            f"--confirm-table {table_id} 를 그대로 입력하세요. "
            f"(입력값: {confirm_table.strip() or '없음'})"
        )


def main(argv: list[str] | None = None) -> None:
    """금융용어 BigQuery 적재를 실행한다. 기본은 dry-run이다."""
    args = parse_args(argv)
    load_dotenv()

    try:
        table_id = terms_config.bigquery_table_id()
        location = terms_config.bigquery_location()
        check_write_confirmation(
            table_id=table_id,
            execute=args.execute,
            confirm_table=args.confirm_table,
        )

        source_records = load_json(INPUT_PATH)
        normalized_records = normalize_records(source_records)

        print(
            "[정보] 금융용어 BigQuery 적재 "
            + ("시작" if args.execute else "dry-run (BigQuery에 접속하지 않음)")
        )
        print(f"- 입력 파일: {terms_config.display_path(INPUT_PATH)}")
        print(f"- 원본 건수: {len(source_records):,}")
        print(f"- 적재 건수: {len(normalized_records):,}")
        print(f"- 대상 테이블: {table_id} (위치: {location})")
        print("- 쓰기 방식: WRITE_TRUNCATE (기존 테이블 내용을 모두 덮어씀)")

        if not args.execute:
            print(
                "\n[dry-run] 실제 적재는 다음 옵션으로 실행합니다: "
                f"--execute --confirm-table {table_id}"
            )
            return

        row_count = load_to_bigquery(
            records=normalized_records,
            table_id=table_id,
            location=location,
        )

        print("\n[완료] BigQuery 적재 성공")
        print(f"- 최종 테이블 행 수: {row_count:,}")

    except FileNotFoundError as exc:
        print(f"[파일 오류] {exc}")
        raise SystemExit(1) from exc

    except (ValueError, json.JSONDecodeError) as exc:
        print(f"[데이터 오류] {exc}")
        raise SystemExit(1) from exc

    except GoogleAPIError as exc:
        print(f"[BigQuery 오류] {exc}")
        raise SystemExit(1) from exc

    except Exception as exc:
        print(f"[예상하지 못한 오류] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
