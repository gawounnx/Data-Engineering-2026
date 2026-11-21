# 트러블슈팅

해결이 확인된 항목과 미확인 항목을 구분합니다.

| 현상 | 원인·판단 | 조치 | 확인 결과 |
|---|---|---|---|
| 한국은행 HTML에서 데이터 확보 어려움 | JS 렌더링 페이지의 정적 HTML 한계 | Network 응답을 확인하고 JSON 요청 사용 | 기존 README에 69건 수집 기록 |
| 공공 API 인증 오류 | 키 인코딩 방식 불일치 | Decoding 키 적용 | 기존 수집 기록; 키 값은 공개하지 않음 |
| 청크 중간에서 문장이 끊김·ETF 검색 불일치 | 고정 문자 수 기준 분할 | 문장 단위 분리 후 길이 균형 조정 | ETF 검색 성공 정성 기록; 개선율 미측정 |
| google.cloud.bigquery ImportError | 실행 환경에 의존성 누락 또는 다른 환경 사용 | 활성 환경에 패키지 설치·인터프리터 확인 | 300개 종목 및 BYC 1행 조회 |
| BigQuery 위치 불일치 | data_sample 실제 위치 US와 설정 불일치 | 메타데이터 확인 후 BQ_LOCATION=US | 금융용어 770행 적재·조회 |
| 뉴스 적재 no such field: source_type | 기존 테이블 스키마와 입력 필드 불일치 | 실습에서 테이블 재생성 후 재적재 | 최초 125건, 재실행 신규 0건 |
| Streamlit watcher의 torchvision 오류 | transformers 모듈 탐색 중 선택 의존성 import | 현재 소스와 설정에서 watcher 동작 재점검 필요 | 최종 해결 여부 미확인 |
| Airflow worker SIGSEGV | 로그만으로 단일 원인 확정 불가 | 별도 환경과 실행 방식 확인 필요 | DAG 테스트 성공은 있음; 장기 scheduler 안정성 미확인 |
| Docker COPY data 실패 | build context에서 data 부재 또는 ignore 규칙 | .dockerignore·.gcloudignore·Dockerfile 정합성 점검 | 해당 후속 빌드의 성공 재확인 필요 |

뉴스 테이블 삭제·재생성은 당시 실습의 해결 이력입니다. 실제 데이터가 있는 환경에는 그대로 적용하지 않습니다. 스키마 변경과 백업·마이그레이션을 먼저 검토해야 합니다.

재발 방지를 위해 환경별 Python 버전, requirements, 데이터셋 위치, 입력 스키마, 빌드 포함 파일을 실행 문서와 함께 고정합니다.

## 현재 코드에서 확인한 우선 수정 항목

주가 수집기 day1_stock_crawler.py의 validate_stock_payload import와 호출이 주석 처리되어 있습니다. 업로더는 검증 함수를 호출하지만 수집 직후에는 검증되지 않습니다. 수집 단계까지 품질 게이트가 활성화됐다는 주석과 실제 동작이 다르므로 최신 소스 확인 후 수정·검증해야 합니다.
