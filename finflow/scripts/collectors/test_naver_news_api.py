import os
from typing import Any

import requests
from dotenv import load_dotenv


NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"


def load_api_credentials() -> tuple[str, str]:
    load_dotenv()

    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError(
            "NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 없습니다. "
            ".env 파일을 확인해주세요."
        )

    return client_id, client_secret


def search_news(
    query: str,
    display: int = 5,
    sort: str = "date",
) -> list[dict[str, Any]]:
    client_id, client_secret = load_api_credentials()

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
            timeout=10,
        )
        response.raise_for_status()
    except requests.Timeout as error:
        raise RuntimeError("네이버 뉴스 API 요청 시간이 초과되었습니다.") from error
    except requests.RequestException as error:
        raise RuntimeError(
            f"네이버 뉴스 API 요청에 실패했습니다: {error}"
        ) from error

    data = response.json()
    return data.get("items", [])


def main() -> None:
    query = "금융"

    articles = search_news(
        query=query,
        display=5,
        sort="date",
    )

    print(f"\n검색어: {query}")
    print(f"수집 건수: {len(articles)}건\n")

    for index, article in enumerate(articles, start=1):
        print(f"[{index}] {article.get('title')}")
        print(f"발행일: {article.get('pubDate')}")
        print(f"원문: {article.get('originallink')}")
        print(f"네이버: {article.get('link')}")
        print(f"요약: {article.get('description')}")
        print("-" * 80)


if __name__ == "__main__":
    main()