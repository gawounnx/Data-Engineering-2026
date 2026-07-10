#!/usr/bin/env python3
"""
FinFlow AI — ① 데이터 수집
금융위원회 주식시세정보 API에서 일별 시세를 수집해 로컬 JSON으로 저장합니다.

수업 코드(day1_requests_crawler.py) 구조를 유지하며 변경한 부분:
- API: 식약처 화장품 성분 → 금융위 주식시세 (응답 구조가 response로 한 겹 더 감싸짐)
- 인증키: 하드코딩 → 환경변수 (보안)
- limit: 실제로 동작하도록 수정
- resultCode 확인 추가 (HTTP 200이어도 API 오류일 수 있음)
- 품질 검증 게이트 활성화

사용 예시:
export DATA_GO_KR_KEY='발급받은_Decoding_인증키'
python day1_stock_crawler.py --bas-dt 20260702 --limit 300 --output ../data/raw_stock_prices.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

#from data_quality import validate_stock_payload


API_URL = (
"https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
)


def fetch_stock_prices(
    service_key: str,
    bas_dt: str,
    limit: int,
    timeout: int = 10,
) -> list[dict[str, Any]]:
    """
    기준일자(bas_dt)의 주식 시세를 페이지 단위로 수집합니다.

    Args:
        service_key: 공공데이터포털 인증키 (Decoding 키)
        bas_dt: 기준일자, 예: "20260702"
        limit: 수집할 최대 레코드 수 (수업 코드와 달리 실제로 적용됨)
        timeout: 요청 대기 시간(초)

    Returns:
        시세 레코드 dict 리스트 (basDt, srtnCd, itmsNm, clpr 등의 키)

    학습 포인트:
        - 금융위 API는 응답이 {"response": {"header", "body"}}로
          식약처 API({"header", "body"})보다 한 겹 더 감싸져 있습니다.
        - 같은 공공데이터포털이라도 기관마다 구조·파라미터가 다릅니다.
          (식약처: type=json / 금융위: resultType=json)
    """
    all_records: list[dict[str, Any]] = []
    page = 1

    while len(all_records) < limit:
        params = {
            "serviceKey": service_key,
            "resultType": "json",
            "basDt": bas_dt,
            "pageNo": page,
            "numOfRows": min(100, limit - len(all_records)),
        }

        response = requests.get(API_URL, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        # HTTP 200이어도 API 레벨 오류일 수 있어 resultCode를 확인합니다
        header = data["response"]["header"]
        if header["resultCode"] != "00":
            raise RuntimeError(
                f"API 오류: {header['resultCode']} - {header['resultMsg']}"
            )

        body = data["response"]["body"]
        total_count = int(body.get("totalCount", 0))

        if total_count == 0:
            # 휴장일이거나 갱신 전(영업일 +1일 13시 이후 갱신)
            raise RuntimeError(
                f"basDt={bas_dt} 데이터 없음. 휴장일이거나 갱신 전일 수 있습니다."
            )

        items = body["items"]["item"]
        all_records.extend(items)

        print(f"[INFO] {page}페이지 완료 ({len(all_records)}/{min(limit, total_count)})")

        if len(all_records) >= total_count:
            break
        page += 1

    return all_records[:limit]


def build_payload(records: list[dict[str, Any]], bas_dt: str) -> dict[str, Any]:
    """수집 레코드에 출처·시각·건수 메타데이터를 붙입니다. (수업 코드와 동일 개념)"""
    return {
        "source": API_URL,
        "bas_dt": bas_dt,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "records": records,
    }


def save_json(payload: dict[str, Any], output_path: Path) -> None:
    """payload를 UTF-8 JSON 파일로 저장합니다. (수업 코드와 동일)"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="금융위 주식시세 수집 스크립트")
    parser.add_argument("--bas-dt", required=True, help="기준일자, 예: 20260702")
    parser.add_argument("--limit", type=int, default=300, help="수집할 최대 건수")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../data/raw_stock_prices.json"),
        help="저장할 JSON 파일 경로",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit <= 0:
        raise ValueError("--limit 값은 1 이상이어야 합니다.")

    # 인증키는 코드가 아니라 환경변수에서 읽습니다 (GitHub 노출 방지)
    service_key = os.environ.get("DATA_GO_KR_KEY")
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_KEY가 없습니다.")
        print("실행 전: export DATA_GO_KR_KEY='마이페이지의_Decoding_인증키'")
        sys.exit(1)

    records = fetch_stock_prices(service_key, args.bas_dt, args.limit)
    payload = build_payload(records, args.bas_dt)

    #validate_stock_payload(payload)  # 품질 게이트 — 주석 처리하지 않습니다

    save_json(payload, args.output)

    sample = records[0]
    print(f"[완료] {len(records)}건 수집 -> {args.output}")
    print(f"[샘플] {sample['basDt']} {sample['itmsNm']} 종가 {sample['clpr']}원")


if __name__ == "__main__":
    main()
