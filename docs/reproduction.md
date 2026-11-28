# 실행과 재현

## 현재 실행 가능한 범위

앱, 뉴스 파이프라인, DAG, 배포 설정은 [FinFlow 앱 안내](../finflow/README.md)에 있습니다. 생성된 벡터 DB는 GCS 스냅샷으로 복원합니다. 한국은행 PDF에서 금융용어 컬렉션을 재현하는 코드도 [금융용어 파이프라인](../finflow/scripts/terms/README.md)에 있습니다.

실행 명령은 저장소 이름이 바뀌어도 쓸 수 있도록 현재 checkout 디렉터리를 기준으로 합니다.

## FinFlow 앱 실행

전제: Python 3.12, 저장소 루트에서 시작합니다. 자세한 설명은 [finflow/README.md](../finflow/README.md)를 따릅니다.

```bash
cd finflow
python3.12 -m venv .venv
source .venv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cpu torch
pip install -r requirements-dev.txt
cp .env.example .env                                     # GEMINI_API_KEY 등 값 채우기
gcloud auth application-default login                    # GCS 스냅샷 접근 시
python scripts/vectorstores/download_chroma_snapshot.py  # financial_terms + financial_news 복원
streamlit run app_ui.py
```

데이터가 없거나 컬렉션이 비어 있으면, 앱은 검색 입력 대신 데이터 준비 방법을 보여 줍니다.

자동 테스트는 데이터, GCS 인증, Gemini 키 없이 실행됩니다.

```bash
python -m pytest tests -v
```

## 금융용어 색인 오프라인 재현

`finflow/`에서 `requirements-terms.txt`를 설치합니다. 한국은행 「2026 경제금융용어 800선」 PDF를 준비하고 `TERMS_PDF_PATH`와 별도 작업 경로를 지정합니다. 추출 → 파싱 → 정제 → `chunk_terms.py --from-cleaned-json` → 임베딩 → ChromaDB 적재 순서로 실행합니다. 캐시된 임베딩 모델을 사용하면 클라우드 인증 없이 재현할 수 있으며, 기본 대상은 `data/terms/chroma`입니다. [명령과 입력 준비](../finflow/scripts/terms/README.md)

## 저장소에 반영된 파일

| 대상 | 경로 | 상태 |
|---|---|---|
| 앱 | `finflow/app_ui.py`, `finflow/app.py`, `finflow/src/`, `finflow/utils/`, `finflow/styles/` | 반영 |
| 의존성 | `finflow/requirements.txt`, `finflow/requirements-dev.txt` | 반영. Airflow는 별도 환경 |
| 뉴스 | `finflow/scripts/collectors/`, `loaders/`, `processors/`, `embeddings/`, `vectorstores/`, `pipelines/` | 반영 |
| 자동화 | `finflow/dags/naver_news_pipeline_dag.py` | 반영. 경로는 환경 변수로 설정 가능 |
| 배포 | `finflow/Dockerfile.app`, `Dockerfile.news-job`, `cloudbuild.*.yaml`, `.dockerignore`, `.gcloudignore`, `scripts/start_streamlit.py` | 반영 |
| 설정 | `finflow/.env.example` (값 없는 변수 이름만) | 반영 |
| 테스트 | `finflow/tests/` | 반영 |
| 금융용어 생성 | `finflow/scripts/terms/`, `finflow/requirements-terms.txt` | [PR #6의 실행 방법과 한계](../finflow/scripts/terms/README.md) |

비밀 값이 있는 `.env`, ADC·서비스 계정 JSON, 개인 정보가 포함된 원본 로그, 가상환경과 캐시, Airflow 런타임(`airflow.cfg`, 메타 DB, 로그)은 제외했습니다. 원본 PDF, 기사, 강의 ZIP은 재배포 조건을 확인하기 전에는 저장소에 복제하지 않습니다. 벡터 DB 바이너리는 Git에 넣지 않고 GCS 스냅샷으로 관리합니다.

## 재현 기준과 현재 상태

| 재현 기준 | 상태 |
|---|---|
| 1. 빈 환경에서 의존성 설치와 설정 파일 준비 | 절차는 문서화됨. 새 가상환경에서 처음부터 설치해 보지는 않음 |
| 2. 원본 출처 → 정제 → 청킹 → 임베딩 → ChromaDB count 재현 | 뉴스는 2026-11-26 Cloud Run Job 실행 성공 기록이 있습니다. 금융용어는 오프라인 경로로 용어 770개와 청크 1,121개를 확인했습니다. |
| 3. 정상·모호·근거 부족 질문으로 검색 근거와 답변 확인 | 운영 앱 확인은 정상 질문 1건입니다. 고정 질문 세트는 [평가 CLI](../finflow/scripts/rag/README.md)로 실행하며, 실제 실행 범위는 [PR #13](https://github.com/gawounnx/finflow/pull/13)에 기록했습니다. |
| 4. 같은 뉴스 입력을 두 번 실행해 중복 적재 여부 확인 | 과거 기록만 있음(신규 0건). 재실행하지 않음 |
| 5. 컨테이너 빌드 후 PORT, Secret, 데이터 로딩 확인 | 2026-11-26 Cloud Build로 빌드해 Cloud Run에 배포, 태그 URL에서 화면과 데이터 로딩 확인 |
| 6. 공개 앱에서 같은 질문을 다시 실행하고 버전·시간·결과 기록 | 일부 확인. 리비전 `00010-buq`에서 질문 1건과 응답 시간(약 35초) 기록. 같은 질문 반복 비교는 미실행 |
| 데이터 준비 상태 판단, 스냅샷 압축 해제 보안, 화면 상태 | 자동 테스트 통과 (`finflow/tests/`, 2026-11-26 기준 185개) |

개발 환경에는 Python 3.13 실습, Python 3.12 앱, Python 3.11 Airflow 기록이 섞여 있습니다. 하나의 버전으로 모두 실행된다고 가정하지 않습니다.
