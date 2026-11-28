# RAG 평가와 뉴스 검색 재현

FinFlow 프로젝트 디렉터리에서 실행한다. 금융용어·뉴스가 함께 든 ChromaDB와 Python 환경을 준비한다. 실제 평가에는 프로젝트의 Gemini 설정도 필요하다. [프로젝트 데이터 준비와 설정](../../README.md)

## 실제 Gemini 평가

```sh
python -m scripts.rag.evaluate --help
python -m scripts.rag.evaluate --cases scripts/rag/evaluation_cases.json > finflow-evaluation.json
```

CLI는 앱과 같은 `src.rag.ask_finflow()`를 기본값으로 호출한다. 기본 세트는 정의·비교·뉴스·모호한 질문·근거 부족 사례를 합쳐 18문항이다. 질문 분석, 재정렬, 답변 생성은 각각 Gemini를 호출할 수 있다. JSON에는 질문별 자동 판정, 실패 이유와 실행 오류, 응답 시간, 실제 답변, 검색 근거가 들어간다. 종료 코드는 전체 통과 `0`, 기준 미충족 또는 실행 오류 `1`, 입력 오류 `2`다. [질문 세트](evaluation_cases.json)

자동 판정은 기대 용어, 답변 표현 그룹, 뉴스 주제어 조건의 충족 여부를 나타낸다. 실제 결정이나 관측에 근거했는지, 모호한 전제를 밝혔는지, 유보가 적절했는지는 문항의 `manual_review`에 따라 답변과 검색 근거를 대조한다.

## 저장된 검색어로 오프라인 재생

기록의 세대 `1795834875310250`과 파일 해시가 일치하는 로컬 색인 및 캐시된 `kekeappa/kor-static-embedding-128` 모델을 사용한다. 스크립트는 뉴스 검색만 호출하며, 색인을 임시 디렉터리에 복제하고 저장된 검색어와 시각으로 상위 5건을 출력한다. [평가 집계·질문 해시·색인 해시·저장 검색어](evaluation_record.json)

```sh
HF_HUB_OFFLINE=1 ANONYMIZED_TELEMETRY=False python -m scripts.rag.replay_news --chroma data/chroma > finflow-news-replay.json
HF_HUB_OFFLINE=1 ANONYMIZED_TELEMETRY=False python -m pytest tests
```

## 기록된 검증 범위

| 검증 | 결과 |
| --- | --- |
| 2026년 11월 28일 실제 18문항 평가 | 자동 기준 13건 통과, 5건 미충족, 실행 오류 0건. 신규 11문항 중 7건 통과. |
| 뉴스 개선 도중 실제 4문항 재평가 | 4건 모두 자동 기준 통과. 응답 모델 버전 `gemini-3.8-flash`. 이 결과는 기존 로컬 코드에서 수집했다. |
| 최신 `main`에 이식한 코드의 고정 색인 재생 | 18개 저장 검색어 중 15개의 상위 5개 제목·URL·순서가 유지됐다. 기준금리 두 질문은 확정 보도가 1·2위에, 원·달러 관측 질문은 해당 장중 보도가 1위에 들었다. |
| 이식한 코드의 오프라인 테스트 | 195건과 하위 테스트 11건 통과. |

실제 평가 규모는 18문항과 뉴스 4문항이다. 최종 보완 및 최신 `main` 이식 후 검증 범위는 오프라인 테스트와 저장 검색어 재생이다. 이번 변경의 배포 상태는 미배포다.
