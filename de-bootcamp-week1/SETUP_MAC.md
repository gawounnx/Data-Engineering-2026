# Mac 유저 초반 세팅 가이드

1주차 실습 전 Mac 환경에서 터미널, Python, Google Cloud 인증까지 준비하는 절차입니다.

## 1. 필수 확인

### 터미널
Mac 기본 `Terminal` 앱을 사용합니다.

```bash
pwd
```

현재 위치가 출력되면 정상입니다.

### Python
Python 3.11 이상을 권장합니다.

```bash
python3 --version
```

Python이 없으면 Homebrew 또는 python.org 설치 파일로 Python을 설치합니다.

### uv
uv는 Python 가상환경 생성과 패키지 설치를 빠르게 처리하는 도구입니다.

공식 설치 문서:
- https://docs.astral.sh/uv/getting-started/installation/

설치:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치 후 새 터미널을 열고 확인합니다.

```bash
uv --version
```

### Google Cloud CLI
Google Cloud CLI는 GCP 인증, Cloud Storage 업로드, BigQuery 적재에 사용합니다.

공식 설치 문서:
- https://cloud.google.com/sdk/docs/install

설치 절차:
1. 터미널에서 Mac 칩 종류를 확인합니다.

```bash
uname -m
```

2. 공식 문서의 macOS 섹션에서 본인 Mac에 맞는 압축 파일을 다운로드합니다.
   - Apple Silicon(M1/M2/M3): ARM64 / Apple silicon
   - Intel Mac: x86_64
3. 다운로드한 `.tar.gz` 파일을 홈 폴더 또는 원하는 위치에 압축 해제합니다.
4. 압축 해제된 폴더에서 설치 스크립트를 실행합니다.

```bash
./google-cloud-sdk/install.sh
```

5. 설치 중 `PATH` 추가 여부를 묻는 질문이 나오면 `Y`를 입력합니다.
6. 설치가 끝나면 새 터미널을 열고 확인합니다.

```bash
gcloud --version
```

7. 초기 설정을 실행합니다.

```bash
gcloud init
```

브라우저가 열리면 사용할 Google 계정으로 로그인하고, 수업에서 사용할 GCP 프로젝트를 선택합니다.

## 2. 1주차 폴더 이동

```bash
cd 1주차
```

경로에 한글이 포함되어 문제가 생기면 repo를 영문 경로 아래로 복사해 실행합니다.

## 3. Python 가상환경 준비

```bash
uv venv
source .venv/bin/activate
uv pip install -r scripts/requirements.txt
```

가상환경이 켜지면 터미널 프롬프트 앞에 `(.venv)`가 표시됩니다.

## 4. 1일차 API 수집 실행

```bash
python scripts/day1_requests_crawler.py --output data/raw_posts.json
```

정상 실행 기준:
- `data/raw_posts.json` 파일이 생성 또는 갱신됩니다.
- 로그에 `[품질 검증 통과]`, `[완료] ...건 수집` 메시지가 보입니다.

## 5. GCP 인증 준비

2일차 GCS/BigQuery 실습 전 실행합니다.

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project your-project-id
```

인증 파일은 기본적으로 아래 위치에 생성됩니다.

```bash
~/.config/gcloud/application_default_credentials.json
```

## 6. GCP 업로드 실행

```bash
export GCP_PROJECT_ID=your-project-id
export GCP_BUCKET=your-bucket

python scripts/day2_upload_to_gcp.py \
  --source data/raw_posts.json \
  --bucket "$GCP_BUCKET" \
  --gcs-object week1/raw_posts.json \
  --project-id "$GCP_PROJECT_ID" \
  --dataset bootcamp
```

## 7. 자주 나는 오류

### Python 명령을 찾을 수 없음

아래 명령으로 설치 여부와 경로를 확인합니다.

```bash
which python3
python3 --version
```

### uv 명령을 찾을 수 없음

설치 후 새 터미널을 열었는지 확인합니다.

```bash
which uv
uv --version
```

그래도 찾지 못하면 uv 설치 명령을 다시 실행합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 패키지 import 오류

가상환경을 켠 뒤 의존성을 다시 설치합니다.

```bash
source .venv/bin/activate
uv pip install -r scripts/requirements.txt
```

### GCP 인증 파일 없음

아래 명령을 다시 실행합니다.

```bash
gcloud auth application-default login
ls ~/.config/gcloud/application_default_credentials.json
```

### BigQuery 권한 오류

GCP 콘솔에서 다음을 확인합니다.
- 결제 계정 연결
- BigQuery API 활성화
- Cloud Storage API 활성화
- 대상 버킷 존재 여부
- 현재 로그인 계정의 프로젝트 권한

### gcloud 명령을 찾을 수 없음

설치 후 새 터미널을 열었는지 확인합니다.

```bash
which gcloud
gcloud --version
```

그래도 찾지 못하면 설치 스크립트를 다시 실행하고 `PATH` 추가 질문에 `Y`를 입력합니다.
