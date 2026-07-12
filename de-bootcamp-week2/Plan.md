# 2주차 Plan.md

## 강의 자료 (HTML 슬라이드)
- `lecture_day1.html` — 1일차: 전처리 및 청킹
- `lecture_day2.html` — 2일차: 로컬 임베딩
- 브라우저에서 열기 · 클릭/키보드로 슬라이드 전환 · 뒤로가기 지원

## 주차 목표
- 마케팅 페르소나 텍스트 데이터를 분석 가능한 형태로 정제한다.
- 청킹 및 로컬 임베딩을 통해 벡터 데이터를 생성하고 JSON으로 저장한다.

## 참고 준비사항
- 1주차에서 준비한 Python 실행 환경을 그대로 활용한다.
- Hugging Face Hub에서 `nvidia/Nemotron-Personas-Korea` 데이터셋을 다운로드한다.
- Gemini API 및 GCP 계정은 이후 주차(4~5주차) 연계를 위해 유지한다.

## 0일차 (사전): HF 데이터 다운로드
### 학습/실습 항목
- `datasets` 라이브러리로 Hugging Face Hub 데이터셋 로드
- 전체 100만 건(약 2GB) 대신 `--limit`으로 실습용 샘플만 저장

### 실습 산출물
- `personas_raw.json` (HF 원본 샘플)

## 1일차 (3H): 타겟 데이터 전처리 및 청킹(Chunking)
### 학습/실습 항목
- Nemotron 페르소나 데이터(7개 페르소나 유형 + 인구통계 메타데이터) 구조 확인
- 결측/중복/불필요 텍스트 정리
- LLM 컨텍스트 길이와 의미 단위를 고려한 청킹 전략 설계
- 청킹 결과를 재현 가능하도록 함수화

### 실습 산출물
- 전처리 스크립트
- 청킹 결과 샘플(JSON)

## 2일차 (3H): 로컬 임베딩 실습
### 학습/실습 항목
- `sentence-transformers` 설치 및 모델 로드
- 청킹된 텍스트를 CPU 환경에서 벡터로 변환
- 텍스트/벡터/메타데이터를 하나의 구조로 정리
- 임베딩 결과를 로컬 JSON 파일로 저장

### 실습 산출물
- 임베딩 생성 스크립트
- 임베딩 결과 JSON 파일

## 주간 과제
- 제공된 페르소나 텍스트를 청킹하고 임베딩하여 JSON 형태로 변환한 결과를 제출한다.

## 실습 체크리스트 (수강생 제출 기준)
- 청킹 규칙(길이, 구분 기준)이 문서화되어 있다.
- 청킹 후 `chunk_count` 일치·빈 text 없음 품질 검증을 통과한다.
- 각 chunk에 대응되는 임베딩 벡터가 생성된다.
- 결과 JSON에 텍스트/벡터/메타데이터 필드가 포함된다.
- 로컬 CPU 환경에서 재실행 가능하다.

## 독립 실행 안내
이 주차는 **week2 folder만** 있으면 실습이 가능합니다. HF 다운로드(`day0`) 또는 `data/personas_raw.json` 샘플로 시작할 수 있습니다.

## 로컬 실행
```bash
cd week2

uv venv
source .venv/bin/activate
uv pip install --torch-backend cpu -r scripts/requirements.txt

# 전체 파이프라인 (HF 다운로드 -> 청킹 -> 임베딩)
DOWNLOAD_LIMIT=50 bash scripts/run_pipeline.sh

# 샘플 개수 조절
DOWNLOAD_LIMIT=20 bash scripts/run_pipeline.sh

# 전체 데이터 다운로드(JSONL, 파이프라인 입력용 샘플 JSON과 별도)
python scripts/day0_download_personas_full.py --output data/personas_full.jsonl
```
