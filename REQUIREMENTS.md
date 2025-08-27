# Project Requirements & Setup Guide

이 문서는 `ares-backend` 프로젝트의 개발 환경을 설정하고 실행하는 전체 과정을 안내합니다. 개발을 처음 시작하는 분들도 쉽게 따라 하실 수 있도록 각 단계에 대한 상세한 설명을 포함하고 있습니다.

## 0. 프로젝트 복제 (Clone)

가장 먼저, GitHub에 있는 프로젝트 소스 코드를 여러분의 컴퓨터로 복제(Clone)해야 합니다. 터미널(Terminal)을 열고 원하는 디렉토리로 이동한 후, 아래 명령어를 실행하세요.

```bash
git clone https://github.com/project-ares-interview/ares-backend.git
```

명령어가 성공적으로 실행되면 `ares-backend`라는 이름의 디렉토리가 생성됩니다. 이 디렉토리로 이동하여 다음 단계들을 진행합니다.

```bash
cd ares-backend
```

## 1. 개요

본 프로젝트는 Python과 Django 프레임워크를 기반으로 하는 RESTful API 서버입니다.

프로젝트의 목표는 모든 개발자가 동일하고 안정적인 환경에서 효율적으로 작업하는 것입니다. 이를 위해 특정 버전의 프로그래밍 언어와 도구를 사용하며, `mise`, `uv`, `dotenvx`와 같은 도구를 통해 개발 환경을 일관되게 관리합니다.

---

## 2. 필수 도구 설치

프로젝트를 시작하기 위해 먼저 세 가지 핵심 도구를 설치해야 합니다.

### 2.1. `mise` (런타임 매니저)

#### `mise`란 무엇인가요?
`mise`는 여러 프로젝트에서 사용하는 다양한 버전의 프로그래밍 언어(예: Python 3.11, Node.js 20)나 개발 도구(예: uv, poetry)를 관리해 주는 도구입니다. 이 도구를 사용하면 "제 컴퓨터에서는 잘 됐는데..."와 같은 문제를 방지하고 모든 팀원이 동일한 버전의 도구를 사용하도록 보장할 수 있습니다.

#### 플랫폼별 설치 방법

-   **macOS / Linux (Homebrew 사용 시)**
    ```bash
    brew install mise
    ```

-   **Linux / macOS (Shell 스크립트 사용 시)**
    ```bash
    curl https://mise.run | sh
    ```
    설치 후, 셸 설정 파일(`.zshrc`, `.bashrc` 등)에 `mise`를 활성화하는 라인을 추가하라는 안내가 나올 수 있습니다. 안내에 따라 설정해주세요.

-   **Windows (PowerShell 사용 시)**
    ```bash
    # 셋 중 하나의 명령어를 사용하여 설치
    winget install jdx.mise

    scoop install mise

    choco install mise
    ```
    *참고: Windows에서는 `mise`가 아직 실험적인 기능일 수 있습니다. 문제가 발생하면 [공식 문서](https://mise.jdx.dev/getting-started.html)를 참고하세요.*

#### Windows PowerShell 설정 (필수)
`mise`를 설치한 후, PowerShell을 열 때마다 `mise`가 자동으로 실행되도록 설정해야 합니다. 
이 설정을 통해 `mise`가 관리하는 도구(Python, uv 등)들을 PowerShell에서 직접 사용할 수 있게 됩니다.

1.  **사용자 환경 변수 작성**:
    PowerShell을 열고 아래 명령어를 입력하여 프로필 파일을 메모장으로 엽니다. 프로필 파일이 없다면 새로 만들어집니다.
    ```powershell
    $shimPath = "$env:USERPROFILE\AppData\Local\mise\shims"
    $currentPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $newPath = $currentPath + ";" + $shimPath
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    ```


2.  **PowerShell 재시작**:
    설정을 적용하기 위해 열려있는 모든 PowerShell 창을 닫고 새로 엽니다. 이제 `mise`가 관리하는 `python`이나 `uv` 같은 명령어를 터미널에서 바로 사용할 수 있습니다.

### 2.2. `uv` (Python 프로젝트 및 의존성 관리자)

#### `uv`란 무엇인가요?
`uv`는 Rust로 작성된 매우 빠른 Python 패키지 설치 및 관리 도구입니다. 기존의 `pip`과 `venv`를 합친 것과 같으며, 훨씬 더 나은 성능을 제공합니다.

#### 설치 방법
`uv`는 앞서 설치한 `mise`를 통해 설치하고 관리합니다. 별도의 설치 명령어는 필요하지 않으며, 다음 "3. 프로젝트 설정" 단계에서 `mise`가 자동으로 감지하여 설치해 줍니다.

---

## 3. 프로젝트 설정

필수 도구를 모두 설치했다면, 이제 프로젝트 코드를 실행할 준비를 합니다.

### 3.1. 런타임 및 도구 활성화

프로젝트 루트 디렉토리에서 아래 명령어를 실행하면, `mise`가 `mise.toml` 파일을 읽어 이 프로젝트에 필요한 정확한 버전의 Python과 `uv`를 자동으로 설치하고 설정해 줍니다.

```bash
mise install
```
> 💡 **Tip**: `mise`를 셸과 통합(`mise activate zsh` 등)하면, 디렉토리 이동 시 자동으로 필요한 도구들을 활성화해주어 `mise install` 명령어를 매번 실행할 필요가 없습니다.

**버전 확인**

설정이 완료되었는지 확인하기 위해 각 도구의 버전을 출력해봅니다.
```bash
uv --version
# 예상 출력: uv 0.8.13
```

### 3.2. 의존성 설치 및 가상 환경 생성

`uv`를 사용하여 프로젝트 실행에 필요한 모든 라이브러리(Django, DRF 등)를 설치합니다. `uv sync` 명령어는 이 프로젝트만을 위한 격리된 Python 환경(**가상 환경**)을 자동으로 만들고, `pyproject.toml` 파일에 정의된 모든 라이브러리를 그 안에 설치합니다.
```bash
uv sync
```

### 3.3. 가상 환경 활성화

`uv sync`를 통해 생성된 가상 환경은 `uv run` 명령어를 사용하면 자동으로 활성화되어 내부의 파이썬이나 라이브러리를 실행해 줍니다.

하지만, `python manage.py makemigrations`처럼 가상 환경 내에서 직접 명령어를 실행하고 싶을 때가 있습니다. 이럴 때는 아래 명령어를 사용하여 현재 터미널 세션에서 가상 환경을 수동으로 활성화할 수 있습니다. `uv venv` 명령어는 사용 중인 운영체제(OS)에 맞춰 자동으로 올바른 활성화 스크립트를 실행해주는 편리한 명령어입니다.
```bash
uv venv
```

성공적으로 실행되면, 터미널 프롬프트(명령어를 입력하는 줄) 앞에 `(.venv)`와 같은 표시가 나타나며, 이는 가상 환경이 활성화되었음을 의미합니다.

> 📖 **참고: 플랫폼별 직접 활성화 명령어**
> `uv venv` 명령어 대신 각 운영체제에 맞는 스크립트를 직접 실행하여 가상 환경을 활성화할 수도 있습니다. `uv`는 프로젝트 루트에 `.venv`라는 이름의 디렉토리를 생성합니다.
>
> - **macOS / Linux (bash, zsh):**
>   ```bash
>   source .venv/bin/activate
>   ```
> - **Windows (Command Prompt):**
>   ```cmd
>   .venv\Scripts\activate.bat
>   ```
> - **Windows (PowerShell):**
>   ```powershell
>   .venv\Scripts\Activate.ps1
>   ```
>   > **PowerShell 스크립트 실행 권한 설정**
>   >
>   > PowerShell은 기본적으로 보안을 위해 스크립트(.ps1) 파일 실행을 제한할 수 있습니다. 만약 위 `Activate.ps1` 명령어 실행 시 오류가 발생한다면, 아래 명령어를 PowerShell에 **한 번만** 실행하여 현재 사용자에 대한 스크립트 실행 권한을 허용해야 합니다.
>   > ```powershell
>   > Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
>   > ```
>   > **명령어 설명:**
>   > - `Set-ExecutionPolicy`: PowerShell의 스크립트 실행 정책을 변경합니다.
>   > - `-ExecutionPolicy RemoteSigned`: 로컬 컴퓨터에서 직접 작성한 스크립트는 실행을 허용하고, 인터넷에서 다운로드한 스크립트는 신뢰할 수 있는 게시자가 서명한 경우에만 실행을 허용하는 정책입니다. 개발 환경에서 일반적으로 사용되는 안전한 설정입니다.
>   > - `-Scope CurrentUser`: 이 정책을 시스템 전체가 아닌 **현재 로그인한 사용자**에게만 적용합니다. 관리자 권한 없이 설정할 수 있으며 다른 사용자에게 영향을 주지 않습니다.

> **비활성화**: 가상 환경에서 빠져나오고 싶을 때는 `deactivate` 명령어를 입력하면 됩니다.

---

## 4. 환경 변수 관리 (dotenvx)

`dotenvx`는 API 키, 데이터베이스 비밀번호와 같이 민감하고 중요한 정보를 코드와 분리하여 안전하게 관리하는 도구입니다. 개발, 테스트, 프로덕션 등 다양한 환경에 맞는 설정을 쉽게 관리하고, 민감한 정보를 암호화하여 안전하게 팀원들과 공유할 수 있습니다.

### 4.1. 설치 방법

-   **macOS / Linux (Homebrew 사용 시)**
    ```bash
    # dotenvx/brew tap을 사용하여 설치
    brew install dotenvx/brew/dotenvx
    ```

-   **Linux / macOS (Shell 스크립트 사용 시)**
    ```bash
    curl -sfS https://dotenvx.sh | sh
    ```

-   **Windows (winget 사용 시)**
    ```bash
    winget install dotenvx
    ```

> 📖 **참고**: 더 많은 설치 방법은 [공식 설치 가이드](https://dotenvx.com/docs/install)에서 확인하실 수 있습니다.

### 4.2. 개념 이해

-   `.env`: 가장 기본적인 환경 변수 파일입니다. 개발 환경의 기본값이나 모든 환경에 공통적으로 적용되는 변수를 저장합니다.
-   `.env.{environment}` (예: `.env.development`, `.env.production`): 특정 환경을 위한 파일입니다. `production` 환경의 데이터베이스 정보처럼, 특정 환경에서만 사용되는 값을 저장합니다.
-   **암호화**: `dotenvx`를 사용하면 환경 변수를 파일에 암호화하여 저장할 수 있습니다. 암호화된 파일에는 암호화를 위한 **공개 키(Public Key)**가 함께 저장됩니다. 이 파일은 Git에 커밋해도 안전합니다.
-   `.env.keys`: 환경 변수를 복호화(해독)하는 데 필요한 **비밀 키(Private Key)**를 저장하는 파일입니다. 이 파일은 **절대로 Git에 공유하거나 커밋해서는 안 됩니다.** 이 파일이 유출되면 암호화된 환경 변수가 모두 노출될 수 있습니다.

### 4.3. 사용 방법

#### 1. 환경별 파일 생성 및 변수 추가

각 환경에 맞는 `.env` 파일을 생성합니다.

```
# .env.development 파일
DEBUG=True
DATABASE_URL="sqlite:///db.sqlite3"
```

```
# .env.production 파일
DEBUG=False
DATABASE_URL="postgresql://user:password@host:port/db"
```

#### 2. 민감 정보 암호화

`dotenvx set` 명령어를 사용하여 민감한 정보를 안전하게 암호화하고 파일에 추가할 수 있습니다.

```bash
# .env.production 파일의 DATABASE_URL 변수를 암호화하여 설정
dotenvx set DATABASE_URL "postgresql://user:password@host:port/db" -f .env.production
```

이 명령어를 처음 실행하면, 암호화에 필요한 키가 생성됩니다.
-   `.env.{environments}` 파일에는 **공개 키**와 암호화된 `DATABASE_URL`이 저장됩니다.
-   `.env.keys` 파일에는 **비밀 키**가 저장됩니다. (이 파일은 `.gitignore`에 추가해야 합니다.)

#### 3. 애플리케이션 실행

`dotenvx run` 명령어와 `-f` 플래그를 사용하여 특정 환경의 `.env` 파일을 로드하여 애플리케이션을 실행합니다.

-   **개발 환경으로 실행**
    ```bash
    # .env.development 파일을 로드하여 실행
    dotenvx run -f .env.development -- uv run python manage.py runserver
    ```

-   **프로덕션 환경으로 실행 (또는 시뮬레이션)**
    ```bash
    # .env.production 파일을 로드하여 실행
    # dotenvx가 .env.keys를 사용해 암호화된 변수를 자동으로 복호화합니다.
    dotenvx run -f .env.production -- uv run python manage.py runserver
    ```

#### 4. 새로운 팀원 합류 시
새로운 팀원은 저장소(Git)에서 repository를 내려받습니다. 그리고 팀 리더나 동료로부터 `.env.keys` 파일을 안전한 채널(예: 1Password, 직접 전달)을 통해 전달받아 프로젝트 루트 디렉토리에 배치해야 합니다.

---

## 5. 애플리케이션 실행 요약

모든 설정이 완료되었다면, 다음 한 줄의 명령어로 개발 서버를 실행할 수 있습니다.
```bash
# 개발 환경
dotenvx run -f .env.development -- uv run python manage.py runserver

# 프로덕션 환경
dotenvx run -f .env.production -- uv run python manage.py runserver
```

### 실행 확인
서버가 성공적으로 실행되면, 웹 브라우저나 API 클라이언트(Postman 등)를 사용하여 아래 주소로 접속해 보세요.
-   **서버 상태 확인**: `http://127.0.0.1:8000/health`
-   **예제 API**: `http://127.0.0.1:8000/api/v1/examples/`

성공적으로 응답이 오면, 개발 환경 설정이 완료된 것입니다!
