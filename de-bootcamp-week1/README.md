# Week 1 — API 수집 → GCS 업로드 → BigQuery 적재

외부 API에서 데이터를 수집하고, 품질 검증을 거쳐 GCS(원본 보관)와
BigQuery(구조화 적재 + 실행 이력 기록)에 적재하는 파이프라인을 구현했습니다.

## 실행 방법

    source .venv/bin/activate
    python scripts/day1_requests_crawler.py --limit 20 --output data/raw_posts.json

    export GCP_PROJECT_ID=<your-project-id>
    export GCP_BUCKET=<your-bucket>
    python scripts/day2_upload_to_gcp.py \
      --source data/raw_posts.json \
      --bucket "$GCP_BUCKET" \
      --gcs-object week1/raw_posts.json \
      --project-id "$GCP_PROJECT_ID" \
      --dataset <your-dataset>

## 트러블슈팅 기록

| 증상 | 원인 | 해결 |
|------|------|------|
| ImportError (bigquery) | 패키지는 uv .venv에 설치, 실행은 conda base의 Python | source .venv/bin/activate 후 실행 |
| 버킷 이름 빈 값 | GCP_BUCKET 환경 변수 export 누락 | export 후 재실행 |

## 배운 것

- 파이프라인은 "적재했다"가 아니라 "적재를 검증했다"까지가 한 세트
- 원본(raw)을 GCS에 보존해야 어떤 실패도 재처리로 복구 가능
- 적재 이력 메타데이터는 데이터만큼 중요 (운영·감사의 근거)
