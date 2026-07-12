# Week 2 — 텍스트 전처리 · 청킹 · 임베딩

HuggingFace 페르소나 데이터를 수집하고, 청킹 후 SentenceTransformer로 벡터화합니다.

## 전체 파이프라인 흐름

```
[1회차]
HuggingFace 페르소나 데이터 (20건)
      │  day0_download_personas.py
      ▼
personas_raw.json
      │  day1_preprocess_chunk.py
      ▼
chunks.json (220청크)

[2회차]
chunks.json
      │  day2_local_embedding.py (CPU 환경, batch_size=16)
      ▼
embeddings.json (220건, dim=128)
      │  day3_query.py
      ▼
코사인 유사도 기반 쿼리 검색

[FinFlow AI 적용]
bok_financial_terms_raw.json (69건)
      │  150자 단위 청킹
      ▼
fin_terms_chunks.json (292청크)
      │  임베딩
      ▼
fin_terms_embeddings.json (292건, dim=128)
      │  fin_terms_query.py
      ▼
금융용어 의미 기반 검색
```

## 설계 포인트

**청킹 전략이 RAG 검색 품질을 결정한다**
- 처음에는 금융용어 1개 = 청크 1개로 설정 → ETF 검색 안 됨
- 150자 단위로 나누기 → 292청크 → ETF "분산투자" 내용이 별도 청크로 분리 → 검색 성공
- 긴 텍스트에서 핵심 내용이 뒷부분에 있으면 청크 단위로 나눠야 검색 가능

**임베딩 모델: kekeappa/kor-static-embedding-128**
- 한국어 특화 경량 모델, 128차원
- CPU 환경에서도 빠르게 동작 (220건 약 0.2초)
- 한계: 영어 약어(ETF)와 한국어 용어(상장지수펀드) 연결이 어려움 → 청킹으로 보완

**코사인 유사도 검색**
- 쿼리 텍스트 → 동일 모델로 벡터화
- 전체 청크와 코사인 유사도 계산
- 유사도 높은 순으로 top-k 반환
- 외부 라이브러리 없이 순수 Python으로 구현

## 스크립트 설명

| 파일 | 역할 |
|------|------|
| `day0_download_personas.py` | HuggingFace에서 페르소나 데이터 20건 다운로드 |
| `day1_preprocess_chunk.py` | 페르소나 텍스트 청킹 (220청크 생성) |
| `day2_local_embedding.py` | SentenceTransformer로 청크 임베딩 (dim=128) |
| `day3_query.py` | 코사인 유사도 기반 쿼리 검색 |
| `run_pipeline.sh` | 수집 → 청킹까지 자동 실행 |

## 실행 방법

```bash
uv venv
source .venv/bin/activate
uv pip install --torch-backend cpu -r scripts/requirements.txt

# 전체 파이프라인 (수집 + 청킹)
DOWNLOAD_LIMIT=20 bash scripts/run_pipeline.sh

# 임베딩
python scripts/day2_local_embedding.py \
  --input data/chunks.json \
  --output data/embeddings.json \
  --batch-size 16

# 쿼리 (가상환경 활성화 필수)
python scripts/day3_query.py --query "부산 사는 60대 남성" --top-k 3
```

## 검증 Q&A (과제 제출용)

| Q | A (chunk_id) |
|---|---|
| 33세 여성 경리 사무원 인천 연수구 | 250145caa9b74e4aaf073d559a61614e_chunk_0 |
| 31세 남성 강구조물 가공원 경북 영주 | 20df908dd5f74569b5d84158c9298a03_chunk_0 |
| 콜센터 상담원 원주시 40대 | 2c30571d3af64e19baac9135d37c31f6_chunk_1 |
| 부산 남구 고성호 영업원 | fdfc8a5857b4444d9ff64eac5ecbef13_chunk_0 |
| 부산 사는 60대 남성 | fdfc8a5857b4444d9ff64eac5ecbef13_chunk_0 |
| 파주 e스포츠 청년 피부관리 20대 | 9d3c430b17ee480aab5936c27724a7e1_chunk_0 |
| 마포구 26세 디지털 취향 청년 | 9012f5e074224c5f9e6bc4f225fce213_chunk_0 |
| 분당 언론학 전공 40대 여성 가족 | 16070be2ab8c44988e5d4e7878761d15_chunk_0 |
| 의정부 자가아파트 음료서비스 70대 여성 | f9ab06ba0c274bdd941a77ff39a9cc5b_chunk_0 |
| 횡성 경비원 가족 소박한 아버지 | c7d53c380549418392b5709b0cda8291_chunk_0 |

## 트러블슈팅 기록

| 증상 | 원인 | 해결 |
|------|------|------|
| `ModuleNotFoundError: sentence_transformers` | 가상환경 미활성화 | `source .venv/bin/activate` 후 실행 |
| ETF 검색 시 엉뚱한 결과 | 긴 설명 전체가 1개 청크라 핵심 내용 매칭 안 됨 | 150자 단위로 재청킹 → 292청크로 분리 |
| 쿼리 결과에 같은 사람 여러 청크 중복 출력 | 1인이 여러 청크를 가짐 | top-k 늘리거나 persona_id 기준 중복 제거 로직 추가 가능 |

## 배운 것

- 청킹 크기(chunk size)가 RAG 검색 정확도를 직접적으로 결정함
- 임베딩은 의미적 유사도를 계산 — 정확히 같은 단어가 없어도 관련 내용을 찾음
- 데이터가 적을수록(20명) 쿼리가 부정확할 수 있음 — 데이터 규모가 검색 품질에 영향
- 검증 Q&A는 데이터를 직접 보고 텍스트에 실제 나오는 단어로 질문을 만들어야 함
- sentence-transformers는 CPU 환경에서도 충분히 빠름 (--torch-backend cpu)
