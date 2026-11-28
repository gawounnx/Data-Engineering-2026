# FinFlow

금융 뉴스를 읽다가 낯선 용어를 만나면 뜻을 찾아 다시 기사로 돌아와야 합니다. FinFlow는 금융 입문자가 질문하면 한국은행 금융용어를 바탕으로 뜻을 설명하고, 관련 뉴스의 맥락과 출처를 함께 보여주는 차가원의 개인 프로젝트입니다.

## 핵심 기능

- **금융용어 설명** — “기준금리가 뭐야?” 같은 질문에 용어 근거를 찾아 쉽게 설명합니다.
- **관련 뉴스 검색** — 질문에 맞는 최근 7일 기사에서 제목, 요약, 날짜와 원문 링크를 제공합니다. 금통위 결정과 직후 환율을 묻는 질문에는 대상·사건·시점이 일치하는 직접 보도를 우선합니다.
- **근거 확인** — 답변과 함께 검색된 금융용어와 기사를 확인할 수 있습니다.

## 구조

| 영역 | 흐름과 코드 |
| --- | --- |
| 앱·검색 | Streamlit → 질문 분석 → 금융용어·뉴스 검색 및 용어 재정렬 → Gemini 답변과 출처 표시. [앱과 실행 환경](finflow/README.md) |
| 뉴스 갱신 | Cloud Scheduler → Cloud Run Job → 네이버 뉴스 수집·검증 → GCS·BigQuery → 청킹·임베딩 → ChromaDB → GCS 스냅샷. [뉴스 파이프라인](finflow/scripts/pipelines/run_news_pipeline.py) |
| 금융용어 색인 | 한국은행 PDF → 추출·파싱·정제 → 청킹(BigQuery 조회 또는 정제 JSON) → 임베딩 → ChromaDB. [금융용어 재현](finflow/scripts/terms/README.md) |

앱과 뉴스 Job은 GCS 스냅샷으로 색인을 공유합니다. 뉴스 Job은 금융용어 컬렉션을 보존하고 뉴스 컬렉션을 갱신합니다. [아키텍처와 기술 결정](docs/architecture.md)

## 검증

- **오프라인 회귀 테스트** — 최신 `main`에 병합하기 전 PR #6에서 앱·뉴스·스냅샷과 금융용어 파이프라인 테스트 198건 통과. [PR #6](https://github.com/gawounnx/finflow/pull/6)
- **금융용어 재현** — PR #6에서 원본 PDF로 용어 770건과 ChromaDB 청크 1,121건을 재현했습니다. BigQuery 적재는 dry-run으로 확인했습니다. [재현 결과와 미검증 범위](finflow/scripts/terms/README.md#4-검증-방법과-결과)
- **응답 평가** — 앱과 같은 RAG 함수로 실행하는 CLI와 18문항 세트가 있습니다. 최종 PR 브랜치에서는 실제 Gemini로 3문항을 실행해 답변과 검색 근거를 대조했습니다. 자동 기준은 기대 검색 근거와 답변 표현의 충족 여부를 측정합니다. [평가 실행과 재현](finflow/scripts/rag/README.md), [검증 결과](https://github.com/gawounnx/finflow/pull/13)
- **운영 확인** — 2026년 11월 26일 운영 앱 질문 1건의 답변과 출처를 확인했습니다. [운영 확인 기록](docs/evaluation.md)

사용자 수요와 이해도는 아직 측정하지 않았습니다.

## 개인 기여

금융용어 PDF 파싱·정제와 색인 생성, 금융용어·뉴스 검색과 출처 표시, 뉴스 수집·증분 갱신, Streamlit 화면, 스냅샷 복구와 Cloud Run 배포를 구현했습니다. 재현 스크립트, 회귀 테스트, 평가 기록으로 확인할 수 있는 작업 범위를 남겼습니다. [개인 기여와 출처](docs/contribution-and-roadmap.md), [개발 이력](docs/development-history.md)

## 실행

Python 3.12와 GCS 스냅샷 읽기 권한을 준비합니다. `.env`에는 프로젝트에서 사용하는 Gemini 설정을 입력합니다.

```bash
cd finflow
python -m pip install -r requirements-dev.txt
cp .env.example .env
python scripts/vectorstores/download_chroma_snapshot.py
streamlit run app_ui.py
```

2026년 11월 26일 배포를 확인한 [공개 앱](https://finflow-app-742422896041.asia-northeast3.run.app)과 화면 검증을 기록한 [PR #11](https://github.com/gawounnx/finflow/pull/11)을 확인할 수 있습니다. 데이터 준비, 테스트, 컨테이너 실행은 [실행·재현 안내](docs/reproduction.md)를 참고합니다.

## 학습 배경과 참고 자료

BDAI 데이터 엔지니어링 부트캠프에서 배운 수집·적재·검색 흐름을 금융 입문자용 서비스로 확장했습니다. 용어 원본은 한국은행 「2026 경제금융용어 800선」, 기사 수집은 네이버 뉴스 검색 API를 사용합니다. [학습·자료 출처](docs/contribution-and-roadmap.md), [금융용어 입력 준비](finflow/scripts/terms/README.md#1-입력-준비)
