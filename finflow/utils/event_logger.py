import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests


LOG_COLLECTOR_URL = os.getenv(
    "LOG_COLLECTOR_URL",
    "http://127.0.0.1:8080",
)


def send_event(
    event_type: str,
    session_id: str,
    page: str = "finflow",
    properties: dict[str, Any] | None = None,
) -> bool:
    """FinFlow 사용자 행동 이벤트를 로그 수집 서버로 전송한다."""

    payload = {
        "event_type": event_type,
        "session_id": session_id,
        "page": page,
        "properties": properties or {},
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        response = requests.post(
            f"{LOG_COLLECTOR_URL}/events",
            json=payload,
            timeout=2,
        )

        if not response.ok:
            print(f"[로그 전송 실패] 상태 코드: {response.status_code}")
            print(f"[전송 payload] {payload}")
            print(f"[API 응답] {response.text}")
            return False

        print(f"[로그 전송 성공] {response.status_code}")
        return True

    except requests.RequestException as error:
        print(f"[로그 전송 오류] {error}")
        return False