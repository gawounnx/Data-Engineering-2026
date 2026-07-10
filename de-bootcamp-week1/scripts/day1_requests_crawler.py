#!/usr/bin/env python3
"""
1일차 실습: requests 기반 API 데이터 수집 예시

기능:
- 공개 API에서 게시글 데이터 수집
- 수집 시각/메타데이터 포함
- 로컬 JSON 파일로 저장

사용 예시:
python day1_requests_crawler.py --limit 30 --output ../data/raw_posts.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from data_quality import validate_posts_payload


API_URL = "https://apis.data.go.kr/1471000/CsmtcsIngdCpntInfoService01/getCsmtcsIngdCpntInfoService01"



def fetch_posts(limit: int, timeout: int = 10) -> list[dict[str, Any]]:



    """
    공개 API에서 게시글 목록을 HTTP GET으로 가져옵니다.

    Args:
        limit: 저장할 최대 게시글 개수 (API가 더 많이 반환해도 앞에서부터 잘라냄)
        timeout: 요청 대기 시간(초). 서버가 응답하지 않으면 TimeoutError 발생

    Returns:
        게시글 dict 리스트. 각 dict는 userId, id, title, body 등의 키를 가짐

    학습 포인트:
        - requests.get(): URL에 GET 요청을 보내고 Response 객체를 반환
        - raise_for_status(): HTTP 4xx/5xx이면 예외를 발생시켜 실패를 조기에 알림
        - response.json(): 응답 본문(JSON 문자열)을 Python dict/list로 파싱
    """

    all_posts = []
    page = 1

    while True:
        params = {
        "serviceKey": "cd865e05369d5fa858ab1e6690ebdb71aafa5bbef414fab46d89697e4a5dae6e",
        "pageNo": page,
        "numOfRows": 100,
        "type": "json",
        }

        response = requests.get(API_URL, params=params, timeout=timeout)
        response.raise_for_status()

        data = response.json()

        body = data["body"]
        posts = body.get("items", [])

        if not posts:
            break

        all_posts.extend(posts)

        total_count = int(body["totalCount"])

        print(f"[INFO] {page}페이지 완료 ({len(all_posts)}/{total_count})")

        if len(all_posts) >= total_count:
            break

        page += 1

    return all_posts
    


def build_payload(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    수집한 게시글에 메타데이터를 붙여 저장용 JSON 구조를 만듭니다.

    Args:
        posts: fetch_posts()에서 반환된 게시글 리스트

    Returns:
        source(수집 URL), fetched_at_utc(수집 시각), record_count(건수),
        records(실제 데이터)를 담은 dict

    학습 포인트:
        - 데이터 파이프라인에서는 원본 데이터(records)와 함께
          '언제, 어디서 수집했는지' 메타데이터를 남기는 것이 좋습니다.
        - datetime.now(timezone.utc).isoformat()은 UTC 기준 ISO 8601 문자열을 만듭니다.
    """
    return {
        "source": API_URL,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_count": len(posts),
        "records": posts,
    }


def save_json(payload: dict[str, Any], output_path: Path) -> None:
    """
    payload dict를 UTF-8 JSON 파일로 디스크에 저장합니다.

    Args:
        payload: build_payload()가 만든 저장용 dict
        output_path: 저장할 파일 경로 (예: ../data/raw_posts.json)

    학습 포인트:
        - Path.parent.mkdir(parents=True): 상위 폴더가 없으면 자동 생성
        - json.dump(): dict를 JSON 형식으로 파일에 기록
        - ensure_ascii=False: 한글 등 비ASCII 문자를 \\uXXXX 이스케이프 없이 저장
        - indent=2: 사람이 읽기 쉽도록 들여쓰기 적용 (용량은 약간 커짐)
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    """
    터미널에서 넘긴 커맨드라인 인자(--limit, --output)를 파싱합니다.

    Returns:
        args.limit, args.output 등으로 접근 가능한 Namespace 객체

    학습 포인트:
        - argparse는 스크립트 실행 시 옵션을 받을 때 표준적으로 사용합니다.
        - 예: python day1_requests_crawler.py --limit 30 --output ../data/raw_posts.json
    """
    parser = argparse.ArgumentParser(description="requests 기반 API 수집 스크립트")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="가져올 레코드 수 (기본값: 20)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../data/raw_posts.json"),
        help="저장할 JSON 파일 경로 (기본값: ../data/raw_posts.json)",
    )
    return parser.parse_args()


def main() -> None:
    """
    스크립트의 진입점. 수집 → 메타데이터 추가 → 저장 순서로 실행합니다.

    흐름:
        1. parse_args()로 CLI 옵션 읽기
        2. fetch_posts()로 API 데이터 수집
        3. build_payload()로 메타데이터 포함 구조 생성
        4. save_json()으로 로컬 파일 저장
    """
    args = parse_args()
    if args.limit <= 0:
        raise ValueError("--limit 값은 1 이상이어야 합니다.")

    posts = fetch_posts(limit=args.limit)
    payload = build_payload(posts)
    #validate_posts_payload(payload, min_records=1)
    save_json(payload, args.output)

    print(f"[완료] {len(posts)}건 수집 -> {args.output}")


if __name__ == "__main__":
    main()
 