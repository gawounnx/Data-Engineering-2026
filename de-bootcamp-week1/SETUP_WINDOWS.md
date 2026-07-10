# Windows 유저 초반 세팅 가이드

1주차 실습 전 Windows 환경에서 Python, Google Cloud 인증까지 준비하는 절차입니다.

## 경로 선택

| 방식 | 권장 대상 | 터미널 | 명령 스타일 |
|------|-----------|--------|-------------|
| **WSL (권장)** | Mac/Linux와 동일한 bash 환경을 쓰고 싶은 경우 | Ubuntu (WSL) | bash (`python3`, `source .venv/bin/activate`) |
| **네이티브 Windows** | WSL 없이 PowerShell만 쓰는 경우 | PowerShell | PowerShell (`python`, `.\.venv\Scripts\Activate.ps1`) |

아래 **A. WSL** 절차를 먼저 따르는 것을 권장합니다. WSL을 쓰지 않을 때만 **B. 네이티브 Windows**를 참고하세요.

---

## A. WSL로 세팅 (권장)

WSL(Windows Subsystem for Linux)은 Windows 안에서 Ubuntu 등 Linux 배포판을 실행하는 기능입니다. Mac 가이드(`SETUP_MAC.md`)와 거의 같은 bash 명령을 그대로 사용할 수 있습니다.

### A-1. WSL 설치

**관리자 권한 PowerShell**을 열고 아래를 실행합니다.

```powershell
wsl --install
```

- 기본적으로 Ubuntu가 설치됩니다.
- 설치 후 PC를 재부팅합니다.
- 재부팅 뒤 Ubuntu가 자동으로 열리면 Linux 사용자 이름과 비밀번호를 설정합니다.

이미 WSL이 설치되어 있으면 배포판 목록을 확인합니다.

```powershell
wsl --list --verbose
```

Ubuntu가 없으면 Microsoft Store에서 **Ubuntu**를 설치하거나 아래를 실행합니다.

```powershell
wsl --install -d Ubuntu
```

이후 실습은 **Ubuntu(WSL) 터미널**에서 진행합니다. Windows Terminal에서 `Ubuntu` 탭을 열거나, 시작 메뉴에서 Ubuntu를 실행하면 됩니다.

### A-2. 필수 확인 (WSL / bash)

#### 터미널

```bash
pwd
```

현재 위치가 출력되면 정상입니다.

#### Python

Python 3.11 이상을 권장합니다.

```bash
python3 --version
```

Python이 없으면 Ubuntu 패키지로 설치합니다.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 --version
```

#### uv

uv는 Python 가상환경 생성과 패키지 설치를 빠르게 처리하는 도구입니다.

공식 설치 문서:
- https://docs.astral.sh/uv/getting-started/installation/

설치:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치 후 새 WSL 터미널을 열고 확인합니다.

```bash
uv --version
```

`uv: command not found`가 나오면 셸 설정을 다시 읽습니다.

```bash
source $HOME/.local/bin/env
uv --version
```

#### Google Cloud CLI

Google Cloud CLI는 GCP 인증, Cloud Storage 업로드, BigQuery 적재에 사용합니다.

공식 설치 문서:
- https://cloud.google.com/sdk/docs/install

WSL(Ubuntu)에서는 Linux 설치 절차를 따릅니다.

```bash
curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz
tar -xf google-cloud-cli-linux-x86_64.tar.gz
./google-cloud-sdk/install.sh
```

설치 중 `PATH` 추가 여부를 묻는 질문이 나오면 `Y`를 입력합니다.
설치가 끝나면 새 WSL 터미널을 열고 확인합니다.

```bash
gcloud --version
```

초기 설정:

```bash
gcloud init
```

브라우저가 열리면 사용할 Google 계정으로 로그인하고, 수업에서 사용할 GCP 프로젝트를 선택합니다.
(WSL2에서는 Windows 기본 브라우저가 자동으로 열리는 경우가 많습니다. 열리지 않으면 터미널에 표시되는 URL을 복사해 브라우저에 붙여넣습니다.)

### A-3. 1주차 폴더 이동 (WSL)

repo를 WSL 홈 디렉터리 아래에 두는 것을 권장합니다. `/mnt/c/...` Windows 경로에서 작업하면 파일 I/O가 느려질 수 있습니다.

**방법 1 — WSL 홈으로 복사 (권장)**

Windows에서 repo가 `C:\Users\본인이름\workspace\DE-2026`에 있다면:

```bash
mkdir -p ~/workspace
cp -r /mnt/c/Users/본인이름/workspace/DE-2026 ~/workspace/
cd ~/workspace/DE-2026/1주차
```

**방법 2 — Windows 경로에서 바로 작업**

```bash
cd /mnt/c/Users/본인이름/workspace/DE-2026/1주차
```

경로에 한글이 포함되어 문제가 생기면 영문 경로로 복사해 실행합니다.

```bash
mkdir -p ~/de-bootcamp
cp -r /mnt/c/Users/본인이름/workspace/DE-2026/* ~/de-bootcamp/
cd ~/de-bootcamp/1주차
```

### A-4. Python 가상환경 준비 (WSL)

```bash
uv venv
source .venv/bin/activate
uv pip install -r scripts/requirements.txt
```

가상환경이 켜지면 프롬프트 앞에 `(.venv)`가 표시됩니다.

### A-5. 1일차 API 수집 실행 (WSL)

```bash
python scripts/day1_requests_crawler.py --output data/raw_posts.json
```

정상 실행 기준:
- `data/raw_posts.json` 파일이 생성 또는 갱신됩니다.
- 로그에 `[품질 검증 통과]`, `[완료] ...건 수집` 메시지가 보입니다.

### A-6. GCP 인증 준비 (WSL)

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

### A-7. GCP 업로드 실행 (WSL)

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

### A-8. WSL에서 자주 나는 오류

#### `python3: command not found`

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 --version
```

#### `uv: command not found`

설치 후 새 터미널을 열었는지 확인합니다.

```bash
source $HOME/.local/bin/env
which uv
uv --version
```

그래도 찾지 못하면 uv 설치 명령을 다시 실행합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### 패키지 import 오류

가상환경을 켠 뒤 의존성을 다시 설치합니다.

```bash
source .venv/bin/activate
uv pip install -r scripts/requirements.txt
```

#### GCP 인증 파일 없음

```bash
gcloud auth application-default login
ls ~/.config/gcloud/application_default_credentials.json
```

#### `gcloud: command not found`

설치 스크립트를 다시 실행하고 `PATH` 추가 질문에 `Y`를 입력합니다.

```bash
./google-cloud-sdk/install.sh
```

#### `/mnt/c/...` 경로에서 느리거나 권한 오류

repo를 WSL 홈(`~/workspace/...`)으로 복사한 뒤 다시 실행합니다.

---

## B. 네이티브 Windows (PowerShell)

WSL 없이 PowerShell만 사용할 때의 절차입니다.

### B-1. 필수 확인

#### PowerShell

Windows 기본 PowerShell 또는 Windows Terminal을 사용합니다.

```powershell
Get-Location
```

현재 위치가 출력되면 정상입니다.

#### Python

Python 3.11 이상을 권장합니다.

```powershell
python --version
```

Python이 없으면 python.org 설치 파일로 설치합니다. 설치 중 `Add python.exe to PATH` 옵션을 체크합니다.

#### uv

공식 설치 문서:
- https://docs.astral.sh/uv/getting-started/installation/

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

설치 후 새 PowerShell을 열고 확인합니다.

```powershell
uv --version
```

#### Google Cloud CLI

공식 설치 문서:
- https://cloud.google.com/sdk/docs/install

설치 절차:
1. 공식 문서의 Windows 섹션에서 `Google Cloud CLI installer`를 다운로드합니다.
2. 다운로드한 설치 파일을 실행합니다.
3. 설치 마법사의 기본 옵션을 사용합니다.
   - Python 포함 설치 옵션은 그대로 둡니다.
   - 설치 중 PATH 설정 옵션이 나오면 활성화합니다.
4. 설치가 끝나면 새 PowerShell을 열고 확인합니다.

```powershell
gcloud --version
```

5. 초기 설정을 실행합니다.

```powershell
gcloud init
```

### B-2. 1주차 폴더 이동

```powershell
cd .\1주차
```

경로에 한글이 포함되어 파일 경로 문제가 생기면 repo를 영문 경로로 복사해 실행합니다.

```powershell
mkdir C:\de-bootcamp
Copy-Item -Recurse .\* C:\de-bootcamp\
cd C:\de-bootcamp\1주차
```

### B-3. Python 가상환경 준비

```powershell
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -r .\scripts\requirements.txt
```

가상환경이 켜지면 PowerShell 프롬프트 앞에 `(.venv)`가 표시됩니다.

실행 정책 오류가 나면 현재 PowerShell 세션에만 허용합니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### B-4. 1일차 API 수집 실행

```powershell
python .\scripts\day1_requests_crawler.py --output .\data\raw_posts.json
```

정상 실행 기준:
- `data\raw_posts.json` 파일이 생성 또는 갱신됩니다.
- 로그에 `[품질 검증 통과]`, `[완료] ...건 수집` 메시지가 보입니다.

### B-5. GCP 인증 준비

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project your-project-id
```

Windows에서 인증 파일은 보통 아래 경로에 생성됩니다.

```powershell
$env:APPDATA\gcloud\application_default_credentials.json
```

### B-6. GCP 업로드 실행

```powershell
$env:GCP_PROJECT_ID = "your-project-id"
$env:GCP_BUCKET = "your-bucket"

python .\scripts\day2_upload_to_gcp.py `
  --source .\data\raw_posts.json `
  --bucket $env:GCP_BUCKET `
  --gcs-object week1/raw_posts.json `
  --project-id $env:GCP_PROJECT_ID `
  --dataset bootcamp
```

### B-7. 네이티브 Windows에서 자주 나는 오류

#### Python 명령을 찾을 수 없음

```powershell
where.exe python
python --version
```

Python 설치 후 새 PowerShell을 열어야 PATH가 반영됩니다.

#### 가상환경 활성화 오류

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

#### uv 명령을 찾을 수 없음

```powershell
where.exe uv
uv --version
```

#### 패키지 import 오류

```powershell
.\.venv\Scripts\Activate.ps1
uv pip install -r .\scripts\requirements.txt
```

#### gcloud 명령을 찾을 수 없음

```powershell
where.exe gcloud
gcloud --version
```

---

## 공통: BigQuery 권한 오류

GCP 콘솔에서 다음을 확인합니다.
- 결제 계정 연결
- BigQuery API 활성화
- Cloud Storage API 활성화
- 대상 버킷 존재 여부
- 현재 로그인 계정의 프로젝트 권한
