from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv


NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "did",
    "sid",
}

DEFAULT_QUERIES = [
    "경제",
    "금융",
    "증권",
    "주식",
    "채권",
    "환율",
    "금리",
    "은행",
    "보험",
    "기업 실적",
]

CATEGORY_KEYWORDS = {
    "주식시장": ["주식", "증시", "코스피", "코스닥", "상장", "주가"],
    "채권": ["채권", "국채", "회사채", "금리채"],
    "외환": ["환율", "원달러", "달러", "외환", "엔화"],
    "통화정책": ["기준금리", "금리 인하", "금리 인상", "한국은행", "통화정책"],
    "은행": ["은행", "가계대출", "주택담보대출", "예금", "대출"],
    "보험": ["보험", "손해보험", "생명보험"],
    "기업": ["실적", "영업이익", "매출", "공시", "인수합병"],
    "가상자산": ["가상자산", "암호화폐", "비트코인", "스테이블코인", "블록체인"],
}

FINANCIAL_RELEVANCE_KEYWORDS = [
    "금융",
    "증권",
    "주식",
    "주가",
    "증시",
    "코스피",
    "코스닥",
    "채권",
    "국채",
    "회사채",
    "환율",
    "외환",
    "달러",
    "엔화",
    "금리",
    "기준금리",
    "은행",
    "예금",
    "대출",
    "가계대출",
    "주택담보대출",
    "보험",
    "실적",
    "매출",
    "영업이익",
    "순이익",
    "공시",
    "상장",
    "투자",
    "배당",
    "자산",
    "부채",
    "가상자산",
    "암호화폐",
    "비트코인",
    "스테이블코인",
    "핀테크",
    "통화정책",
    "한국은행",
    "금융위원회",
    "금융감독원",
]


def load_api_credentials() -> tuple[str, str]:
    """환경변수에서 네이버 API 인증정보를 불러옵니다."""
    load_dotenv()

    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError(
            "NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 없습니다. "
            ".env 파일을 확인해주세요."
        )

    return client_id, client_secret


def clean_html_text(value: str | None) -> str:
    """HTML 태그와 HTML 엔티티를 제거합니다."""
    if not value:
        return ""

    decoded = html.unescape(value)
    without_tags = re.sub(r"<[^>]+>", "", decoded)
    normalized = re.sub(r"\s+", " ", without_tags)

    return normalized.strip()


def normalize_url(url: str | None) -> str:
    """기사 식별에 필요한 쿼리는 유지하고 추적용 값만 제거합니다."""
    if not url:
        return ""

    parts = urlsplit(url)

    filtered_query = [
        (key, value)
        for key, value in parse_qsl(
            parts.query,
            keep_blank_values=True,
        )
        if key.lower() not in TRACKING_QUERY_KEYS
    ]

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(filtered_query),
            "",
        )
    )


def create_article_id(url: str, title: str) -> str:
    """URL을 우선 사용하고, URL이 없으면 제목으로 고유 ID를 생성합니다."""
    source = url or title
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def parse_published_at(pub_date: str | None) -> str | None:
    """네이버 뉴스 날짜를 ISO 8601 형식으로 변환합니다."""
    if not pub_date:
        return None

    try:
        parsed = parsedate_to_datetime(pub_date)
        return parsed.isoformat()
    except (TypeError, ValueError):
        return None


def classify_article(title: str, summary: str) -> str:
    """제목과 요약을 바탕으로 간단한 금융 카테고리를 분류합니다."""
    text = f"{title} {summary}".lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            return category

    return "기타경제"


def calculate_financial_relevance(
    title: str,
    summary: str,
) -> int:
    """제목과 요약에 포함된 금융 관련 키워드 수를 계산합니다."""
    text = f"{title} {summary}".lower()

    return sum(
        1
        for keyword in FINANCIAL_RELEVANCE_KEYWORDS
        if keyword.lower() in text
    )


def is_financial_article(
    title: str,
    summary: str,
    minimum_score: int = 1,
) -> bool:
    """금융 관련 키워드가 일정 수 이상 포함됐는지 검사합니다."""
    score = calculate_financial_relevance(
        title=title,
        summary=summary,
    )

    return score >= minimum_score


def request_news(
    query: str,
    display: int,
    sort: str,
    client_id: str,
    client_secret: str,
) -> list[dict[str, Any]]:
    """네이버 뉴스 검색 API를 호출합니다."""
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }

    params = {
        "query": query,
        "display": display,
        "sort": sort,
    }

    try:
        response = requests.get(
            NAVER_NEWS_API_URL,
            headers=headers,
            params=params,
            timeout=15,
        )
        response.raise_for_status()
    except requests.Timeout as error:
        raise RuntimeError(
            f"검색어 '{query}' 요청 시간이 초과되었습니다."
        ) from error
    except requests.RequestException as error:
        raise RuntimeError(
            f"검색어 '{query}' API 요청에 실패했습니다: {error}"
        ) from error

    payload = response.json()
    return payload.get("items", [])


def transform_article(
    raw_article: dict[str, Any],
    query: str,
    collected_at: str,
) -> dict[str, Any]:
    """네이버 API 응답을 FinFlow 뉴스 스키마로 변환합니다."""
    title = clean_html_text(raw_article.get("title"))
    summary = clean_html_text(raw_article.get("description"))

    original_url = normalize_url(raw_article.get("originallink"))
    naver_url = normalize_url(raw_article.get("link"))

    canonical_url = original_url or naver_url
    article_id = create_article_id(canonical_url, title)

    financial_relevance_score = calculate_financial_relevance(
        title=title,
        summary=summary,
    )

    return {
        "article_id": article_id,
        "title": title,
        "summary": summary,
        "published_at": parse_published_at(raw_article.get("pubDate")),
        "original_url": original_url,
        "naver_url": naver_url,
        "category": classify_article(title, summary),
        "financial_relevance_score": financial_relevance_score,
        "search_queries": [query],
        "collected_at": collected_at,
        "source_type": "naver_news_search_api",
        "embedding_status": False,
    }


def merge_duplicate_article(
    existing: dict[str, Any],
    new_article: dict[str, Any],
) -> None:
    """동일 기사가 여러 검색어에 검색되면 검색어 목록만 합칩니다."""
    existing_queries = set(existing.get("search_queries", []))
    new_queries = set(new_article.get("search_queries", []))

    existing["search_queries"] = sorted(existing_queries | new_queries)


def collect_news(
    queries: list[str],
    display: int,
    sort: str,
) -> list[dict[str, Any]]:
    """여러 검색어로 뉴스 데이터를 수집하고 중복을 제거합니다."""
    client_id, client_secret = load_api_credentials()
    collected_at = datetime.now().astimezone().isoformat()

    articles_by_id: dict[str, dict[str, Any]] = {}

    for query in queries:
        print(f"[수집 시작] 검색어: {query}")

        try:
            raw_articles = request_news(
                query=query,
                display=display,
                sort=sort,
                client_id=client_id,
                client_secret=client_secret,
            )
        except RuntimeError as error:
            print(f"[수집 실패] {error}")
            continue

        print(f"[API 결과] {len(raw_articles)}건")

        for raw_article in raw_articles:
            article = transform_article(
                raw_article=raw_article,
                query=query,
                collected_at=collected_at,
            )

            if article["financial_relevance_score"] < 1:
                continue

            article_id = article["article_id"]

            if article_id in articles_by_id:
                merge_duplicate_article(
                    existing=articles_by_id[article_id],
                    new_article=article,
                )
            else:
                articles_by_id[article_id] = article

    articles = list(articles_by_id.values())

    articles.sort(
        key=lambda article: article.get("published_at") or "",
        reverse=True,
    )

    return articles


def save_json(
    articles: list[dict[str, Any]],
    output_path: Path,
    queries: list[str],
) -> None:
    """수집 결과와 메타데이터를 JSON 파일로 저장합니다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "metadata": {
            "source": "Naver News Search API",
            "query_count": len(queries),
            "queries": queries,
            "record_count": len(articles),
            "generated_at": datetime.now().astimezone().isoformat(),
        },
        "articles": articles,
    }

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="네이버 뉴스 검색 API 기반 금융뉴스 수집기"
    )

    parser.add_argument(
        "--queries",
        nargs="+",
        default=DEFAULT_QUERIES,
        help="수집할 뉴스 검색어 목록",
    )

    parser.add_argument(
        "--display",
        type=int,
        default=20,
        help="검색어 하나당 수집할 기사 수",
    )

    parser.add_argument(
        "--sort",
        choices=["date", "sim"],
        default="date",
        help="date는 최신순, sim은 관련도순",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/naver_financial_news.json"),
        help="JSON 출력 파일 경로",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if not 1 <= args.display <= 100:
        raise ValueError("--display는 1부터 100 사이여야 합니다.")

    articles = collect_news(
        queries=args.queries,
        display=args.display,
        sort=args.sort,
    )

    save_json(
        articles=articles,
        output_path=args.output,
        queries=args.queries,
    )

    print()
    print("[수집 완료]")
    print(f"- 검색어 수: {len(args.queries)}개")
    print(f"- 중복 제거 후 기사 수: {len(articles)}건")
    print(f"- 저장 위치: {args.output.resolve()}")

    category_counts: dict[str, int] = {}

    for article in articles:
        category = article["category"]
        category_counts[category] = category_counts.get(category, 0) + 1

    print("- 카테고리별 건수")

    for category, count in sorted(category_counts.items()):
        print(f"  · {category}: {count}건")


if __name__ == "__main__":
    main()
