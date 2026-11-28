# 아키텍처와 기술 결정

## 실제 확인 범위

앱, 금융용어·뉴스 파이프라인, Airflow DAG와 배포 설정은 [FinFlow 앱 안내](../finflow/README.md)에 정리돼 있습니다. 아래 구조는 코드 기준입니다. 2026-11-26에 앱과 뉴스 Job을 Cloud Run에서 실행한 기록과 검증 범위는 [검증 기록](../README.md#검증)을 참고합니다.

## 데이터 경로

| 경로 | 처리 | 저장·소비 |
|---|---|---|
| 금융용어 | 한국은행 PDF 추출·파싱·정제 → 청킹·임베딩. [금융용어 파이프라인](../finflow/scripts/terms/README.md) | BigQuery 용어 테이블 / ChromaDB financial_terms / RAG |
| 주식 시세 | API 응답의 필수 필드·건수·숫자 검증 → 수치 변환 | GCS 원본 / BigQuery stock_prices (현재 앱에는 미연결) |
| 금융 뉴스 | 네이버 검색 API → 기사 검증·중복 제거 → BigQuery에서 읽어 청킹·임베딩 | 날짜별 GCS 원본 / BigQuery financial_news / ChromaDB financial_news |
| 질문 | 질문 분석 → 금융용어 벡터 검색·재정렬 → 뉴스 하이브리드(키워드·벡터·최신성) 검색 → Gemini 답변 | Streamlit 답변·용어 근거·뉴스 출처 표시 |
| 벡터 스냅샷 | `data/chroma` 전체(두 컬렉션)를 tar.gz로 묶음 | GCS `finflow/vectorstore/chroma/latest.tar.gz` |

초기 코드의 데이터셋은 `data_sample`, 후속 뉴스·배포 기록에는 `finflow`가 등장합니다. 이름만 바꾸어 동일 테이블이 존재한다고 가정하지 않고 실제 데이터셋·리전을 확인해야 합니다.

## 결정과 대안

| 선택 | 해결 문제 | 비용·한계 | 더 단순한 대안 |
|---|---|---|---|
| BigQuery와 ChromaDB 분리 | 숫자 SQL 조회와 문장 의미 검색의 서로 다른 요구 | 두 저장소의 데이터 버전 관리 필요 | 수치가 거의 없으면 JSON/SQLite + ChromaDB |
| GCS 원본 보관 | 수집 결과 추적·재처리 | 저장 비용, 원본과 테이블 연결 관리 | 개인 로컬 파일 보관 |
| 128차원 임베딩 | 작은 벡터로 금융용어 검색 구성 | 다른 모델 대비 우수성 미측정 | 키워드 검색을 기준선으로 비교 |
| Streamlit | 사용자 질문·응답 화면을 빠르게 구현 | 세밀한 UX 제어·서비스 확장에 제약 | 초기에는 CLI |
| Airflow | 수집·검증·업로드·적재 단계 관찰 | 별도 Python 환경·scheduler 운영 부담 | 단일 파이프라인 스크립트 + 스케줄러 |
| Cloud Run 앱·Job | 상시 서버 관리 없이 시연·배치 분리 | cold start, 메모리, 외부 API 비용·권한 관리 | 로컬 시연과 수동 수집 |

공개 코드에는 두 실행 경로가 모두 있습니다. Airflow DAG(`finflow/dags/`)는 수집 → 검증 → GCS → BigQuery까지 실행합니다. Cloud Run Job용 파이프라인(`finflow/scripts/pipelines/run_news_pipeline.py`)은 여기에 더해 청킹, 임베딩, ChromaDB 적재, 스냅샷 업로드까지 실행합니다. 수집과 적재 단계가 겹치므로, 실제 운영 주체와 스케줄을 정해 단순화해야 합니다.

## 배포와 데이터 수명

코드 기준 데이터 공급 방식은 두 가지입니다([finflow/README.md 4장](../finflow/README.md)).
- **이미지에 포함:** 빌드 전에 스냅샷을 내려받아 `data/chroma`를 이미지에 넣습니다.
- **런타임 로드:** `FINFLOW_BUCKET`을 설정하면 앱이 GCS 스냅샷을 5분 간격으로 확인합니다. 새 세대가 있으면 임시 폴더에 내려받아 검증한 뒤 적용합니다.

과거 빌드 실패 원인이었던 `COPY data/chroma`는 `data/chroma/.gitkeep`과 `.dockerignore` 수정으로 보완했습니다. 다만 실제 빌드와 Cloud Run 동작은 다시 확인하지 않았습니다. Gemini 키는 Secret Manager 같은 런타임 주입을 전제로 합니다. Cloud Run의 로컬 변경은 영구 저장으로 간주하지 않습니다.

## 남은 설계 판단

- 현재 앱은 질문마다 금융용어와 뉴스를 모두 검색하고, 주가는 조회하지 않음. 질문 유형별 라우팅이 필요한지 결정
- 뉴스 검색 결과는 프롬프트와 화면 출처에 함께 쓰임. 답변에서 용어 근거와 뉴스 근거를 구분해 보여 주는지 확인
- 근거가 부족하면 답변을 보류하는 조건 정의
- 벡터 데이터·모델·청킹 설정의 버전과 생성 일자 함께 기록
- FastAPI는 필요성이 확인되기 전까지 필수 구성으로 추가하지 않음
