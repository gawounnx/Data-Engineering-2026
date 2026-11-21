"""정제된 네이버 금융뉴스를 BigQuery에 적재한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.cloud import bigquery


PROJECT_ID = "melodic-furnace-501500-g4"
DATASET_ID = "finflow"
TABLE_ID = "financial_news"

INPUT_PATH = Path(
    "data/raw/naver_financial_news.json"
)


def load_articles(path: Path) -> list[dict[str, Any]]:
    """뉴스 JSON 파일에서 articles 목록을 불러온다."""
    if not path.exists():
        raise FileNotFoundError(
            f"뉴스 파일을 찾을 수 없습니다: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    articles = data.get("articles")

    if not isinstance(articles, list):
        raise ValueError(
            "JSON 파일에 articles 리스트가 없습니다."
        )

    return [
        article
        for article in articles
        if isinstance(article, dict)
    ]


def build_schema() -> list[bigquery.SchemaField]:
    """financial_news 테이블 스키마를 반환한다."""
    return [
        bigquery.SchemaField(
            "article_id",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "title",
            "STRING",
            mode="REQUIRED",
        ),
        bigquery.SchemaField(
            "summary",
            "STRING",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "published_at",
            "TIMESTAMP",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "original_url",
            "STRING",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "naver_url",
            "STRING",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "category",
            "STRING",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "financial_relevance_score",
            "INTEGER",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "search_queries",
            "STRING",
            mode="REPEATED",
        ),
        bigquery.SchemaField(
            "collected_at",
            "TIMESTAMP",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "source_type",
            "STRING",
            mode="NULLABLE",
        ),
        bigquery.SchemaField(
            "embedding_status",
            "BOOLEAN",
            mode="NULLABLE",
        ),
    ]


def create_table_if_not_exists(
    client: bigquery.Client,
) -> str:
    """BigQuery 테이블이 없으면 생성한다."""
    table_ref = (
        f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
    )

    try:
        client.get_table(table_ref)
        print(f"[정보] 기존 테이블 사용: {table_ref}")
    except Exception:
        table = bigquery.Table(
            table_ref,
            schema=build_schema(),
        )

        table.time_partitioning = (
            bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="published_at",
            )
        )

        table.clustering_fields = [
            "category",
            "article_id",
        ]

        client.create_table(table)

        print(f"[정보] 테이블 생성: {table_ref}")

    return table_ref


def fetch_existing_ids(
    client: bigquery.Client,
    article_ids: list[str],
) -> set[str]:
    """BigQuery에 이미 존재하는 article_id를 조회한다."""
    if not article_ids:
        return set()

    table_ref = (
        f"`{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
    )

    query = f"""
    SELECT article_id
    FROM {table_ref}
    WHERE article_id IN UNNEST(@article_ids)
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter(
                "article_ids",
                "STRING",
                article_ids,
            )
        ]
    )

    rows = client.query(
        query,
        job_config=job_config,
    ).result()

    return {
        row.article_id
        for row in rows
    }


def insert_new_articles(
    client: bigquery.Client,
    table_ref: str,
    articles: list[dict[str, Any]],
) -> int:
    """기존 기사와 중복되지 않는 뉴스만 적재한다."""
    article_ids = [
        str(article.get("article_id", "")).strip()
        for article in articles
        if article.get("article_id")
    ]

    existing_ids = fetch_existing_ids(
        client=client,
        article_ids=article_ids,
    )

    new_articles = [
        article
        for article in articles
        if article.get("article_id") not in existing_ids
    ]

    if not new_articles:
        print("[정보] 새로 적재할 뉴스가 없습니다.")
        return 0

    job_config = bigquery.LoadJobConfig(
        schema=build_schema(),
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    load_job = client.load_table_from_json(
        new_articles,
        table_ref,
        job_config=job_config,
    )

    load_job.result()

    if load_job.errors:
        raise RuntimeError(
            f"BigQuery 적재 오류: {load_job.errors}"
        )

    return len(new_articles)


def main() -> None:
    """뉴스 데이터를 BigQuery에 중복 없이 적재한다."""
    articles = load_articles(INPUT_PATH)

    client = bigquery.Client(
        project=PROJECT_ID,
    )

    table_ref = create_table_if_not_exists(
        client=client,
    )

    inserted_count = insert_new_articles(
        client=client,
        table_ref=table_ref,
        articles=articles,
    )

    print("\n[BigQuery 뉴스 적재 완료]")
    print(f"- 입력 기사: {len(articles)}건")
    print(f"- 신규 적재: {inserted_count}건")
    print(f"- 테이블: {table_ref}")


if __name__ == "__main__":
    main()