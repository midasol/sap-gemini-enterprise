# 아키텍처

이 문서는 SAP ADK Agent의 모듈 구조, 데이터 흐름, 컴포넌트 관계를 설명합니다.

## 상위 개요

SAP ADK Agent는 [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/)으로 구축된 AI 에이전트로, 자연어를 통해 SAP Gateway OData 서비스를 쿼리합니다. **Vertex AI Agent Engine**에 배포되며 **Private Service Connect (PSC)**를 통해 온프레미스 SAP 시스템에 연결됩니다.

```
User (Chat UI)
    |
    v
Vertex AI Agent Engine (Gemini LLM)
    |
    v
sap_agent/agent.py  (ADK root_agent + tool functions)
    |
    v
sap_gw_connector/   (SAP Gateway HTTP client layer)
    |  (via PSC network attachment)
    v
SAP Gateway (OData v2/v4 services)
```

## 모듈 구조

```
sap_agent/
  agent.py                  # 메인 에이전트: root_agent 정의, tool 함수
  sap_auth_config.py        # OAuth 흐름을 위한 ADK AuthConfig 빌드
  .env                      # 환경 변수 템플릿
  services.yaml             # SAP OData 서비스 정의
  sap_gw_connector/
    core/
      auth.py               # 인증 전략 (SAPAuthorizationCodeStrategy)
      sap_client.py          # SAP OData 요청용 HTTP 클라이언트
      exceptions.py          # 커스텀 예외 계층 구조
    config/
      settings.py            # Pydantic 설정 모델 (SAPConnectionConfig 등)
      schemas.py             # services.yaml용 Pydantic 모델
      loader.py              # YAML 설정 로더
    tools/
      base.py                # SAPTool ABC + ToolRegistry
      auth_tool.py           # sap_authenticate tool
      query_tool.py          # sap_query tool
      entity_tool.py         # sap_get_entity tool
      service_tool.py        # sap_list_services tool
    utils/
      validators.py          # 입력 검증 헬퍼
      logger.py              # 구조화된 로깅 (structlog)
    protocol/
      schemas.py             # JSON-RPC 프로토콜 스키마

cloud-run-oauth-callback/   # OAuth 리다이렉트를 위한 별도 Cloud Run 서비스
  main.py                   # Flask 앱: /callback, /identify, /health
  Dockerfile
  requirements.txt

scripts/
  deploy_agent_engine.py    # Vertex AI Agent Engine 배포
  cleanup_agent_engines.py  # 모든 Agent Engine 삭제
  setup_gcp_prerequisites.sh # GCP API, 서비스 계정, IAM 설정
  setup_psc_infrastructure.sh # PSC 서브넷, 네트워크 첨부, 방화벽 설정
  test_*.py                 # 다양한 원격 테스트 스크립트

tests/                      # pytest 테스트 스위트
```

## 핵심 컴포넌트

### 1. 에이전트 레이어 (`sap_agent/agent.py`)

메인 모듈에서 정의하는 항목:

- **`root_agent`**: Gemini 기반의 ADK `LlmAgent`, tool 함수가 등록되어 있음
- **Tool 함수** (LLM에 노출):
  - `sap_authenticate` — OAuth 로그인 시작, 코드 교환, 사용자별 토큰 관리
  - `sap_query` — 필터, 페이지네이션, 필드 선택을 사용한 OData 엔티티 세트 쿼리
  - `sap_get_entity` — 키로 단일 엔티티 조회
  - `sap_list_services` — `services.yaml`에 설정된 SAP OData 서비스 목록 조회
- **사용자별 인증 관리**: 세션 기반 UID 또는 실제 사용자 ID를 키로 하는 `SAPAuthenticator` 인스턴스의 스레드 안전 캐시. Agent Engine의 `default-user-id`는 캐시 키로 사용되지 않으며, 세션 기반 UID(`session-{session_id}`)가 고유 식별자로 사용되고 OAuth 완료 후 실제 이메일로 업그레이드됨.
- **Secret Manager 통합**: 런타임에 SAP 자격 증명을 로드하고 대기 중인 OAuth 코드를 폴링. 기존 `sap-oauth-token-*` 시크릿 스캔을 통한 세션 간 토큰 복구 지원.

### 2. 인증 (`core/auth.py`)

**Strategy 패턴**을 사용하며 하나의 구현체가 있습니다:

- **`SAPAuthorizationCodeStrategy`**: PKCE를 사용한 OAuth 2.0 Authorization Code
  - PKCE 코드 챌린지를 포함한 인증 URL 생성
  - state에서 code verifier를 결정론적으로 도출 (컨테이너 재시작 시에도 유지)
  - 인가 코드를 사용자별 SAP 액세스 토큰으로 교환
  - refresh token을 통한 만료 토큰 자동 갱신
  - LRU 제거 방식의 사용자별 토큰 캐시 (최대 1000명)

- **`SAPAuthenticator`**: 하위 호환성을 위해 전략을 래핑하는 Facade

### 3. SAP HTTP 클라이언트 (`core/sap_client.py`)

`SAPClient`는 SAP Gateway와의 모든 HTTP 통신을 처리합니다:

- `aiohttp`를 사용한 비동기 HTTP 및 커넥션 풀링
- 401 응답 시 자동 토큰 갱신
- 지수 백오프를 사용한 재시도
- 쓰기 작업을 위한 CSRF 토큰 지원
- OData 작업: query, get, create, update, delete

### 4. 설정 시스템

세 가지 계층의 설정:

| 계층 | 소스 | 용도 |
|------|------|------|
| `SAPConnectionConfig` | 환경 변수 (`SAP_*`) | SAP 서버 연결 + OAuth 자격 증명 |
| `services.yaml` | YAML 파일 | OData 서비스/엔티티 정의 |
| Secret Manager | GCP Secret Manager | 프로덕션 런타임 자격 증명 |

### 5. Cloud Run OAuth 콜백

별도의 Flask 마이크로서비스 (`cloud-run-oauth-callback/`)로 다음을 수행합니다:
1. `/callback`에서 SAP OAuth 리다이렉트 콜백 수신
2. GCP Secret Manager에 인가 코드 저장
3. Google One Tap (`/identify`)을 사용하여 사용자의 Google 계정 연결
4. 에이전트가 Secret Manager를 폴링하여 완료된 로그인 감지

## 데이터 흐름: 쿼리 실행

```
1. 사용자 요청: "지난달 판매 주문을 보여주세요"
2. Gemini LLM이 sap_query tool 호출을 결정
3. sap_query()가 인증된 사용자를 확인:
   a. 사용자별 캐시에서 SAPAuthenticator 가져오기
   b. get_valid_token() 호출 → 캐시된 또는 갱신된 토큰 반환
4. SAPClient.query_entity_set()이 OData GET 요청 전송:
   - URL: https://{host}:{port}/sap/opu/odata/{service_path}/{entity_set}
   - Headers: Authorization: Bearer {access_token}
   - Params: $filter, $select, $top, $skip, $format=json
5. 응답이 파싱되어 LLM에 반환
6. LLM이 사용자를 위한 자연어 응답을 생성
```

## 데이터 흐름: OAuth 인증

```
1. 사용자가 sap_authenticate를 트리거 (또는 인증 없이 첫 쿼리 시도)
2. 에이전트가 PKCE 챌린지를 포함한 SAP OAuth URL 생성
3. 사용자가 URL을 열면 → SAP 로그인 페이지 → SAP이 Cloud Run 콜백으로 리다이렉트
4. Cloud Run 콜백이 Secret Manager에 인가 코드 저장
5. Google One Tap이 사용자의 Google 계정 식별 (선택 사항)
6. 에이전트가 Secret Manager를 폴링하여 대기 중인 코드 발견
7. 에이전트가 코드를 access_token + refresh_token으로 교환
8. 토큰이 후속 요청을 위해 사용자별로 캐시됨
```

## 예외 계층 구조

```
SAPError (base)
  ├── SAPAuthenticationError
  │     └── SAPOAuthError
  ├── SAPConnectionError
  ├── SAPRequestError
  ├── SAPTimeoutError
  └── SAPValidationError
```

## 네트워크 아키텍처 (프로덕션)

```
Vertex AI Agent Engine
    |  (PSC Network Attachment)
    v
VPC Network (sap-cal-default-network)
    |  (PSC Subnet: 192.168.10.0/28)
    |  (Firewall: allow tcp:44300,8000,443,80)
    v
SAP Gateway (on-premise, e.g., 10.142.0.5:44300)
```

Private Service Connect를 통해 Agent Engine 컨테이너가 공용 인터넷 노출 없이 VPC를 통해 온프레미스 SAP 시스템에 접근할 수 있습니다.

---

- [English Documentation](../ARCHITECTURE.md)
