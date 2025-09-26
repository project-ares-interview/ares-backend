# ARES Backend - AI Interview Coach

ARES는 AI 기반의 모의 면접 및 이력서 분석 플랫폼의 백엔드 API 서버입니다. Azure의 RAG(Retrieval-Augmented Generation) 기술을 활용하여 지원자의 역량을 심도 있게 분석하고, 맞춤형 면접 경험을 제공합니다.

## 주요 기능

- **🤖 AI 기반 모의 면접**:
  - **구조화된 면접**: `경험/역량`, `상황/케이스`, `조직 적합성` 3단계로 구성된 체계적인 면접을 진행합니다.
  - **면접관 페르소나**: '실무 리더' 또는 '임원' 모드를 선택하여 다른 관점의 질문과 피드백을 받을 수 있습니다.
  - **RAG 기반 질문 생성**: Azure Blob Storage에 저장된 최신 기업 자료(사업보고서 등)를 실시간으로 분석하여, 회사와 직무에 맞는 심도 있는 질문을 생성합니다.
- **📄 이력서 및 자소서 분석**:
  - 제출된 이력서와 자기소개서를 AI가 분석하고, 예상 질문과 답변 가이드를 제공합니다.
- **📊 심층 분석 리포트**:
  - 면접 종료 후, 답변 내용, NCS 직무 역량 기반 평가, 강점/약점 분석 등이 포함된 종합 리포트를 제공합니다.
- **👤 사용자 프로필 및 이력서 관리**:
  - 표준화된 이력서 및 경력, 학력 등 프로필 정보를 관리하는 CRUD API를 제공합니다.

## 기술 스택

- **Backend**: Django, Django REST Framework
- **AI/ML**:
  - **RAG Pipeline**: LlamaIndex
  - **LLM**: Azure OpenAI
  - **Vector Store**: Azure AI Search
  - **Document Storage**: Azure Blob Storage
- **Authentication**: dj-rest-auth, Simple JWT
- **Database**: (Default: SQLite, can be configured)
- **Package Management**: uv
- **Environment Management**: dotenv

## 설치 및 실행 방법

### 1. 사전 요구사항

- Python 3.11.9 이상
- `uv` 패키지 관리자
- Azure 계정 및 아래 서비스 생성:
  - Azure Storage Account (Blob 컨테이너 생성)
  - Azure AI Search
  - Azure OpenAI Service (모델 배포)

### 2. 프로젝트 클론

```bash
git clone <your-repository-url>
cd ares-backend
```

### 3. 가상 환경 생성 및 라이브러리 설치

`uv`를 사용하여 가상 환경을 생성하고, `pyproject.toml`에 명시된 라이브러리를 설치합니다.

```bash
# 가상 환경 생성
uv venv

# 가상 환경 활성화
source .venv/bin/activate  # macOS/Linux
.venv\Scripts\activate    # Windows

# 라이브러리 동기화
uv pip sync pyproject.toml
```

### 4. 환경 변수 설정

프로젝트 루트에 `.env.development` 파일을 생성하고 아래 내용을 채워넣습니다.

```env
# Django Settings
SECRET_KEY='your-django-secret-key'
DEBUG=True

# Database (SQLite example)
# DATABASE_URL=sqlite:///db.sqlite3

# Azure Services
AZURE_STORAGE_CONNECTION_STRING='your-storage-connection-string'
AZURE_SEARCH_ENDPOINT='https://your-search-service.search.windows.net'
AZURE_SEARCH_KEY='your-search-admin-key'
AZURE_OPENAI_ENDPOINT='https://your-openai-service.openai.azure.com/'
AZURE_OPENAI_KEY='your-openai-api-key'
AZURE_OPENAI_API_VERSION='2024-02-15-preview' # 사용하는 API 버전에 맞게 수정
AZURE_OPENAI_MODEL='your-deployment-name' # gpt-4o 등
AZURE_OPENAI_EMBEDDING_MODEL='your-embedding-deployment-name' # text-embedding-3-small 등

# Social Auth (Optional)
GOOGLE_CLIENT_ID='your-google-client-id'
GOOGLE_CLIENT_SECRET='your-google-client-secret'
```

### 5. 데이터베이스 마이그레이션

```bash
uv run python manage.py migrate
```

### 6. 개발 서버 실행

`dotenvx`를 사용하여 `.env.development` 파일의 환경 변수를 주입하고 서버를 실행합니다.

```bash
dotenvx run -f .env.development -- uv run daphne -p 8000 ares.asgi:application
```

## 주요 API 엔드포인트

- **`POST /api/v1/interviews/start/`**: 새로운 AI 모의 면접을 시작합니다.
- **`POST /api/v1/interviews/next/`**: 다음 꼬리 질문을 요청합니다.
- **`POST /api/v1/interviews/answer/`**: 질문에 대한 답변을 제출하고 분석을 요청합니다.
- **`POST /api/v1/interviews/finish/`**: 면접을 종료합니다.
- **`GET /api/v1/interviews/report/<uuid:session_id>/`**: 특정 면접 세션의 최종 리포트를 조회합니다.
- **`POST /api/v1/resume/analyze/`**: 이력서 텍스트를 분석합니다.
- **`POST /api/v1/auth/registration/`**: 회원가입
- **`POST /api/v1/auth/login/`**: 로그인

> 이 외에도 이력서, 자기소개서, 사용자 프로필 관리를 위한 다양한 CRUD 엔드포인트가 `ares/api/views/v1/urls.py`에 정의되어 있습니다.

---

## Deployment to Azure (Staging)

다음은 `ares-backend`와 `ares-frontend`를 Azure Container Instances(ACI)에 Staging 환경으로 배포하는 과정입니다.

### 1단계: Azure Container Registry (ACR) 준비

Docker 이미지를 저장할 프라이빗 레지스트리를 생성하고 로그인합니다. 이 작업은 최초 한 번만 수행하면 됩니다.

```bash
# 리소스 그룹 (없으면 생성: az group create --name YourResourceGroup --location koreacentral)
RESOURCE_GROUP="YourResourceGroup"

# ACR 이름 (전역적으로 고유해야 함)
ACR_NAME="projectares"

# ACR 생성
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic

# ACR 로그인
az acr login --name $ACR_NAME
```

### 2단계: Backend Docker 이미지 빌드 및 푸시

`Dockerfile`을 사용하여 백엔드 애플리케이션을 Docker 이미지로 빌드하고, 생성한 ACR에 푸시합니다.

```bash
# ares-backend 프로젝트 루트에서 실행
docker build -t ${ACR_NAME}.azurecr.io/ares-backend:staging-v1 .
docker push ${ACR_NAME}.azurecr.io/ares-backend:staging-v1
```

### 3단계: 배포를 위한 환경 변수 준비

배포 명령어에 사용될 백엔드용 `DOTENV_KEY`를 미리 확인합니다.

```bash
# ares-backend 프로젝트 루트에서 실행
BACKEND_DOTENV_KEY=$(npx dotenvx keys staging)
echo $BACKEND_DOTENV_KEY
```

### 4단계: Azure Container Instances (ACI) 배포

프론트엔드/백엔드 이미지를 하나의 컨테이너 그룹으로 배포합니다. 이 명령어는 일반적으로 프론트엔드 프로젝트 디렉토리나 별도의 배포 스크립트에서 실행하지만, 여기서는 전체 구조를 보여주기 위해 포함합니다.

> **참고:** 아래 명령어는 프론트엔드 이미지가 `ares-frontend:staging-v1` 태그로 ACR에 푸시되었다고 가정합니다.

```bash
# 배포 관련 변수 설정
RESOURCE_GROUP="YourResourceGroup"
ACR_NAME="projectares"
ACI_NAME="ares-staging-app"
DNS_NAME="ares-app-$(openssl rand -hex 4)" # 고유한 DNS 이름 생성

# ACR 관리자 계정 활성화 및 자격 증명 가져오기
az acr update -n $ACR_NAME --admin-enabled true
ACR_USERNAME=$(az acr credential show -n $ACR_NAME --query "username" -o tsv)
ACR_PASSWORD=$(az acr credential show -n $ACR_NAME --query "passwords[0].value" -o tsv)

# 이전에 확인한 백엔드 DOTENV_KEY
BACKEND_DOTENV_KEY="dotenv://:key_..."

# ACI 컨테이너 그룹 생성
az container create \
  --resource-group $RESOURCE_GROUP \
  --name $ACI_NAME \
  --image "${ACR_NAME}.azurecr.io/ares-frontend:staging-v1" \
  --dns-name-label $DNS_NAME \
  --ports 80 \
  --registry-login-server "${ACR_NAME}.azurecr.io" \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --containers \
    "[{\
      \"name\": \"ares-backend\",\
      \"image\": \"${ACR_NAME}.azurecr.io/ares-backend:staging-v1\",\
      \"ports\": [],\
      \"environmentVariables\": [{\
        \"name\": \"DOMAIN_NAME\",\
        \"value\": \"${DNS_NAME}.koreacentral.azurecontainer.io\"
      },{\
        \"name\": \"DJANGO_ALLOWED_HOSTS\",\
        \"value\": \"${DNS_NAME}.koreacentral.azurecontainer.io,localhost\"
      }],\
      \"secureEnvironmentVariables\": {\
        \"DOTENV_KEY\": \"$BACKEND_DOTENV_KEY\"
      }
    }]"

echo "배포가 시작되었습니다. 몇 분 후 http://${DNS_NAME}.koreacentral.azurecontainer.io 에서 확인하실 수 있습니다."
```