# FinFlow 앱

금융용어 RAG와 네이버 금융뉴스 하이브리드 검색을 결합한 Streamlit 앱입니다. 모든 명령은 이 `finflow/` 디렉터리에서 실행합니다.

## 구성

| 구분 | 경로 |
|---|---|
| 앱 진입점 | `app_ui.py` (대시보드, Docker 기본), `app.py` (기본 UI) |
| 검색·생성 | `src/` (rag, retriever, news_retriever, reranker, llm, prompt, query_analyzer, news_snapshot) |
| 뉴스 파이프라인 | `scripts/pipelines/run_news_pipeline.py`와 `scripts/` 하위 단계별 스크립트 |
| Airflow DAG | `dags/naver_news_pipeline_dag.py` |
| 배포 | `Dockerfile.app`, `Dockerfile.news-job`, `cloudbuild.*.yaml`, `.dockerignore`, `.gcloudignore` |

## 1. 환경 준비

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --index-url https://download.pytorch.org/whl/cpu torch   # CPU 환경
pip install -r requirements.txt
cp .env.example .env               # 값 채우기 (.env는 Git에 올리지 않음)
gcloud auth application-default login   # GCS·BigQuery 접근 시
```

## 2. 데이터 준비 (Git에 포함하지 않음)

clone 직후 `data/chroma/`에는 빈 폴더를 유지하기 위한 `.gitkeep`만 있습니다. 앱은 질문마다 아래 **두 컬렉션을 모두** 검색하므로, 둘 다 있고 비어 있지 않아야 합니다.

| 컬렉션 | 용도 | 검색 모듈 | 준비 방법 |
|---|---|---|---|
| `financial_terms` | 금융용어 RAG | `src/retriever.py` | GCS 스냅샷에서 복원 (2-A). 처음부터 생성하는 방법은 2-C |
| `financial_news` | 뉴스 검색 | `src/news_retriever.py` | GCS 스냅샷에서 복원 (2-A) 또는 뉴스 파이프라인 (2-B) |

두 컬렉션은 같은 ChromaDB(`data/chroma/chroma.sqlite3`)에 저장됩니다. GCS 스냅샷 `finflow/vectorstore/chroma/latest.tar.gz`는 `data/chroma/` 폴더 전체를 묶은 것이므로 **두 컬렉션이 함께 복원**됩니다. 뉴스 파이프라인은 이 스냅샷을 내려받아 `financial_news`만 갱신한 뒤 다시 올리기 때문에, `financial_terms`는 스냅샷 안에서 계속 유지됩니다.

### 2-A. GCS 스냅샷 내려받기 (권장)

```bash
# FINFLOW_BUCKET / CHROMA_SNAPSHOT_OBJECT를 비우면 스크립트 기본값을 사용
python scripts/vectorstores/download_chroma_snapshot.py
```

이 스크립트는 스냅샷을 `data/` 아래 임시 폴더에 내려받아 다음을 확인합니다.
- 압축 항목이 `chroma/` 아래의 일반 파일·디렉터리인지
- `financial_terms`와 `financial_news`가 모두 있고 비어 있지 않은지

모두 통과한 경우에만 기존 `data/chroma/`를 교체합니다. 다운로드, 압축 해제, 검증 중 어느 단계에서 실패해도 기존 데이터는 그대로 남습니다. 교체 후에는 스냅샷에 `.gitkeep`이 없을 수 있으니, 필요하면 `git checkout -- data/chroma/.gitkeep`으로 복구합니다.

### 2-B. 뉴스 데이터 갱신 (전체 파이프라인)

```bash
python scripts/pipelines/run_news_pipeline.py
```

실행 순서는 다음과 같습니다.
1. 스냅샷 다운로드
2. 네이버 뉴스 수집 (`data/raw/`)
3. GCS 업로드
4. BigQuery 적재
5. BigQuery에서 읽어 청킹 (`data/processed/`)
6. 임베딩
7. ChromaDB 적재
8. 스냅샷 업로드

필요한 것: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, GCS·BigQuery 권한(ADC).

배포 환경에서는 Cloud Scheduler가 매시간 Cloud Run Job(`finflow-news-job`)으로 이 파이프라인을 실행합니다. Airflow DAG(`dags/`)는 로컬 오케스트레이션 예시이며 1~4단계만 포함합니다.

**검색용 뉴스는 최근 7일만 유지합니다.**
- 청킹(5단계)
  - 최근 `NEWS_RETENTION_DAYS`일(기본 7일) 안에 발행된 기사만 대상입니다.
  - 한 번에 최대 `NEWS_MAX_ARTICLES_PER_RUN`건(기본 2000건)만 처리합니다.
  - 진행 위치는 `(collected_at, article_id)` 키로 `financial_news` 컬렉션 메타데이터(`ingest_*`)에 저장합니다. 수집기는 한 번 실행에 같은 `collected_at`을 쓰므로, `article_id`까지 비교해야 같은 시각 기사가 처리량 경계에 걸쳐도 빠지거나 반복되지 않습니다.
  - 새 기사를 최신순으로 먼저 처리합니다. 처리량보다 많으면 최신 기사만 처리하고 나머지는 "아직 못 채운 구간"으로 남겨, 이후 실행에서 남는 처리량으로 최신 쪽부터 거꾸로 채웁니다. 그래서 첫 실행이나 장애 복구 때도 앱에는 최신 기사부터 들어갑니다.
  - 진행 위치는 스냅샷과 함께 저장되므로, 업로드 전에 실패한 실행은 진행 위치를 바꾸지 않고 다음 실행이 같은 기사부터 다시 처리합니다.
  - 한계: 이미 지나간 진행 위치보다 이른 `collected_at`으로 BigQuery에 늦게 적재된 기사는 읽지 않습니다. 현재 운영 경로(같은 Job 안에서 수집 → 적재 → 청킹)에서는 생기지 않지만, 다른 경로로 과거 시각 데이터를 적재하면 생길 수 있습니다.
- ChromaDB 적재(7단계)
  - 보관 기간 안의 기존 청크와 새 청크로 임시 컬렉션을 만들어 검증한 뒤, `financial_news`와 교체합니다. 중간에 실패하면 기존 컬렉션이 그대로 남습니다.
  - 교체 후 지운 데이터의 공간을 정리합니다. ChromaDB는 데이터나 컬렉션을 지워도 인덱스 파일을 줄이지 않아, 정리하지 않으면 스냅샷이 계속 커집니다.
  - `financial_terms`는 읽거나 바꾸지 않습니다.
- 스냅샷 업로드(8단계): 두 컬렉션이 모두 있고 비어 있지 않을 때만 올립니다.
- BigQuery와 GCS의 뉴스 원본은 지우지 않습니다.
- 앱도 같은 `NEWS_RETENTION_DAYS`를 읽어 뉴스 검색 범위를 최근 7일로 제한하고, 화면에 제공 범위를 표시합니다.

> 스냅샷이 없는 새 버킷에서 실행하면 `financial_news`만 만들어집니다. 이 상태로는 앱이 "데이터 준비 필요"를 표시합니다.

### 2-C. 금융용어 컬렉션을 처음부터 생성

`financial_terms` 생성 스크립트는 `scripts/terms/`에 있습니다. 실행 방법과 검증 결과는 [scripts/terms/README.md](scripts/terms/README.md)를 참고합니다.

순서: PDF 추출 → 파싱 → 정제 → BigQuery 적재 → 청킹 → 임베딩 → ChromaDB 적재

- 4단계 BigQuery 적재는 **테이블 전체를 덮어씁니다(WRITE_TRUNCATE).** 기본은 dry-run이고, 실제로 쓰려면 `--execute --confirm-table <테이블 ID>`가 필요합니다.
- 7단계 ChromaDB 적재의 기본 저장 경로는 서비스 DB(`data/chroma`)와 분리된 `data/terms/chroma`입니다. 서비스 DB에 쓰려면 `--allow-service-db`가 필요하고, 기존 컬렉션을 초기화하려면 `--replace`가 필요합니다.
- 추가 의존성은 `requirements-terms.txt`(pdfplumber)에 있습니다. 원본 PDF는 저작권 확인 전까지 저장소에 넣지 않습니다.

### 앱의 데이터 상태 판단

`src/news_snapshot.py`는 화면을 그릴 때마다 데이터 상태를 확인합니다.

| 상황 | 앱 동작 |
|---|---|
| 버킷 미설정 + 로컬 DB 없음 (clone 직후) | "검색 데이터 준비 필요"와 준비 방법 안내, 검색 입력 대신 안내 표시. 빈 DB 파일을 만들지 않음 |
| 로컬 DB에 컬렉션이 없거나 비어 있음 | 같은 안내와 누락된 컬렉션 이름 표시 |
| `FINFLOW_BUCKET` 설정 + 인증 실패 | 인증 오류 원인 안내. 적용된 스냅샷이 없으면 로컬 DB 상태로 판단 |
| `FINFLOW_BUCKET` 설정 + 스냅샷 없음 | "GCS 스냅샷을 찾을 수 없습니다" 안내. 로컬 DB 상태로 판단 |
| 내려받은 스냅샷에 필수 컬렉션 누락 | 해당 스냅샷을 적용하지 않음 |
| 이미 적용한 스냅샷이 있는 상태에서 이후 확인 실패 | 기존 스냅샷으로 계속 서비스하고 오류만 표시 |

## 3. 로컬 실행

```bash
streamlit run app_ui.py
# 또는: 스냅샷 복원 후 app.py 실행
python scripts/start_streamlit.py
```

`.env`의 `FINFLOW_BUCKET`과 `CHROMA_SNAPSHOT_OBJECT`가 모두 비어 있으면 로컬 `data/chroma/`를 씁니다. 하나라도 값이 있으면 GCS 스냅샷을 5분 간격으로 확인합니다. 새 세대가 있으면 임시 폴더에 내려받아 검증한 뒤 검색 경로를 교체합니다.

### Gemini 설정과 호출량

`src/llm.py`는 API 버전을 코드에서 명시합니다. `google-genai`는 `requirements.txt`에서 검증한 버전(3.6.1)으로 고정합니다.

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_MODEL` | `gemini-flash-latest` | 모델 이름 또는 별칭 |
| `GOOGLE_GENAI_API_VERSION` | `v1beta` | 비어 있으면 `v1beta`를 씁니다. `google-genai` 3.x의 기본값은 `v1`이고, `gemini-flash-latest` 별칭은 `v1`에서 404를 반환합니다 |

질문 하나에 Gemini를 보통 3번 호출합니다(질문 분석 → 금융용어 재정렬 → 최종 답변). 재시도 정책은 따로 설정하지 않고 SDK 기본 동작을 따릅니다. `google-genai` 3.6.1에서 오류 응답은 재시도 없이 한 번만 보내는 것을 오프라인 테스트로 확인했습니다.

2026-11-22 로컬 측정값입니다. `gemini-flash-latest`가 `gemini-3.8-flash`로 연결됐고, "기준금리가 뭐야?" 한 번에 다음과 같았습니다.
- 요청 3회
- 토큰 합계 약 7.7k (사고 토큰 포함)
- 화면 기준 약 36초

모델 버전이나 질문에 따라 값은 달라집니다.

요금은 앱을 배포한 프로젝트가 아니라 **API 키가 속한 프로젝트의 결제 계정**에 청구됩니다. 그 결제 계정의 선불 잔액이 0이면 모든 질문이 HTTP 402 `RESOURCE_EXHAUSTED`로 실패합니다. 이때는 AI Studio에서 키의 프로젝트를 확인한 뒤 결제 계정을 바꾸거나 잔액을 충전합니다.

## 3-1. 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest tests -v
```

| 파일 | 내용 |
|---|---|
| `tests/test_news_snapshot.py` | 데이터 준비 상태 판단 시나리오 10개. 빈 clone, 빈 DB, 빈 컬렉션, 정상 로컬 데이터, 인증 실패 2종, 스냅샷 부재, 용어 누락 스냅샷 거부, 정상 스냅샷 적용과 이후 실패 시 유지 |
| `tests/test_terms_pipeline.py` | 금융용어 생성 파이프라인의 운영 데이터 보호. BigQuery 적재가 기본 dry-run이고 확인 문자열이 맞을 때만 덮어쓰는지, 청킹의 BigQuery 모드가 읽기만 하는지, 오프라인 청킹 모드의 열·순서, ChromaDB 적재의 서비스 DB 보호와 `--replace` 초기화 범위 |
| `tests/test_snapshot_extract.py` | 스냅샷 압축 해제 보안 검증. 일반 파일과 디렉터리만 허용하는지 확인. 경로 탈출(`..`, 절대 경로, Windows 경로), 심볼릭 링크, 하드 링크, 장치 파일, FIFO, 링크를 통한 우회 쓰기를 거부하는지 확인. 거부된 스냅샷은 적용하지 않고 정리하는지, 숫자가 아닌 generation을 거부하는지 확인 |
| `tests/test_app_ui.py` | Streamlit AppTest로 데이터 없음과 있음 각각의 화면 검증 |
| `tests/test_download_snapshot_script.py` | `download_chroma_snapshot.py`가 다운로드·압축 해제·검증·교체 중 어느 단계에서 실패해도 기존 `data/chroma/`를 보존하는지, `chroma/` 밖 경로와 심볼릭 링크를 거부하는지 확인 |
| `tests/test_term_ranking.py` | 키워드 정확 일치 용어가 병합 결과 상위에 오는지 확인 |
| `tests/test_env_defaults.py` | 빈 환경 변수가 스크립트 기본값을 덮어쓰지 않는지 확인 |
| `tests/test_news_incremental.py` | 청킹 진행 위치(같은 수집 시각 기사가 2000건 경계에 걸쳐도 누락·반복 없음, 첫 복구 때 최신 기사 우선, 무작위 수집량에서도 누락 없음; 실제 SQL을 SQLite로 실행해 검증), 실패 후 재실행 시 미반영 기사를 건너뛰지 않는지, 컬렉션 교체·정리 후 스냅샷을 복원해 새 프로세스에서 금융용어 ID·내용·벡터와 검색 결과가 같은지, 새 뉴스가 없을 때 임베딩 모델을 불러오지 않는지, ChromaDB 7일 보관과 금융용어 보존, 실패 시 기존 컬렉션 유지, 반복 실행 시 저장 공간이 늘지 않는지, 빈 컬렉션 업로드 거부, 앱의 뉴스 검색 범위와 안내 문구. BigQuery·GCS·임베딩 모델은 mock으로 대체 |
| `tests/test_llm_config.py` | Gemini 요청이 기본 `v1beta` 경로로 가는지 확인. `GOOGLE_GENAI_API_VERSION`이 빈 값이면 `v1beta`, 값이 있으면 그 버전을 쓰는지 확인. 키를 헤더로 보내는지, 오류 응답을 재시도 없이 한 번만 보내는지 확인. HTTP 전송 계층은 가짜 응답으로 대체 |

테스트는 매번 임시 폴더에 작은 ChromaDB를 새로 만듭니다. 그래서 실제 데이터, GCS 인증, Gemini 키 없이 실행됩니다. GCS와 Gemini HTTP 전송은 mock으로 대체하고, 이벤트 로그 전송도 막습니다. 실제 Gemini 응답 품질과 질문 제출부터 답변까지의 화면 흐름은 자동 테스트에 포함하지 않습니다.

18문항 실제 평가와 저장 검색어의 오프라인 재생은 [RAG 평가와 뉴스 검색 재현](scripts/rag/README.md)을 참고하세요.

## 4. 컨테이너 빌드와 배포

> 2026-11-26에 Cloud Build로 앱 이미지를 빌드해 Cloud Run에 배포했습니다. 당시 `finflow-app-00014-kos` 리비전은 트래픽 0% 태그 URL에서 확인한 뒤 전환했습니다. 뉴스 Job은 Cloud Scheduler `finflow-news-daily`(매일 08·12·18시 KST)로 실행합니다. [검증 범위](../README.md#검증)

`Dockerfile.app`은 `COPY data/chroma ./data/chroma`로 빌드 시점의 로컬 ChromaDB를 이미지에 넣습니다. 빌드 방법은 두 가지입니다.

- **데이터를 이미지에 포함:** 빌드 전에 2-A를 실행합니다.
  ```bash
  python scripts/vectorstores/download_chroma_snapshot.py
  gcloud builds submit --config cloudbuild.app.yaml .
  ```
- **런타임에 내려받기:** 데이터 없이 빌드해도 `.gitkeep` 덕분에 `COPY`는 성공합니다. 이 경우 Cloud Run 서비스에 `FINFLOW_BUCKET` 환경 변수를 설정해야 합니다. 서비스 계정에는 스냅샷 객체의 읽기 권한이 필요합니다. 둘 다 없으면 앱은 "검색 데이터 준비 필요"를 표시합니다.

`GEMINI_API_KEY` 같은 비밀 값은 이미지에 넣지 말고 Cloud Run 환경 변수나 Secret Manager로 주입합니다.

뉴스 Job:

```bash
gcloud builds submit --config cloudbuild.news-job.yaml .
```

## 5. Airflow DAG

`dags/naver_news_pipeline_dag.py`는 수집 → 검증 → GCS → BigQuery를 실행합니다. 각 단계는 FinFlow 가상환경의 Python으로 `scripts/` 아래 스크립트를 subprocess로 실행합니다. Airflow 환경과 앱 환경은 분리되어 있습니다(개발 시 Airflow 2.10 / Python 3.11, 앱 / Python 3.12).

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `FINFLOW_PROJECT_ROOT` | DAG 파일의 상위 폴더 (`finflow/dags/` → `finflow/`) | 스크립트 실행 위치이자 `.env`를 읽는 위치 |
| `FINFLOW_PYTHON` | `<루트>/.venv/bin/python` (Windows: `.venv\Scripts\python.exe`) | 스크립트 실행용 Python. `requirements.txt`가 설치된 환경이어야 함 |

```bash
export AIRFLOW_HOME="$PWD/airflow"                 # airflow.cfg·메타 DB·로그 (Git 제외)
export AIRFLOW__CORE__DAGS_FOLDER="$PWD/dags"      # 이 폴더를 직접 읽으면 루트 자동 계산
# DAG 파일을 다른 dags 폴더로 "복사"해 쓰는 경우에만 필요 (심볼릭 링크는 자동 인식)
# export FINFLOW_PROJECT_ROOT="/path/to/finflow"
# 가상환경 위치가 다를 때
# export FINFLOW_PYTHON="/path/to/venv/bin/python"
airflow db migrate
airflow standalone
```

이 환경 변수들은 DAG를 파싱하는 Airflow 프로세스(scheduler / dag-processor)에 설정해야 합니다. 앱이 읽는 `.env`에 넣어도 DAG에는 적용되지 않습니다.
