# SAP Agent 빠른 참조

## 설정 체크리스트

```bash
# 1. GCP 리소스 (API, 서비스 계정, IAM)
./scripts/setup_gcp_prerequisites.sh

# 2. SAP 전용 프라이빗 네트워크 (온프레미스인 경우)
./scripts/setup_psc_infrastructure.sh

# 3. SAP 자격증명 저장
gcloud secrets create sap-credentials --replication-policy="automatic"
echo '{ "auth_type": "sap_oauth", ... }' | gcloud secrets versions add sap-credentials --data-file=-

# 4. Cloud Run OAuth 콜백 배포
cd cloud-run-oauth-callback && gcloud run deploy sap-oauth-callback --source . --allow-unauthenticated

# 5. Agent 배포
python scripts/deploy_agent_engine.py --project <PROJECT_ID>

# 5-alt. 기존 Agent 업데이트
python scripts/deploy_agent_engine.py --project <PROJECT_ID> --update <RESOURCE_NAME>

# 6. 확인
gcloud ai reasoning-engines list --region=us-central1
```

## 환경 변수

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `SAP_HOST` | 예 | - | SAP Gateway 호스트명 또는 IP |
| `SAP_PORT` | 아니오 | 44300 | SAP Gateway HTTPS 포트 |
| `SAP_CLIENT` | 아니오 | 100 | SAP 클라이언트 번호 |
| `SAP_AUTH_TYPE` | 예 | sap_oauth | 인증 유형 (`sap_oauth`만 지원) |
| `SAP_OAUTH_CLIENT_ID` | 예 | - | OAuth 클라이언트 ID |
| `SAP_OAUTH_CLIENT_SECRET` | 예 | - | OAuth 클라이언트 시크릿 |
| `SAP_OAUTH_TOKEN_URL` | 예 | - | 토큰 엔드포인트 |
| `SAP_OAUTH_AUTHORIZE_URL` | 예 | - | 인가 엔드포인트 |
| `SAP_OAUTH_REDIRECT_URI` | 예 | - | 리다이렉트 URI (프로덕션에서는 Cloud Run URL) |
| `SAP_OAUTH_SCOPE` | 아니오 | - | OAuth 스코프 |
| `SAP_AGENT_MODEL` | 아니오 | gemini-3.1-pro-preview | LLM 모델 오버라이드 |
| `SAP_VERIFY_SSL` | 아니오 | false | SSL 검증 |
| `SAP_TIMEOUT` | 아니오 | 30 | 요청 타임아웃 (초) |
| `SAP_RETRY_ATTEMPTS` | 아니오 | 3 | 재시도 횟수 |

## Agent 도구

### sap_authenticate

SAP OAuth Authorization Code + PKCE로 인증합니다.

```python
# Step 1: 로그인 URL 획득 (인수 불필요)
sap_authenticate()
# 반환: {"auth_url": "https://sap-host/authorize?...", "state": "..."}

# Step 2: 코드 교환 (보통 Cloud Run을 통해 자동 감지)
sap_authenticate(authorization_code="<code>", oauth_state="<state>")
# 반환: {"status": "authenticated", "sap_user": "SAPUSER01"}
```

### sap_list_services

`services.yaml`에 설정된 SAP OData 서비스를 목록으로 반환합니다.

```python
sap_list_services()
# 반환: 서비스 ID, 이름, 엔티티 목록
```

### sap_query

필터링 및 페이지네이션을 지원하는 OData 엔티티 세트 쿼리입니다.

```python
sap_query(
    service="Z_TRAVEL_RECO_SRV",       # 필수 - 서비스 ID
    entity_set="AirlineSet",            # 필수 - 엔티티 세트 이름
    filter="Carrid eq 'LH'",           # 선택 - OData $filter
    select="Carrid,Carrname",           # 선택 - 쉼표 구분 필드
    top=10,                             # 선택 - 최대 레코드 수
    skip=0,                             # 선택 - 페이지네이션 오프셋
    format="json_compact"               # 선택 - "json" 또는 "json_compact" (기본)
)
```

### sap_get_entity

키로 단일 엔티티를 조회합니다.

```python
sap_get_entity(
    service="Z_SALES_ORDER_GENAI_SRV",  # 필수
    entity_set="zsd004Set",              # 필수
    entity_key="91000092",               # 필수 - 키 값
    select="Vbeln,Netwr,Waerk"           # 선택
)
```

## 스크립트

| 스크립트 | 용도 |
|----------|------|
| `scripts/setup_gcp_prerequisites.sh` | API 활성화, 서비스 계정 생성, IAM 할당 |
| `scripts/setup_psc_infrastructure.sh` | SAP용 Private Service Connect 설정 |
| `scripts/deploy_agent_engine.py` | Vertex AI에 Agent 배포/업데이트 |
| `scripts/cleanup_agent_engines.py` | 배포된 Agent 제거 |
| `scripts/test_deployed_sap_agent.py` | 배포된 Agent 테스트 |
| `scripts/test_agent_engine.py` | 기본 연결 테스트 |
| `scripts/test_agent_engine_airlines.py` | 항공사 쿼리 테스트 |

## 로컬 개발

```bash
# HTTPS 서버 (SAP OAuth 리다이렉트에 필요)
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -keyout certs/key.pem -out certs/cert.pem \
  -days 365 -nodes -subj '/CN=localhost'
python run_https.py
# -> https://localhost:8000

# ADK Web UI (간단하지만 OAuth 리다이렉트 불가)
adk web
# -> http://localhost:8501
```

## SAP 설정 (트랜잭션)

| 트랜잭션 | 작업 |
|----------|------|
| `SICF` | `/sap/bc/sec/oauth2/authorize` 및 `/token` 활성화 |
| `SOAUTH2` | OAuth 클라이언트 생성, 리다이렉트 URI 등록, 스코프 할당 |
| `SU01` | OAuth 클라이언트 사용자 비밀번호 설정 (= 클라이언트 시크릿) |
| `PFCG` | 최종 사용자에게 OData 권한 할당 |

## 트러블슈팅

| 문제 | 해결 방법 |
|------|----------|
| SAP 연결 타임아웃 | PSC에 내부 IP 사용; 포트 44300 방화벽 확인 |
| SSL 오류 | 개발 환경에서 `SAP_VERIFY_SSL=false`; 프로덕션에서는 적절한 인증서 사용 |
| OAuth invalid_grant | 인가 코드 만료; 로그인 흐름 재시작 |
| OAuth invalid_client | 클라이언트 ID와 시크릿 확인 |
| 리다이렉트 URI 불일치 | SOAUTH2, Secret Manager, Cloud Run에서 정확히 일치해야 함 |
| serviceUsageConsumer 오류 | `setup_gcp_prerequisites.sh` 실행 |
| Secret Manager 거부 | `agent-engine-sa`의 IAM 확인 |
| redirect_uri를 찾을 수 없음 | Secret Manager `sap-credentials`에 포함되어 있는지 확인 |
| 서비스 목록이 비어 있음 | `services.yaml` 설정 확인 |
| 쿼리 결과 없음 | 엔티티 세트 이름과 OData 필터 문법 확인 |

### 디버깅 명령어

```bash
# Agent Engine 로그
gcloud logging read "resource.type=aiplatform.googleapis.com/ReasoningEngine" \
  --limit=50 --format=json

# Agent 상세 정보
gcloud ai reasoning-engines describe <ENGINE_ID> --region=us-central1

# 자격증명 확인
gcloud secrets versions access latest --secret=sap-credentials
```

## 빠른 테스트

```python
from vertexai import agent_engines

agent = agent_engines.get(
    "projects/<number>/locations/us-central1/reasoningEngines/<engine-id>"
)
session = agent.create_session()

session.send_message("authenticate with SAP")   # 로그인 URL 획득
session.send_message("list available services")  # 인증 후
session.send_message("show me all airlines")     # 데이터 조회
```

## 기본 설정값

| 설정 | 기본값 |
|------|--------|
| SAP Port | 44300 |
| SAP Client | 100 |
| Model | gemini-3.1-pro-preview |
| Region | us-central1 |
| Timeout | 30초 |
| Retries | 3 |
| Output Format | json_compact |
| Max Cached Users | 1000 |

---

- [배포 가이드](DEPLOYMENT_GUIDE.md)
- [SAP OAuth 설정](AUTH_SAP_OAUTH.md)
- [English Documentation](../QUICK_REFERENCE.md)
