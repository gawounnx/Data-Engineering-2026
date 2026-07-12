# Week 1 — API 수집 → GCS 업로드 → BigQuery 적재

외부 API에서 데이터를 수집하고, 품질 검증을 거쳐 GCS(원본 보관)와 BigQuery(구조화 적재)에 저장하는 첫 번째 클라우드 파이프라인을 구현했습니다.

## 전체 파이프라인 흐름
[1일차]
JSONPlaceholder API
│  day1_requests_crawler.py
▼
품질 검증 (data_quality.py)
│  record_count 일치 · 필수 필드 null 검사
▼
data/raw_posts.json
[2일차]
raw_posts.json
│  day2_upload_to_gcp.py
├── ① 품질 검증 ← 실패 시 여기서 중단 (이중 게이트)
├── ② GCS 업로드  → gs://dbai-2026-project/week1/raw_posts.json
├── ③ BigQuery 적재 → data_sample.posts (20건)
└── ④ 실행 이력 기록 → data_sample.ingest_metadata
[FinFlow AI — 개인 프로젝트]
금융위원회 API → raw_stock_prices.json (300건)
한국은행 API  → bok_financial_terms_raw.json (69건)
│  day2_upload_stock_to_gcp.py
├── ① 품질 검증 (validate_stock_payload)
├── ② GCS 업로드  → gs://dbai-2026-project/week1/raw_stock_prices.json
├── ③ BigQuery 적재 → data_sample.stock_prices (300건)
└── ④ 실행 이력 기록 → data_sample.ingest_metadata

## 설계 포인트

**GCS와 BigQuery의 역할 분리**
- GCS: 원본(raw) 보관 = 데이터 레이크. 재처리 기준점이 되고, 장애 시 언제든 다시 처리 가능
- BigQuery: SQL로 조회하는 분석용 저장소 = 데이터 웨어하우스
- 각 레코드에 gcs_uri를 남겨 원본을 역추적할 수 있음

**업로드 전 품질 검증 (이중 게이트)**
- 검증이 GCS 업로드보다 먼저 실행됨
- 불량 데이터가 클라우드에 도달하기 전에 파이프라인 중단
- "조용한 실패(silent failure)" 방지가 핵심 목적

**실행 이력 메타데이터 (ingest_metadata)**
- 언제, 어떤 파일에서, 몇 건이 적재됐는지 기록
- 파일 크기와 MD5 해시까지 저장
- 중복 적재 확인, 장애 시 재처리 판단의 근거
- "파이프라인의 영수증"

## 스크립트 설명

| 파일 | 역할 |
|------|------|
| `day1_requests_crawler.py` | JSONPlaceholder API에서 게시글 20건 수집 |
| `day1_stock_crawler.py` | 금융위원회 API에서 주식 시세 300건 수집 (환경변수 DATA_GO_KR_KEY) |
| `bok_terms_api_crawler.py` | 한국은행 경제금융용어사전 69건 수집 (내부 JSON API 역분석) |
| `data_quality.py` | 품질 검증 함수 모음 (posts용, stock_prices용) |
| `day2_upload_to_gcp.py` | posts 데이터 GCS + BigQuery 적재 |
| `day2_upload_stock_to_gcp.py` | 주식 시세 전용 GCS + BigQuery 적재 |
| `fin_terms_query.py` | 금융용어 임베딩 기반 쿼리 검색 |

## 실행 방법

```bash
# 가상환경 활성화 (중요: conda base와 uv .venv 공존 환경)
source .venv/bin/activate

# 1일차: API 수집
python scripts/day1_requests_crawler.py --limit 20 --output data/raw_posts.json

# GCP 인증
gcloud auth application-default login

# 2일차: GCS + BigQuery 적재
export GCP_PROJECT_ID=melodic-furnace-501500-g4
export GCP_BUCKET=dbai-2026-project

python scripts/day2_upload_to_gcp.py \
  --source data/raw_posts.json \
  --bucket "$GCP_BUCKET" \
  --gcs-object week1/raw_posts.json \
  --project-id "$GCP_PROJECT_ID" \
  --dataset data_sample

# FinFlow AI: 주식 시세 적재
python scripts/day2_upload_stock_to_gcp.py \
  --source data/raw_stock_prices.json \
  --bucket "$GCP_BUCKET" \
  --gcs-object week1/raw_stock_prices.json \
  --project-id "$GCP_PROJECT_ID" \
  --dataset data_sample
```

## BigQuery 검증 SQL

```sql
-- posts 건수 확인
SELECT COUNT(*) AS post_count
FROM `melodic-furnace-501500-g4.data_sample.posts`;

-- 주식 시세 건수 확인
SELECT COUNT(*) AS stock_count
FROM `melodic-furnace-501500-g4.data_sample.stock_prices`;

-- 실행 이력 확인
SELECT ingested_at_utc, source_file, record_count, file_size_bytes
FROM `melodic-furnace-501500-g4.data_sample.ingest_metadata`
ORDER BY ingested_at_utc DESC LIMIT 5;
```

## FinFlow AI 포트폴리오 포인트

- 공공데이터포털(data.go.kr) OpenAPI 연동 — Decoding 키 사용, 페이지네이션 처리
- 한국은행 사이트 내부 JSON API 역분석 — BeautifulSoup 실패 → 개발자도구 Network 탭 분석 → requests 전환
- 데이터 소스별 파이프라인 분리 설계 (posts용 / 주식용)
- 환경변수로 API 키 관리 (코드에 키 직접 X)

## 트러블슈팅 기록

| 증상 | 원인 | 해결 |
|------|------|------|
| `ImportError: cannot import name 'bigquery'` | 패키지는 uv `.venv`에 설치, 실행은 conda base의 Python | `source .venv/bin/activate` 후 실행 또는 `.venv/bin/python` 직접 호출 |
| 버킷 이름이 빈 값으로 전달됨 | `GCP_BUCKET` 환경변수 export 누락 | `export GCP_BUCKET=dbai-2026-project` 후 재실행 |
| `DataQualityError: records[0].id 가 비어 있습니다` | `day2_upload_to_gcp.py`가 posts 전용이라 주식 데이터 필드를 모름 | 주식 전용 `day2_upload_stock_to_gcp.py` 별도 작성 |
| `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` | 공공데이터포털 Encoding 키 사용 | Decoding 키로 교체 |

## 배운 것

- 파이프라인은 "적재했다"가 아니라 "적재를 검증했다"까지가 한 세트
- 원본(raw)을 GCS에 보존해야 어떤 실패도 재처리로 복구 가능
- 적재 이력 메타데이터는 데이터만큼 중요 — 운영·감사의 근거
- conda와 uv 가상환경이 공존하는 환경에서는 Python 실행 경로 확인이 필수
- 데이터 소스마다 스크립트를 분리하는 것이 유지보수에 유리
