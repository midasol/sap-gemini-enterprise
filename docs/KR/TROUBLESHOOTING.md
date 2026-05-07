# 문제 해결

SAP ADK Agent를 개발, 배포, 실행할 때 발생할 수 있는 일반적인 문제와 해결 방법입니다.

## 인증 오류

### "SAP OAuth login required. No user has authenticated yet."

**원인**: 에이전트에 현재 사용자의 캐시된 토큰이 없습니다. 먼저 인증이 필요합니다.

**해결**: 에이전트에 인증을 요청합니다 (`sap_authenticate`를 호출하여 로그인 URL을 반환합니다).

### "Authorization code is invalid or expired. Please restart the SAP login flow."

**원인**: OAuth 인가 코드가 이미 사용되었거나 교환 전에 만료되었습니다.

**해결**: 새 로그인 흐름을 시작합니다. 인가 코드는 일회성이며 시간 제한이 있습니다.

### "OAuth client credentials are invalid."

**원인**: `SAP_OAUTH_CLIENT_ID` 또는 `SAP_OAUTH_CLIENT_SECRET`이 SAP에 설정된 값과 일치하지 않습니다.

**해결**: SAP 트랜잭션 `SOAUTH2`에서 자격 증명을 확인하고 `.env` 또는 Secret Manager를 업데이트합니다.

### "SAP session expired and refresh failed."

**원인**: 액세스 토큰과 refresh token 모두 만료되었거나 취소되었습니다.

**해결**: 사용자가 SAP 로그인 흐름을 통해 다시 인증해야 합니다.

## 연결 오류

### "Connection error during SAP OAuth token request"

**원인**: 에이전트가 SAP 서버에 접근할 수 없습니다.

**해결**:
- **로컬 개발**: SAP 호스트가 네트워크에서 접근 가능한지 확인합니다. VPN이 필요한 경우 확인합니다.
- **Agent Engine**: PSC 인프라가 올바르게 설정되었는지 확인합니다. 확인 사항:
  - 네트워크 첨부 존재 여부: `gcloud compute network-attachments list`
  - 방화벽 규칙의 트래픽 허용 여부: `gcloud compute firewall-rules list --filter="name:allow-agent-engine"`
  - 방화벽 대상 범위의 SAP 호스트 IP 정확성

### "Request timeout for GET/POST ..."

**원인**: SAP 서버가 느리거나 접근할 수 없습니다.

**해결**: `SAP_TIMEOUT`을 늘립니다 (기본값: 30초). SAP 시스템 상태를 확인합니다.

### SSL 인증서 오류

**원인**: SAP이 자체 서명 또는 내부 CA 인증서를 사용합니다.

**해결**: 개발 환경에서는 `SAP_VERIFY_SSL=False`로 설정합니다. 프로덕션에서는 적절한 인증서를 설치하거나 CA 번들을 설정합니다.

## 설정 오류

### "SAP host cannot be empty"

**원인**: `SAP_HOST` 환경 변수가 설정되지 않았습니다.

**해결**: `sap_agent/.env` 또는 환경 변수에 `SAP_HOST`를 설정합니다.

### "oauth_authorize_url is required for sap_oauth authentication"

**원인**: 설정에 `SAP_OAUTH_AUTHORIZE_URL`이 누락되었습니다.

**해결**: 모든 필수 OAuth 환경 변수를 설정합니다. [설정 레퍼런스](CONFIGURATION.md)를 참고하세요.

### "Only auth_type 'sap_oauth' is supported"

**원인**: `SAP_AUTH_TYPE`이 `sap_oauth` 이외의 값으로 설정되었습니다.

**해결**: `SAP_AUTH_TYPE=sap_oauth`로 설정합니다. Basic auth와 client credentials는 더 이상 지원되지 않습니다.

### "Service 'X' not found in configuration"

**원인**: 쿼리에 사용된 서비스 ID가 `services.yaml`의 어떤 서비스와도 일치하지 않습니다.

**해결**: `sap_agent/services.yaml`에서 올바른 서비스 ID를 확인합니다. `sap_list_services`를 사용하여 사용 가능한 서비스를 확인합니다.

## 배포 오류

### "Deployment failed: permission denied"

**원인**: IAM 권한이 누락되었습니다.

**해결**: `scripts/setup_gcp_prerequisites.sh`를 실행하여 서비스 계정과 역할을 설정합니다. 필요한 주요 역할:
- `agent-engine-sa`에 `roles/aiplatform.user`
- `agent-engine-sa`에 `roles/secretmanager.secretAccessor`
- AI Platform 서비스 에이전트에 `roles/compute.networkAdmin`

### "Secret 'sap-credentials' not found"

**원인**: SAP 자격 증명 시크릿이 Secret Manager에 생성되지 않았습니다.

**해결**: 시크릿을 생성합니다:
```bash
echo '{"auth_type":"sap_oauth","host":"...","oauth_client_id":"..."}' | \
  gcloud secrets versions add sap-credentials --data-file=-
```

### Agent Engine이 SAP에 접근할 수 없음

**원인**: PSC 인프라가 설정되지 않았습니다.

**해결**: `scripts/setup_psc_infrastructure.sh`를 실행하여 다음을 생성합니다:
- PSC 서브넷
- 네트워크 첨부
- SAP IP로의 트래픽을 허용하는 방화벽 규칙

## Cloud Run OAuth 콜백

### "GOOGLE_CLOUD_PROJECT environment variable is required"

**원인**: Cloud Run 서비스에 프로젝트 ID 설정이 누락되었습니다.

**해결**: 배포 시 `GOOGLE_CLOUD_PROJECT`를 설정합니다:
```bash
gcloud run deploy ... --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
```

### Google One Tap이 나타나지 않음

**원인**: Google Client ID가 일치하지 않거나 사용자의 브라우저가 서드파티 쿠키를 차단합니다.

**해결**:
- `GOOGLE_CLIENT_ID`가 OAuth 동의 화면과 일치하는지 확인합니다
- SAP 로그인 자체는 여전히 작동합니다 — One Tap은 사용자 식별을 위한 선택 기능입니다

## 테스트 실패

### "ModuleNotFoundError: No module named 'sap_agent'"

**원인**: 패키지가 개발 모드로 설치되지 않았습니다.

**해결**: `pip install -e .` 또는 `uv sync --group dev`

### 실제 SAP 자격 증명이 환경에 있을 때 테스트 실패

**원인**: `clean_env` 픽스처가 `SAP_*` 변수를 제거하지만 일부가 누출될 수 있습니다.

**해결**: 테스트는 `conftest.py` autouse 픽스처를 사용하여 환경을 정리합니다. 문제가 지속되면 테스트 프로세스 외부에서 설정된 환경 변수를 확인합니다.

---

- [English Documentation](../TROUBLESHOOTING.md)
