# 실행과 재현

## 현재 실행 가능한 범위

이 저장소에는 최신 FinFlow app.py·src·배포 설정이 없습니다. 따라서 clone 후 최신 서비스가 바로 실행된다고 안내할 수 없습니다. 초기 실습의 환경 안내는 [macOS](../de-bootcamp-week1/SETUP_MAC.md), [Windows](../de-bootcamp-week1/SETUP_WINDOWS.md), [1주차](../de-bootcamp-week1/README.md), [2주차](../de-bootcamp-week2/README.md)를 참고합니다.

실행 명령은 저장소 이름 변경 여부와 무관하게 사용할 수 있도록 현재 checkout 디렉터리를 기준으로 합니다.

## 초기 수집·적재 코드 확인

전제: Python과 가상환경을 준비하고 저장소 루트에서 실행합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r de-bootcamp-week1/scripts/requirements.txt
python de-bootcamp-week1/scripts/day1_stock_crawler.py --help
python de-bootcamp-week1/scripts/day2_upload_stock_to_gcp.py --help
```

Windows 활성화는 해당 환경 안내를 따릅니다. --help는 옵션 확인이며 수집·적재 성공 검증을 대신하지 않습니다. 실제 클라우드 실행 전 API 키·ADC 인증·대상 프로젝트·버킷·데이터셋·권한을 준비합니다.

## 최신 소스 반영에 필요한 파일

과거 로컬 프로젝트 루트: ~/projects/de-bootcamp-2026  
과거 앱 디렉터리: finflow/

| 대상 | 필요 파일·확인 |
|---|---|
| 앱 | app.py, src/, utils/, styles/ 등 실제 import 대상 |
| 의존성 | requirements.txt와 Python 버전, Airflow 별도 환경 설정 |
| 데이터 생성 | PDF parser·정제·청킹·embedding·ChromaDB loader |
| 뉴스 | scripts/collectors/, scripts/loaders/, scripts/pipelines/ |
| 자동화 | dags/naver_news_pipeline_dag.py |
| 배포 | Dockerfile.app, Dockerfile.news-job, cloudbuild 설정, start_streamlit.py, ignore 규칙 |
| 설정 | 실제 코드에서 읽는 변수만 담은 .env.example |

비밀 값이 있는 .env, ADC·서비스 계정 JSON, 개인 정보가 포함된 원본 로그, 가상환경과 캐시는 제외합니다. 원본 PDF·기사·강의 ZIP은 재배포 조건 확인 없이 저장소로 복제하지 않습니다. 벡터 DB 바이너리를 Git에 넣기보다 생성 명령과 스냅샷 버전을 관리합니다.

## 최신 코드 도착 후 통과할 재현 기준

1. 빈 환경에서 의존성 설치와 설정 파일 준비
2. 원본 출처 → 정제 → 청킹 → embedding → ChromaDB count 재현
3. 정상·모호·근거 부족 질문을 실행하고 검색 근거·답변 확인
4. 같은 뉴스 입력 두 번 실행 후 중복 적재 여부 확인
5. 컨테이너 빌드 후 PORT·Secret·데이터 로딩 확인
6. 공개 앱에서 같은 질문을 다시 실행하고 버전·시간·결과 기록

각 명령은 최신 파일의 실제 CLI와 일치하게 업데이트한 뒤 공개합니다. 개발 환경에는 Python 3.13 실습, Python 3.12 앱, Python 3.11 Airflow 기록이 혼재하므로 하나의 버전으로 모두 실행된다고 가정하지 않습니다.
