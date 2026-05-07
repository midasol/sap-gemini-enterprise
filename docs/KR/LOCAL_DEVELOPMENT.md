# 로컬 개발 가이드

## 사전 요구 사항

- Python 3.11+ (최대 3.13)
- Vertex AI가 활성화된 GCP 프로젝트
- 네트워크에서 접근 가능한 SAP Gateway 시스템
- 인증된 `gcloud` CLI

## 설정

### 1. 클론 및 설치

```bash
git clone <repository-url>
cd sap-adk-agent

# uv로 설치 (권장)
uv sync --group dev

# 또는 pip으로 설치
pip install -e ".[dev]"
```

### 2. 환경 설정

환경 변수 템플릿을 복사하고 편집합니다:

```bash
cp sap_agent/.env.example sap_agent/.env
# sap_agent/.env를 SAP 자격 증명으로 편집
```

필수 변수 — 전체 목록은 [설정 레퍼런스](CONFIGURATION.md)를 참고하세요:

```env
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id

SAP_HOST=your-sap-host
SAP_OAUTH_CLIENT_ID=your-client-id
SAP_OAUTH_CLIENT_SECRET=your-client-secret
SAP_OAUTH_TOKEN_URL=https://your-sap-host:44300/sap/bc/sec/oauth2/token?sap-client=100
SAP_OAUTH_AUTHORIZE_URL=https://your-sap-host:44300/sap/bc/sec/oauth2/authorize?sap-client=100
```

### 3. 서비스 설정

`sap_agent/services.yaml`을 SAP OData 서비스에 맞게 편집합니다. 스키마는 [설정 레퍼런스](CONFIGURATION.md#servicesyaml)를 참고하세요.

## 로컬 실행

### ADK Dev UI

가장 간단한 로컬 실행 방법으로, ADK 웹 인터페이스를 실행합니다:

```bash
cd sap_agent
adk web
```

`http://localhost:8000`에서 채팅 UI가 포함된 ADK 개발 서버가 시작됩니다.

### HTTPS 개발 서버

OAuth 콜백 테스트를 위해 HTTPS가 필요한 경우:

```bash
# 자체 서명 인증서 생성
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem \
  -days 365 -nodes -subj '/CN=localhost'

# HTTPS 서버 실행
python run_https.py
```

자체 서명 인증서를 사용하여 `https://localhost:8000`에서 서버가 시작됩니다.

## 테스트 실행

```bash
# 전체 테스트
pytest

# 상세 출력
pytest -v

# 특정 테스트 파일
pytest tests/test_auth.py
```

자세한 내용은 [테스트 가이드](TESTING.md)를 참고하세요.

## 린팅

```bash
# Ruff (린팅 + 포매팅)
ruff check .
ruff format .

# MyPy (타입 체크)
mypy sap_agent/

# Codespell (오타 검사)
codespell
```

린트 의존성 설치: `pip install -e ".[lint]"`

## 프로젝트 스크립트

| 스크립트 | 용도 |
|---------|------|
| `scripts/deploy_agent_engine.py` | Vertex AI Agent Engine에 에이전트 배포 |
| `scripts/cleanup_agent_engines.py` | 모든 Agent Engine 인스턴스 삭제 |
| `scripts/setup_gcp_prerequisites.sh` | GCP API, 서비스 계정, IAM 설정 |
| `scripts/setup_psc_infrastructure.sh` | 온프레미스 SAP용 PSC 네트워킹 설정 |
| `scripts/test_agent_engine.py` | 배포된 Agent Engine 인스턴스 테스트 |
| `scripts/test_remote_agent_v2.py` | 스트리밍을 사용한 원격 에이전트 테스트 |

### Agent Engine 배포

```bash
python scripts/deploy_agent_engine.py --project your-project-id

# 기존 배포 업데이트
python scripts/deploy_agent_engine.py --project your-project-id \
  --update projects/123/locations/us-central1/reasoningEngines/456
```

---

- [English Documentation](../LOCAL_DEVELOPMENT.md)
