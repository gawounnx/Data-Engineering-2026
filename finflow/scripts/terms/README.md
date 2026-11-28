# 금융용어 생성 파이프라인 (`financial_terms`)

FinFlow의 금융용어 검색에 쓰는 `financial_terms` 컬렉션을 만드는 7단계 스크립트입니다. 입력은 한국은행 「2026 경제금융용어 800선」 PDF이고, 출력은 ChromaDB 컬렉션입니다.

## 배경

데이터 엔지니어링 부트캠프 1주차에서 API 수집과 GCS·BigQuery 적재를 실습했습니다. 이 파이프라인은 그 흐름을 금융 데이터에 적용하고 PDF 추출, 용어 파싱, 정제, 청킹, 임베딩, ChromaDB 적재로 확장한 개인 프로젝트 작업입니다. [학습 배경과 출처](../../../docs/contribution-and-roadmap.md)

- 처음에는 한국은행 용어 API로 69건을 수집했습니다.
- 검색 대상이 너무 적어 800선 PDF로 바꿨습니다.

아래 표의 week1 경로는 이전 전의 원본 위치입니다. 실행 명령은 `finflow/`를 기준으로 합니다. 강의 자료에서 가져온 부분과 직접 작성한 부분을 줄 단위로 구분한 기록은 없습니다.

| 단계 | 스크립트 | week1 원본 |
|---|---|---|
| 1 | `extract_terms_pdf.py` | `parser/extract_financial_terms.py` |
| 2 | `parse_terms.py` | `parser/parse_financial_terms_v3.py` |
| 3 | `clean_terms.py` | `preprocessing/clean_financial_terms.py` |
| 4 | `load_terms_to_bigquery.py` | `bigquery/load_financial_terms.py` |
| 5 | `chunk_terms.py` | `chunking/create_financial_term_chunks.py` |
| 6 | `embed_terms.py` | `embedding/create_financial_term_embeddings.py` |
| 7 | `load_terms_to_chromadb.py` | `chromadb/load_financial_terms.py` |

옮기면서 바꾼 것은 경로, 환경 설정, 운영 데이터 보호 장치, 오프라인 청킹 모드입니다. 파싱·정제·청킹·임베딩 로직은 바꾸지 않았습니다.

## 1. 입력 준비

1. 의존성을 설치합니다. 앱과 뉴스 Job 이미지에는 들어가지 않습니다.
   ```bash
   pip install -r requirements-terms.txt   # requirements.txt + pdfplumber
   ```
2. 한국은행 「2026 경제금융용어 800선」 PDF를 받아 `data/terms/raw/2026_경제금융용어 800선.pdf`에 둡니다.
   - 원본 PDF는 저작권 확인 전까지 저장소에 넣지 않습니다. `data/terms/`는 `.gitignore`로 제외돼 있습니다.
3. 필요하면 환경 변수를 설정합니다(`.env.example` 참고).

| 변수 | 기본값 | 용도 |
|---|---|---|
| `FINFLOW_TERMS_DATA_DIR` | `data/terms` | 작업 폴더(`raw/`, `processed/`, `chroma/`) |
| `TERMS_PDF_PATH` | `<작업 폴더>/raw/2026_경제금융용어 800선.pdf` | 입력 PDF |
| `GCP_PROJECT_ID`, `BQ_DATASET_ID` | (필수) | 4·5단계 BigQuery 테이블 |
| `TERMS_BQ_TABLE` | `financial_terms` | 4·5단계 테이블 이름 |
| `BQ_LOCATION` | `asia-northeast3` | BigQuery 위치. week1 원본에서는 4단계가 `asia-northeast3`, 5단계가 `US`로 서로 달랐습니다 |

## 2. 실행 순서

`finflow/`에서 실행합니다. 출력은 모두 `data/terms/processed/` 아래에 생깁니다.

| 단계 | 명령 | 입력 → 출력 | 외부 접속·쓰기 |
|---|---|---|---|
| 1 | `python scripts/terms/extract_terms_pdf.py` | PDF → `financial_terms_pages.json`, `financial_terms_raw.txt` | 없음 |
| 2 | `python scripts/terms/parse_terms.py` | → `financial_terms_800_v3.json`, `financial_terms_parse_report_v3.json` | 없음 |
| 3 | `python scripts/terms/clean_terms.py` | → `financial_terms_800_v4_cleaned.json`, `financial_terms_cleaning_report_v4.json` | 없음 |
| 4 | `python scripts/terms/load_terms_to_bigquery.py` | 정제 JSON → BigQuery 테이블 | **기본 dry-run**(접속 없음). 아래 4단계 설명 참고 |
| 5 | `python scripts/terms/chunk_terms.py` | BigQuery → `financial_terms_chunks.json` | BigQuery **읽기만** |
| 5′ | `python scripts/terms/chunk_terms.py --from-cleaned-json` | 정제 JSON → `financial_terms_chunks.json` | 없음(오프라인) |
| 6 | `python scripts/terms/embed_terms.py` | → `financial_terms_embeddings.json` | 모델이 캐시에 없으면 Hugging Face Hub에서 내려받음 |
| 7 | `python scripts/terms/load_terms_to_chromadb.py` | → `data/terms/chroma`의 `financial_terms` | 로컬 파일만. 아래 7단계 설명 참고 |

### 4단계: BigQuery 덮어쓰기
- 실제 적재는 `WRITE_TRUNCATE`입니다. **대상 테이블의 기존 내용을 모두 지우고 새로 씁니다.**
- 기본 실행은 dry-run입니다. BigQuery 클라이언트를 만들지 않고 대상 테이블과 건수만 출력합니다.
- 실제로 쓰려면 대상 테이블 ID를 그대로 다시 입력해야 합니다. 입력값이 다르면 아무것도 쓰지 않고 중단합니다.
  ```bash
  python scripts/terms/load_terms_to_bigquery.py --execute \
      --confirm-table <project>.<dataset>.<table>
  ```
- 운영 테이블을 덮어쓰지 않으려면 `TERMS_BQ_TABLE`에 별도 테이블 이름(예: `financial_terms_rebuild`)을 지정합니다.

### 5′: 오프라인 청킹 모드
`--from-cleaned-json`은 4단계와 같은 변환(`normalize_records`)을 거친 행을 `term_id` 순으로 읽습니다. 5단계의 BigQuery 조회(`SELECT … ORDER BY term_id`)와 같은 열·순서를 만들기 위한 모드입니다.

### 7단계: ChromaDB 쓰기와 초기화
- 기본 저장 경로는 **서비스 DB(`data/chroma`)와 분리된 `data/terms/chroma`**입니다.
- `data/chroma`는 앱과 뉴스 Job이 GCS 스냅샷과 동기화하는 경로입니다. 여기에 쓰려면 `--chroma-path data/chroma --allow-service-db`가 필요합니다.
- 같은 이름의 컬렉션이 이미 있으면 중단합니다. `--replace`를 주면 `financial_terms`만 **삭제 후 다시 만듭니다(초기화).** `financial_news` 등 다른 컬렉션은 건드리지 않습니다.
- 이 단계는 GCS에 업로드하지 않습니다.
- 서비스에 반영하는 절차(스냅샷 내려받기 → 서비스 DB 교체 → 스냅샷 업로드)는 자동화하지 않았고 검증하지도 않았습니다.

## 3. 출력물

| 파일 | 내용 (week1 기준 결과) |
|---|---|
| `financial_terms_pages.json` | 428쪽 페이지별 텍스트 |
| `financial_terms_800_v3.json` | 용어 770건(찾아보기 789건 중 본문에서 확인된 것) |
| `financial_terms_800_v4_cleaned.json` | 정제 후 770건(190건 수정, 13건 추가 확인 필요) |
| BigQuery `financial_terms` | 770행 (`term_id`, `term`, `definition`, `source`, `source_document`, `data_version`, `content_hash`, `loaded_at`) |
| `financial_terms_chunks.json` | 청크 1,121건(문장 단위, 목표 450자, 최소 250자, 최대 600자) |
| `financial_terms_embeddings.json` | 128차원 벡터 1,121개 |
| ChromaDB `financial_terms` | 1,121건, cosine 거리 |

## 4. 검증 방법과 결과

### 오프라인 테스트
```bash
pytest tests/test_terms_pipeline.py
```
- 4단계: 기본 dry-run에서 BigQuery 클라이언트를 만들지 않는지, 확인 문자열이 없거나 다르면 쓰지 않는지, 확인되면 `WRITE_TRUNCATE`로 지정한 테이블에 쓰는지
- 5단계: 오프라인 모드의 열과 순서, BigQuery 모드가 조회만 하는지
- 7단계: 기본 경로, 서비스 DB 보호, `--replace` 없이는 기존 컬렉션을 유지하는지, `--replace`가 `financial_terms`만 초기화하는지

각 보호 장치를 일부러 끄면 해당 테스트가 실패하는 것도 확인했습니다.

### week1 결과물 재현 비교 (로컬, 2026-11-23)
원본 PDF 복사본으로 1~3, 4(dry-run), 5′, 6, 7단계를 임시 폴더에서 실행했습니다. 그 결과를 `de-bootcamp-week1/data/` 결과물과 비교했습니다.

| 단계 | 결과 |
|---|---|
| 1, 2 | 페이지 JSON, 전체 텍스트, 용어 JSON, 파싱 보고서가 바이트 단위로 같음 |
| 3 | 용어 770건이 같음. 메타데이터의 경로 문자열만 다름(원본 `data/processed/...` → 파일 이름) |
| 5′ | 청크 1,121건이 같음. 메타데이터 `source`만 오프라인 모드 표시로 다름 |
| 6 | ID와 벡터 외 필드가 같음. 벡터 최대 차이는 6e-8 |
| 7 | ID, 문서, 메타데이터, 컬렉션 설정(cosine)이 같음. 벡터 최대 차이는 6e-8. 표본 23개가 모두 자기 자신을 검색 1위로 찾음 |

실행 환경은 macOS입니다.
- 1~5단계: pdfplumber 0.11.10
- 6~7단계: FinFlow 고정 버전(chromadb 1.5.8, sentence-transformers 5.6.1), 모델은 로컬 캐시(`HF_HUB_OFFLINE=1`)

week1 ChromaDB의 `financial_terms`는 FinFlow 서비스 스냅샷의 금융용어와 ID·본문이 같습니다.

### 파서 버전 선택
week1에는 v3 파서가 두 개 있었습니다. 같은 입력 복사본으로 둘 다 실행해 비교했습니다.
- `parse_financial_terms_v3.py`(채택): 기존 `financial_terms_800_v3.json`을 그대로 재현합니다.
- `parse_financial_terms_v3_recovered_backup.py`: 8개 용어의 설명이 다르고, 그중 3개는 끝 페이지도 다릅니다. 이 파서는 각 페이지의 첫 줄을 무조건 지웁니다.
  - 7건에서는 머리글(다음 용어 이름)이 빠졌습니다.
  - `레버리지 효과`에서는 본문 문장("감내할 만한 수준 이내로 부채를 적절하게 활용하는 것이 바람직하다.")까지 빠졌습니다.

운영 데이터를 재현하고 본문 문장을 잃지 않는 `v3`를 채택했습니다.

## 5. 검증하지 않은 것과 알려진 문제

- **BigQuery 실제 적재·조회는 실행하지 않았습니다.** 4단계는 dry-run과 mock으로만 확인했습니다. 5단계 BigQuery 모드가 오프라인 모드와 같은 결과를 내는지는 SELECT 열과 정렬 기준으로만 맞췄습니다.
- **week1 당시 실제로 쓴 BigQuery 데이터셋·위치는 확인하지 않았습니다.** 원본 5단계의 위치 기본값은 `US`였습니다.
- **Linux 환경과 모델 다운로드 경로는 검증하지 않았습니다.** 다른 버전의 numpy·torch에서는 벡터가 아주 조금 다를 수 있습니다.
- **서비스 DB 교체와 GCS 스냅샷 반영 절차는 검증하지 않았습니다.**
- **머리글이 본문에 섞인 용어 7건(운영 데이터에도 있음):** `경제전망보고서`, `동아시아·외환위기`, `사이버리스크`, `역전세`, `증권커스터디서비스`, `차입매수(LBO)`, `크라우드펀딩`
  - 설명 끝에 다음 페이지 머리글(용어 이름)이 붙어 있습니다.
  - 파서를 고치면 운영 데이터와 달라지므로 이번에는 그대로 두었습니다.
- 정제 보고서의 "추가 확인 필요" 13건은 검토하지 않았습니다.
