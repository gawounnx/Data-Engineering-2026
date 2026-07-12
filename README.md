# DE Bootcamp 2026 — AI 데이터 파이프라인 & RAG 서비스

> 데이터 수집부터 RAG 서비스 배포까지, 5주간의 데이터 엔지니어링 부트캠프(BDAI 1기) 학습 기록입니다.
> 부트캠프 실습과 병행하여 **금융 도메인 개인 프로젝트(FinFlow AI)** 를 함께 구현합니다.

---

## FinFlow AI란?

> 공공 금융 데이터를 자동 수집·검증·저장하고, AI가 금융 정보를 이해하기 쉽게 설명하는 금융 리서치 플랫폼

**대상:** 사회초년생·투자 입문자
**문제:** 금융 뉴스를 읽어도 용어와 재무지표를 이해하기 어려워 투자 판단 이전에 학습 장벽을 경험
**해결:** 공공 금융 데이터를 자동 수집하고 AI가 초보자 눈높이로 설명

---

## FinFlow AI 데이터 소스

### Phase 1 (부트캠프 기간, 현재 진행)

| 데이터 | 출처 | 용도 | 저장소 | 상태 |
|--------|------|------|--------|------|
| 주식 시세 | 금융위원회 OpenAPI | 주가 조회, 시계열 | BigQuery | ✅ 300건 완료 |
| 기업 기본정보 | 금융위원회 OpenAPI | 기업명, 업종, 상장 정보 | BigQuery | ⏳ 예정 |
| 기업 재무정보 | 금융위원회 OpenAPI | 매출, 영업이익, 자산, 부채 | BigQuery + ChromaDB | ⏳ 예정 |
| 금융용어 | 한국은행 경제금융용어사전 | RAG 용어사전 | ChromaDB | ✅ 69건 완료 |

### Phase 2 (수료 후 확장)

| 데이터 | 출처 | 용도 |
|--------|------|------|
| 공시 | 금융감독원 OpenDART | 사업보고서, 분기보고서 RAG |
| 경제지표 | 한국은행 ECOS | 기준금리, 환율, CPI, GDP |
| 금융 뉴스 | RSS / 뉴스 API | 최신 이슈 설명 |
| 증권사 리포트 | - | 기업 분석 RAG |

---

## FinFlow AI 아키텍처

```
                    사용자 질문
                         │
          ┌──────────────┴──────────────┐
          │                             │
   숫자·기간·순위 질문            의미·설명·요약 질문
   "삼성전자 최근 주가는?"        "PER이 뭐야?"
          │                             │
       BigQuery                      ChromaDB
       SQL 조회                    벡터 유사도 검색
          │                             │
          └──────────────┬──────────────┘
                         │
                       Gemini
                     답변 생성
                         │
                 초보자 눈높이 최종 답변
```

### 데이터 성격별 저장소 분리

```
FinFlow AI
│
├─ BigQuery (정형 데이터)
│   ├─ stock_prices     주가 시세 (날짜, 종가, 거래량)
│   ├─ company_info     기업 기본정보
│   ├─ financials       기업 재무 원본 수치
│   └─ query_logs       사용자 검색 로그 (운영 분석용)
│
└─ ChromaDB (비정형 텍스트)
    ├─ glossary              금융용어 (완료)
    ├─ financial_summaries   기업재무 요약문 (예정)
    ├─ dart                  DART 공시 (Phase 2)
    └─ news                  기업 뉴스 (Phase 2)
```

| 데이터 | 저장소 | 이유 |
|--------|--------|------|
| 주가 시세, 재무 원본 수치 | BigQuery | 정형 숫자 → SQL 집계·정렬이 적합 |
| 기업재무 요약 문장 | ChromaDB | 숫자→텍스트 변환 후 의미 검색 |
| 금융용어 정의 | ChromaDB | 텍스트 → 의미 기반 검색 |
| 사용자 질문·피드백 | BigQuery `query_logs` | 서비스 운영 데이터 분석 |

### 사용자 검색 로그 (`query_logs`)

단순한 AI 챗봇을 넘어 **데이터 수집 → 운영 → 분석 → AI 서비스**까지 연결하는 구조입니다.

```sql
-- 자주 검색된 금융용어 분석
SELECT query, COUNT(*) AS search_count
FROM `data_sample.query_logs`
GROUP BY query
ORDER BY search_count DESC;
```

---

## FinFlow AI 개발 순서

**같은 파이프라인을 데이터만 바꿔서 반복하는 구조 — 재사용 가능한 설계가 핵심**

```
1단계 (완료) — 금융용어 컬렉션
한국은행 API → 수집 → 전처리 → 청킹 → 임베딩 → ChromaDB glossary → 검색 테스트

2단계 (3주차~) — 기업재무 추가
금융위원회 API → 수집 → 숫자→문장 변환 → 청킹 → 임베딩 → ChromaDB financial_summaries

3단계 (Phase 2) — DART 공시
DART API → 수집 → 전처리 → 청킹 → 임베딩 → ChromaDB dart

4단계 (Phase 2) — 기업 뉴스
뉴스 → 수집 → 전처리 → 청킹 → 임베딩 → ChromaDB news
```

---

## 전체 파이프라인 (10단계)

```
1. 데이터 수집      → API로 원천 데이터 확보
2. 데이터 검증      → 필수 필드, record_count 일치 여부 확인 (이중 게이트)
3. 전처리           → 텍스트 정제, 숫자→설명 문장 변환, 청킹
4. BigQuery 적재    → 정형 데이터 DW 저장 + ingest_metadata 이력 기록
5. 텍스트 임베딩    → SentenceTransformer로 벡터화 (dim=128)
6. ChromaDB 저장    → 벡터 DB에 컬렉션별 적재
7. Airflow 자동화   → DAG으로 전체 파이프라인 스케줄링
8. RAG 검색         → 쿼리 → 유사 청크 검색 → Gemini로 답변 생성
9. Streamlit 대시보드 → 금융 데이터 시각화 + 검색 UI
10. Cloud Run 배포  → 공개 접근 가능한 서비스로 배포
```

---

## 주차별 진행 상황

| 주차 | 주제 | 상태 | 폴더 |
|------|------|------|------|
| 1주차 | API 수집 → GCS 업로드 → BigQuery 적재 | ✅ 완료 | [de-bootcamp-week1/](./de-bootcamp-week1) |
| 2주차 | 데이터 전처리 · 청킹 · 임베딩 | ✅ 완료 | [de-bootcamp-week2/](./de-bootcamp-week2) |
| 3주차 | ChromaDB 적재 · Airflow DAG 자동화 | ⏳ 예정 | - |
| 4주차 | RAG 검색 + Gemini · Streamlit 대시보드 | ⏳ 예정 | - |
| 5주차 | Cloud Run 배포 · 최종 프로젝트 | ⏳ 예정 | - |

---

## 주차별 핵심 성과

### 1주차
**부트캠프 실습**
- JSONPlaceholder API 수집 → GCS → BigQuery `posts` 테이블 적재 (20건)
- `ingest_metadata` 테이블로 실행 이력 추적 ("파이프라인의 영수증")

**FinFlow AI**
- 금융위원회 OpenAPI → 주식 시세 **300건** 수집 → BigQuery `stock_prices` 적재
- 한국은행 경제금융용어사전 **69건** 수집 (내부 JSON API 역분석)
- BeautifulSoup 시도 → JS 렌더링 문제 발견 → Network 탭 분석 → 숨겨진 JSON API 역분석 → requests 전환
- 데이터 소스별 파이프라인 분리 설계 (`day2_upload_to_gcp.py` / `day2_upload_stock_to_gcp.py`)

### 2주차
**부트캠프 실습**
- HuggingFace 페르소나 데이터 20건 → 청킹 **220청크** → 임베딩 (dim=128)
- 코사인 유사도 기반 쿼리 스크립트 구현, 검증 Q&A 10개 완성

**FinFlow AI**
- 금융용어 69건 → 전처리 → 청킹 **292청크** → 임베딩 완료
- `fin_terms_query.py`로 의미 기반 금융용어 검색 구현
- **청킹 개선 실험:** 문자 수 기준 청킹 → 문장 중간 끊김 문제 발견 → 150자 단위 균형 청킹으로 개선 → ETF 검색 성공

---

## 포트폴리오 포인트

### 1주차
- 공공데이터포털 API 연동 실무 경험 (Decoding 키, 페이지네이션, 중첩 응답 구조)
- 한국은행 사이트 내부 JSON API 역분석 — "BeautifulSoup 실패 → 개발자도구 Network 분석 → requests 전환"이라는 문제 해결 과정 자체가 현업 DE의 일상
- 데이터 성격(정형/비정형)에 따라 저장소를 분리하는 설계 감각

### 2주차
> 초기 버전에서는 문자 수 기준으로 청킹하여 문장이 중간에서 끊기는 문제가 있었다. 검색 품질 향상을 위해 150자 단위 균형 청킹으로 개선하여 의미 단위가 유지되도록 수정하였다.

단순히 과제를 한 것이 아니라 **검색 품질을 고민하고 개선한 엔지니어**라는 것을 보여줍니다.

---

## GCP 환경

| 항목 | 값 |
|------|------|
| 프로젝트 ID | `melodic-furnace-501500-g4` |
| GCS 버킷 | `dbai-2026-project` |
| BigQuery 데이터셋 | `data_sample` |

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

## 주요 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `ImportError: bigquery` | conda base와 uv venv 공존 | `.venv/bin/python` 직접 호출 |
| `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` | 공공데이터포털 Encoding 키 사용 | Decoding 키로 교체 |
| ETF 검색 시 엉뚱한 결과 | 긴 설명이 1개 청크 → 핵심 내용 매칭 안 됨 | 150자 단위 균형 청킹으로 개선 |
| 문장이 중간에서 끊김 | 고정 문자 수 기준 청킹의 한계 | 문장 단위 분리 후 길이 균형 조정 |
