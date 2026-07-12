# DE Bootcamp 2026 — AI 데이터 파이프라인 & RAG 서비스

> 데이터 수집부터 RAG 서비스 배포까지, 5주간의 데이터 엔지니어링 부트캠프(BDAI 1기) 학습 기록입니다.
> 부트캠프 실습과 병행하여 **금융 도메인 개인 프로젝트(FinFlow AI)** 를 함께 구현합니다.

## FinFlow AI란?

대학생·사회초년생을 위한 **한국 공공 금융 데이터 기반 AI 리서치 어시스턴트**입니다.

- 금융위원회 OpenAPI → 주식 시세 수집
- 한국은행 경제금융용어사전 → 금융용어 수집 및 임베딩
- RAG 기반 검색으로 금융 질문에 답변 생성 (4주차~)
- Streamlit 대시보드로 시각화 (4주차~)
- Cloud Run으로 공개 배포 (5주차~)

## 전체 파이프라인 (10단계)

```
1. 데이터 수집      → API/스크래핑으로 원천 데이터 확보
2. 데이터 검증      → 필수 필드, record_count 일치 여부 확인 (이중 게이트)
3. 전처리           → 텍스트 정제, 청킹 (chunk size가 RAG 품질 결정)
4. BigQuery 적재    → 구조화 데이터 DW 저장 + ingest_metadata 이력 기록
5. 텍스트 임베딩    → SentenceTransformer로 벡터화 (dim=128)
6. ChromaDB 저장    → 벡터 DB에 임베딩 적재
7. Airflow 자동화   → DAG으로 전체 파이프라인 스케줄링
8. RAG 검색         → 쿼리 → 유사 청크 검색 → Gemini로 답변 생성
9. Streamlit 대시보드 → 금융 데이터 시각화 + 검색 UI
10. Cloud Run 배포  → 공개 접근 가능한 서비스로 배포
```

## 주차별 진행 상황

| 주차 | 주제 | 상태 | 폴더 |
|------|------|------|------|
| 1주차 | API 수집 → GCS 업로드 → BigQuery 적재 | ✅ 완료 | [de-bootcamp-week1/](./de-bootcamp-week1) |
| 2주차 | 데이터 전처리 · 청킹 · 임베딩 | ✅ 완료 | [de-bootcamp-week2/](./de-bootcamp-week2) |
| 3주차 | ChromaDB 적재 · Airflow DAG 자동화 | ⏳ 예정 | - |
| 4주차 | RAG 검색 + Gemini · Streamlit 대시보드 | ⏳ 예정 | - |
| 5주차 | Cloud Run 배포 · 최종 프로젝트 | ⏳ 예정 | - |

## 주차별 핵심 성과

### 1주차
- JSONPlaceholder API 수집 → GCS → BigQuery `posts` 테이블 적재 (부트캠프 실습)
- 금융위원회 OpenAPI → 주식 시세 **300건** 수집 → BigQuery `stock_prices` 적재 (FinFlow AI)
- 한국은행 경제금융용어사전 **69건** 수집 (내부 JSON API 역분석)
- `ingest_metadata` 테이블로 실행 이력 추적 ("파이프라인의 영수증")
- 데이터 소스별 파이프라인 분리 설계 (`day2_upload_to_gcp.py` / `day2_upload_stock_to_gcp.py`)

### 2주차
- HuggingFace 페르소나 데이터 20건 → 청킹 **220청크** → 임베딩 (부트캠프 실습)
- 금융용어 69건 → **292청크** (150자 단위) → 임베딩 (FinFlow AI)
- 청킹 크기 개선 실험: 용어 1개 = 1청크 → ETF 검색 실패 / 150자 단위 → ETF 검색 성공
- 코사인 유사도 기반 쿼리 스크립트 구현 (`fin_terms_query.py`)

## GCP 환경

| 항목 | 값 |
|------|------|
| 프로젝트 ID | `melodic-furnace-501500-g4` |
| GCS 버킷 | `dbai-2026-project` |
| BigQuery 데이터셋 | `data_sample` |
| 리전 | asia-northeast3 |

## 기술 스택

| 분류 | 기술 |
|------|------|
| 언어/환경 | Python 3.13, uv, macOS Apple Silicon |
| 클라우드 | GCP (Cloud Storage, BigQuery, Cloud Run) |
| 데이터 수집 | requests, 금융위원회 OpenAPI, 한국은행 API |
| AI/임베딩 | sentence-transformers (kekeappa/kor-static-embedding-128, dim=128) |
| 벡터 DB | ChromaDB (3주차~) |
| 오케스트레이션 | Airflow (3주차~) |
| 프론트엔드 | Streamlit (4주차~) |
| LLM | Gemini API (4주차~) |

## 실행 공통 준비

```bash
# GCP 인증 (최초 1회)
gcloud auth application-default login
gcloud config set project melodic-furnace-501500-g4

# 가상환경 (각 주차 폴더에서)
uv venv
source .venv/bin/activate
uv pip install -r scripts/requirements.txt
```

## 주요 트러블슈팅

| 증상 | 해결 |
|------|------|
| `ImportError: bigquery` | conda base와 uv venv 공존 → `.venv/bin/python` 직접 호출 |
| 공공데이터포털 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` | Encoding 키 → Decoding 키로 교체 |
| ETF 검색 시 엉뚱한 결과 | 청크 단위 축소 (1용어 1청크 → 150자 단위) |
