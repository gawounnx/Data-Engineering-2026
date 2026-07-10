#!/usr/bin/env python3
"""
1주차 데이터 품질 검증 유틸리티

파이프라인 단계마다 최소한의 검증을 수행해
'조용한 실패'를 줄이는 것이 목표입니다.
"""

from __future__ import annotations

from typing import Any


class DataQualityError(Exception):
    """데이터 품질 검증 실패."""


def validate_posts_payload(payload: dict[str, Any], *, min_records: int = 1) -> None:
    """
    API 수집 결과(JSON) 품질 검증.

    검증 항목:
    - records 리스트 존재 및 최소 건수
    - record_count 메타데이터와 실제 건수 일치
    - 필수 필드(id, title, body) null/빈값 여부
    """
    records = payload.get("records")
    if not isinstance(records, list):
        raise DataQualityError("records 필드가 리스트가 아닙니다.")

    if len(records) < min_records:
        raise DataQualityError(
            f"레코드 수 부족: {len(records)}건 (최소 {min_records}건 필요)"
        )

    declared_count = payload.get("record_count")
    if declared_count is not None and declared_count != len(records):
        raise DataQualityError(
            f"record_count 불일치: 메타데이터={declared_count}, 실제={len(records)}"
        )

    required_fields = ("id", "title", "body")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise DataQualityError(f"records[{index}]가 객체(dict)가 아닙니다.")
        for field in required_fields:
            value = record.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise DataQualityError(
                    f"records[{index}].{field} 가 비어 있습니다 (null/빈 문자열)."
                )

    print(
        f"[품질 검증 통과] posts — {len(records)}건, "
        f"필수 필드({', '.join(required_fields)}) 이상 없음"
    )
