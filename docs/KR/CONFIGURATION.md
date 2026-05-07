# 설정 레퍼런스

SAP ADK Agent의 모든 설정 옵션에 대한 문서입니다.

## 환경 변수

환경 변수는 `sap_agent/.env` (로컬 개발) 또는 컨테이너 환경 변수 (Agent Engine 배포)에서 로드됩니다. `SAP_` 접두사가 필요합니다.

### 필수 변수

| 변수 | 설명 | 예시 |
|------|------|------|
| `SAP_HOST` | SAP Gateway 서버 호스트명/IP | `10.142.0.5` |
| `SAP_OAUTH_CLIENT_ID` | SAP의 OAuth 2.0 클라이언트 ID | `OAUTH2` |
| `SAP_OAUTH_CLIENT_SECRET` | OAuth 2.0 클라이언트 시크릿 | `MySecret123` |
| `SAP_OAUTH_TOKEN_URL` | SAP OAuth 토큰 엔드포인트 | `https://10.142.0.5:44300/sap/bc/sec/oauth2/token?sap-client=100` |
| `SAP_OAUTH_AUTHORIZE_URL` | SAP OAuth 인가 엔드포인트 | `https://10.142.0.5:44300/sap/bc/sec/oauth2/authorize?sap-client=100` |

### 선택 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SAP_PORT` | `44300` | SAP 서버 포트 |
| `SAP_CLIENT` | `100` | SAP 클라이언트 번호 |
| `SAP_AUTH_TYPE` | `sap_oauth` | 인증 유형 (`sap_oauth`만 지원) |
| `SAP_OAUTH_REDIRECT_URI` | _(없음)_ | 콜백용 OAuth 리다이렉트 URI |
| `SAP_OAUTH_SCOPE` | _(없음)_ | OAuth 스코프 (예: `Z_TRAVEL_RECO_SRV_0001`) |
| `SAP_OAUTH_CSRF_FOR_WRITES` | `False` | 쓰기 작업 시 CSRF 토큰 가져오기 |
| `SAP_VERIFY_SSL` | `False` | SSL 인증서 검증 (유효한 인증서가 있는 프로덕션에서는 `True`로 설정) |
| `SAP_TIMEOUT` | `30` | 요청 타임아웃 (초) |
| `SAP_RETRY_ATTEMPTS` | `3` | 실패 시 재시도 횟수 |

### GCP 변수

| 변수 | 설명 | 예시 |
|------|------|------|
| `GOOGLE_GENAI_USE_VERTEXAI` | Gemini에 Vertex AI 사용 | `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | GCP 프로젝트 ID | `my-project-id` |
| `GOOGLE_CLOUD_LOCATION` | GCP 리전 | `global` |
| `GOOGLE_CLIENT_ID` | One Tap용 Google OAuth Client ID | _(자동 설정)_ |

### 서버 변수 (선택)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SAP_GW_HOST` | `0.0.0.0` | 서버 바인드 주소 |
| `SAP_GW_PORT` | `8000` | 서버 포트 |
| `SAP_GW_LOG_LEVEL` | `INFO` | 로깅 레벨 |
| `SAP_GW_DEBUG` | `False` | 디버그 모드 활성화 |
| `SAP_SERVICES_CONFIG_PATH` | _(자동 감지)_ | `services.yaml` 경로 |

## services.yaml

SAP OData 서비스, 엔티티 세트, 게이트웨이 설정을 정의합니다. `sap_agent/services.yaml`에 위치합니다.

### 스키마

```yaml
# 게이트웨이 URL 설정
gateway:
  base_url_pattern: "https://{host}:{port}/sap/opu/odata"  # {host}와 {port}를 포함해야 함
  metadata_suffix: "/$metadata"
  service_catalog_path: "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection"
  auth_endpoint:
    use_catalog_metadata: true           # 권장: 일반 카탈로그 사용
    # service_id: Z_MY_SRV              # 대안: 특정 서비스 사용
    # entity_name: MyEntitySet          # CSRF 토큰용 엔티티
    # csrf_required_for_writes: false

# 서비스 정의
services:
  - id: Z_MY_SERVICE_SRV                # 고유 ID (tool 호출에 사용)
    name: "My Service"                   # 사람이 읽을 수 있는 이름
    path: "/SAP/Z_MY_SERVICE_SRV"       # /로 시작해야 함
    version: v2                          # v2 또는 v4
    description: "서비스 설명"
    entities:
      - name: MyEntitySet               # $metadata의 엔티티 세트 이름
        key_field: MyKey                 # 기본 키 필드
        description: "엔티티 설명"
        navigations:                     # 네비게이션 프로퍼티 (참고용)
          - ToRelated
        default_select:                  # 쿼리 기본 필드
          - MyKey
          - Name
          - Date
    custom_headers: {}                   # 서비스별 HTTP 헤더
```

### 서비스 세부 정보 확인 방법

1. **서비스 이름**: SAP 트랜잭션 `SE80` 또는 `/IWFND/MAINT_SERVICE`
2. **엔티티 세트 및 키**: `{base_url}{service_path}/$metadata` 브라우징
3. **서비스 경로 형식**: 일반적으로 `/SAP/<SERVICE_NAME>` 또는 `/<NAMESPACE>/<SERVICE_NAME>`

## Secret Manager (프로덕션)

프로덕션에서는 SAP 자격 증명이 GCP Secret Manager에 `sap-credentials`라는 시크릿 이름으로 저장됩니다:

```json
{
  "auth_type": "sap_oauth",
  "host": "10.142.0.5",
  "port": 44300,
  "client": "100",
  "oauth_client_id": "YOUR_CLIENT_ID",
  "oauth_client_secret": "YOUR_CLIENT_SECRET",
  "oauth_token_url": "https://sap-server/sap/bc/sec/oauth2/token?sap-client=100",
  "oauth_authorize_url": "https://sap-server/sap/bc/sec/oauth2/authorize?sap-client=100",
  "oauth_redirect_uri": "https://your-callback-url/callback",
  "oauth_scope": "YOUR_SCOPE"
}
```

에이전트는 `agent.py`의 `_load_runtime_secrets()`를 통해 시작 시 이 시크릿을 읽습니다.

## 설정 검증

모든 설정은 Pydantic을 사용하여 로드 시 검증됩니다:

- `SAPConnectionConfig`: host, port (1-65535), auth_type (`sap_oauth`여야 함) 검증 및 OAuth 자격 증명 완전성 확인
- `ServicesYAMLConfig`: 서비스 경로가 `/`로 시작하는지, OData 버전이 `v2` 또는 `v4`인지, 엔티티 이름이 비어있지 않은지 검증
- `GatewayConfig`: `base_url_pattern`에 `{host}`와 `{port}` 플레이스홀더가 포함되어 있는지 검증

잘못된 설정은 시작 시 `ValueError` 또는 `ValidationError`를 발생시킵니다.

---

- [English Documentation](../CONFIGURATION.md)
