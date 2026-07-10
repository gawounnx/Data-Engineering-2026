#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import reqauests


SEARCH_URL = "https://www.bok.or.kr/portal/ecEdu/ecWordDicary/searchWord.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.bok.or.kr/portal/ecEdu/ecWordDicary/search.do?menuNo=200688",
    "X-Requested-With": "XMLHttpRequest",
}

TERMS = [
    "기준금리",
    "물가상승률",
    "환율",
    "금리",
    "채권",
    "주식",
    "ETF",
    "인플레이션",
    "디플레이션",
    "통화정책",
    "국채",
    "회사채",
    "경기",
    "GDP",
    "소비자물가지수",
]


def search_term(term: str, timeout: int = 10) -> list[dict[str, Any]]:
    params = {
        "collection": "dic",
        "query": term,
        "initYn": "N",
    }

    response = requests.get(
        SEARCH_URL,
        params=params,
        headers=HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    return data.get("wordList", [])


def collect_terms() -> list[dict[str, Any]]:
    records = []

    for term in TERMS:
        print(f"[INFO] 검색 중: {term}")

        word_list = search_term(term)

        if not word_list:
            print(f"[WARN] 검색 결과 없음: {term}")
            continue

        for item in word_list:
            records.append(
                {
                    "search_keyword": term,
                    "term": item.get("ecWordNm"),
                    "description": item.get("ecWordCn"),
                    "ec_word_sn": item.get("ecWordSn"),
                    "source": "한국은행 경제금융용어사전",
                    "source_url": "https://www.bok.or.kr/portal/ecEdu/ecWordDicary/search.do?menuNo=200688",
                }
            )

        time.sleep(0.5)

    return records


def save_json(records: list[dict[str, Any]], output_path: Path) -> None:
    payload = {
        "source": "한국은행 경제금융용어사전",
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "records": records,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    records = collect_terms()
    output_path = Path("data/bok_financial_terms_raw.json")
    save_json(records, output_path)

    print(f"[완료] {len(records)}건 저장 -> {output_path}")


if __name__ == "__main__":
    main()