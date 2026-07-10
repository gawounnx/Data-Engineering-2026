# 1주차 Plan.md

## 강의 자료 (HTML 슬라이드)
- `lecture_day1.html` — 1일차: 개발 환경 · API 수집
- `lecture_day2.html` — 2일차: GCS · BigQuery 연동
- 브라우저에서 열기 · 클릭/키보드로 슬라이드 전환 · 뒤로가기 지원

## 주차 목표
- 로컬 Python 개발 환경을 안정적으로 구동한다.
- 외부 API 데이터를 수집하고 클라우드 저장소 및 DW(BigQuery)까지 적재하는 기본 파이프라인을 구축한다.

## 사전 준비 및 과금 요소
- Google Gemini API 무료 티어를 사용한다.
- GCP BigQuery 사용을 위해 해외 결제가 가능한 신용카드가 필요하다.
- OS별 초반 세팅:
  - Mac: `SETUP_MAC.md`
  - Windows: `SETUP_WINDOWS.md`

## 1일차 (3H): 개발 환경 구동 및 데이터 수집 기초
### 학습/실습 항목
- 오리엔테이션 및 로컬 Python 환경 설정
- Python 가상환경 생성 및 의존성 설치
- Python `requests`를 이용한 외부 API 호출 스크립트 작성
- API 응답(JSON) 파싱 및 로컬 파일 저장
- **데이터 품질 검증**: `record_count` 일치, 필수 필드 null 체크 (`data_quality.py`)

### 실습 산출물
- `requests` 기반 데이터 수집 스크립트
- 샘플 원본 데이터 파일(JSON/CSV)
- 품질 검증 통과 로그 (`[품질 검증 통과]`)

## 2일차 (3H): 클라우드 스토리지 및 DW 연동
### 학습/실습 항목
- GCP 프로젝트 생성 및 기본 설정(무료 티어 기준)
- Cloud Storage 버킷 생성 및 원본 데이터 업로드
- BigQuery 데이터셋/테이블 생성 및 메타데이터 적재
- 스크립트에서 Storage 업로드 + BigQuery 적재 흐름 연결
- 업로드 전 동일 품질 검증 재실행 (이중 게이트)

### 실습 산출물
- Cloud Storage 업로드 결과
- BigQuery 테이블 적재 결과

## 주간 과제
- API 수집부터 Cloud Storage/BigQuery 적재까지 동작하는 전체 스크립트 실행 결과를 캡처해 제출한다.

## 실습 체크리스트 (수강생 제출 기준)
- 로컬 Python 가상환경이 정상 실행된다.
- API 호출 성공 로그와 수집 데이터 파일이 확인된다.
- 품질 검증(`record_count`, 필수 필드) 통과 로그가 확인된다.
- Cloud Storage 업로드 객체가 확인된다.
- BigQuery 적재 레코드가 확인된다.
- 전체 실행 흐름(수집 -> 업로드 -> 적재) 캡처가 포함된다.

## 독립 실행 안내
이 주차는 **1주차 폴더만** 있으면 실습이 가능합니다. `data/raw_posts.json` 샘플 데이터가 포함되어 있습니다.

## 로컬 실행
```bash
cd 1주차

uv venv
source .venv/bin/activate
uv pip install -r scripts/requirements.txt

# day1 API 수집
python scripts/day1_requests_crawler.py --output data/raw_posts.json

# day2 GCP 업로드 (사전: gcloud auth application-default login)
export GCP_PROJECT_ID=your-project-id
export GCP_BUCKET=your-bucket
python scripts/day2_upload_to_gcp.py \
  --source data/raw_posts.json \
  --bucket "$GCP_BUCKET" \
  --gcs-object week1/raw_posts.json \
  --project-id "$GCP_PROJECT_ID" \
  --dataset bootcamp
```
