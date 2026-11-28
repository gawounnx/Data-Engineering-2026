"""고정 색인과 저장된 검색어로 뉴스 검색 순위를 오프라인에서 재생한다."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from datetime import datetime
from hashlib import file_digest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src import news_retriever as news

from .evaluate import DEFAULT_CASES, load_cases


RECORD_PATH = Path(__file__).with_name("evaluation_record.json")


def verify_hash(path: Path, expected: str) -> None:
    with path.open("rb") as file:
        digest = file_digest(file, "sha256").hexdigest()
    if digest != expected:
        raise ValueError(f"평가 기록의 SHA-256과 일치하지 않습니다 ({path})")


def replay_query(question: str, query: str, as_of: datetime) -> list[dict]:
    class ReplayClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of.astimezone(tz) if tz is not None else as_of.replace(tzinfo=None)

    with patch.object(news, "datetime", ReplayClock):
        articles = news.search_financial_news(
            query=query,
            news_path=news.CHROMA_PATH.parent / "offline-replay-no-raw-news.json",
            original_query=question,
            prefer_recent=news.has_recency_intent(question) or news.has_recency_intent(query),
        )
    return [
        {
            "rank": rank,
            "title": article["title"],
            "published_at": article["published_at"],
            "original_url": article["original_url"],
            "retrieval_score": article["retrieval_score"],
        }
        for rank, article in enumerate(articles, start=1)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chroma", type=Path, default=news.CHROMA_PATH, help="기록과 일치하는 ChromaDB 경로")
    args = parser.parse_args()

    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    try:
        verify_hash(DEFAULT_CASES, record["cases_sha256"])
        questions = {case["id"]: case["question"] for case in load_cases(DEFAULT_CASES)}
        with TemporaryDirectory(prefix="finflow-news-replay-") as temp:
            copied = shutil.copytree(args.chroma, Path(temp) / "chroma")
            for name, expected in record["snapshot_files_sha256"].items():
                verify_hash(copied / name, expected)
            with (
                patch.object(news, "CHROMA_PATH", copied),
                patch.dict(os.environ, {"NEWS_RETENTION_DAYS": str(news.DEFAULT_NEWS_RETENTION_DAYS)}),
                redirect_stdout(sys.stderr),
            ):
                cases = [
                    {
                        "id": row["id"],
                        "as_of": row["as_of"],
                        "news_sources": replay_query(
                            questions[row["id"]], row["news_query"], datetime.fromisoformat(row["as_of"]),
                        ),
                    }
                    for row in record["news_replay"]
                ]
            news.load_news_articles.cache_clear()
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({"data_generation": record["snapshot_generation"], "cases": cases}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
