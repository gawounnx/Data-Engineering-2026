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

이 스크립트는 기존 `data/chroma/`를 지우고 다시 만들기 때문에 `.gitkeep`이 사라질 수 있습니다. 그렇게 되면 `git checkout -- data/chroma/.gitkeep`으로 복구합니다.

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

> 스냅샷이 없는 새 버킷에서 실행하면 `financial_news`만 만들어집니다. 이 상태로는 앱이 "데이터 준비 필요"를 표시합니다.

### 2-C. 금융용어 컬렉션을 처음부터 생성 (현재 저장소 범위 밖)

`financial_terms` 생성 스크립트는 아직 이 저장소에 포함되지 않았습니다. 원본 작업 환경의 `de-bootcamp-week1/`에 있으며 순서는 다음과 같습니다.

| 단계 | 스크립트 (week1 기준) | 입력 → 출력 | 추가 의존성 |
|---|---|---|---|
| 1. PDF 텍스트 추출 | `scripts/parser/extract_financial_terms.py` | 한국은행 「2026 경제금융용어 800선」 PDF → `financial_terms_pages.json` | `pdfplumber` |
| 2. 용어 파싱 | `scripts/parser/parse_financial_terms_v3.py` | → `financial_terms_800_v3.json` | |
| 3. 정제 | `scripts/preprocessing/clean_financial_terms.py` | → `financial_terms_800_v4_cleaned.json` | |
| 4. BigQuery 적재 | `scripts/bigquery/load_financial_terms.py` | → `<프로젝트>.<데이터셋>.financial_terms` | `GCP_PROJECT_ID`, `BQ_DATASET_ID` |
| 5. 청킹 | `scripts/chunking/create_financial_term_chunks.py` | BigQuery → `financial_terms_chunks.json` | |
| 6. 임베딩 | `scripts/embedding/create_financial_term_embeddings.py` | → `financial_terms_embeddings.json` | `kekeappa/kor-static-embedding-128` (앱과 동일해야 함) |
| 7. ChromaDB 적재 | `scripts/chromadb/load_financial_terms.py` | → `data/chroma` `financial_terms` 컬렉션 | |

원본 PDF는 저작권 확인 전까지 저장소에 넣지 않습니다.

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

## 3-1. 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest tests -v
```

| 파일 | 내용 |
|---|---|
| `tests/test_news_snapshot.py` | 데이터 준비 상태 판단 시나리오 10개. 빈 clone, 빈 DB, 빈 컬렉션, 정상 로컬 데이터, 인증 실패 2종, 스냅샷 부재, 용어 누락 스냅샷 거부, 정상 스냅샷 적용과 이후 실패 시 유지 |
| `tests/test_snapshot_extract.py` | 스냅샷 압축 해제 보안 검증. 일반 파일과 디렉터리만 허용하는지 확인. 경로 탈출(`..`, 절대 경로, Windows 경로), 심볼릭 링크, 하드 링크, 장치 파일, FIFO, 링크를 통한 우회 쓰기를 거부하는지 확인. 거부된 스냅샷은 적용하지 않고 정리하는지, 숫자가 아닌 generation을 거부하는지 확인 |
| `tests/test_app_ui.py` | Streamlit AppTest로 데이터 없음과 있음 각각의 화면 검증 |

테스트는 매번 임시 폴더에 작은 ChromaDB를 새로 만듭니다. 그래서 실제 데이터, GCS 인증, Gemini 키 없이 실행됩니다. GCS는 mock으로 대체하고 이벤트 로그 전송도 막습니다. 질문 제출부터 Gemini 답변까지의 흐름은 테스트하지 않습니다.

## 4. 컨테이너 빌드와 배포

> 이 문서의 컨테이너 빌드·실행과 Cloud Run 동작은 아직 실제로 검증하지 않았습니다. 정적 점검과 로컬 Python 실행만 확인했습니다.

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
