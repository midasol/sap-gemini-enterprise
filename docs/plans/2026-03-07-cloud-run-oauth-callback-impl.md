# Cloud Run OAuth Callback Proxy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cloud Run 서비스로 SAP OAuth callback을 자동 수신하고, Agent Engine이 Secret Manager에서 코드를 자동 감지하여 수동 복사/붙여넣기를 제거

**Architecture:** Cloud Run이 SAP redirect를 수신하여 code+state를 Secret Manager에 저장. Agent Engine의 `sap_authenticate()`가 재호출 시 Secret Manager에서 pending code를 자동 감지하여 기존 Deterministic PKCE로 token exchange 수행.

**Tech Stack:** Python/Flask (Cloud Run), Google Secret Manager, 기존 agent.py 수정

---

### Task 1: Cloud Run 서비스 프로젝트 구조 생성

**Files:**
- Create: `cloud-run-oauth-callback/main.py`
- Create: `cloud-run-oauth-callback/requirements.txt`
- Create: `cloud-run-oauth-callback/Dockerfile`

**Step 1: 프로젝트 디렉토리 및 requirements.txt 생성**

```bash
mkdir -p cloud-run-oauth-callback
```

`cloud-run-oauth-callback/requirements.txt`:
```
flask==3.1.0
gunicorn==23.0.0
google-cloud-secret-manager==2.23.1
```

**Step 2: Dockerfile 작성**

`cloud-run-oauth-callback/Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "main:app"]
```

**Step 3: main.py 작성 (Cloud Run 서비스)**

`cloud-run-oauth-callback/main.py`:
```python
"""SAP OAuth Callback Proxy for Cloud Run.

Receives SAP OAuth redirect callbacks, stores authorization codes
in Secret Manager for Agent Engine to consume automatically.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from flask import Flask, request

from google.cloud import secretmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
PENDING_SECRET_PREFIX = "sap-oauth-pending"


def _sanitize_state_for_secret_id(state: str) -> str:
    """Create a Secret Manager-safe ID from the first 16 chars of state."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", state[:16])
    return f"{PENDING_SECRET_PREFIX}-{safe}"


def _get_sm_client():
    return secretmanager.SecretManagerServiceClient()


def _ensure_secret(client, secret_id: str) -> str:
    """Create secret if it doesn't exist. Returns full secret path."""
    parent = f"projects/{PROJECT_ID}"
    secret_path = f"{parent}/secrets/{secret_id}"
    try:
        client.get_secret(request={"name": secret_path})
    except Exception:
        try:
            client.create_secret(request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            })
            logger.info("Created secret: %s", secret_id)
        except Exception as e:
            if "ALREADY_EXISTS" not in str(e):
                raise
    return secret_path


@app.route("/callback")
def oauth_callback():
    """Receive SAP OAuth redirect and store code in Secret Manager."""
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        logger.warning("OAuth error: %s", error)
        return _error_page(f"SAP login failed: {error}"), 400

    if not code or not state:
        logger.warning("Missing code or state in callback")
        return _error_page("Invalid callback: missing code or state"), 400

    try:
        client = _get_sm_client()
        secret_id = _sanitize_state_for_secret_id(state)
        secret_path = _ensure_secret(client, secret_id)

        payload = json.dumps({
            "code": code,
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        client.add_secret_version(request={
            "parent": secret_path,
            "payload": {"data": payload.encode("UTF-8")},
        })

        logger.info("Stored pending OAuth code: secret=%s, state=%.8s...",
                     secret_id, state)
        return _success_page(), 200

    except Exception as e:
        logger.error("Failed to store OAuth code: %s", e)
        return _error_page("Internal error. Please try again."), 500


@app.route("/health")
def health():
    return {"status": "ok"}


def _success_page() -> str:
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>SAP Login Complete</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         display: flex; justify-content: center; align-items: center;
         min-height: 100vh; background: #f5f5f5; margin: 0; }
  .card { background: #fff; border-radius: 12px; padding: 40px;
          box-shadow: 0 2px 16px rgba(0,0,0,0.1); text-align: center;
          max-width: 420px; }
  .check { width: 64px; height: 64px; background: #e8f5e9; border-radius: 50%;
           display: flex; align-items: center; justify-content: center;
           margin: 0 auto 20px; font-size: 32px; color: #2e7d32; }
  h2 { color: #1a73e8; margin-bottom: 12px; font-size: 22px; }
  p { color: #666; line-height: 1.6; font-size: 15px; }
</style></head>
<body><div class="card">
  <div class="check">&#10003;</div>
  <h2>SAP Login Complete</h2>
  <p>You can now close this tab and return to the chat.<br>
     The agent will automatically detect your login.</p>
</div></body></html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>SAP Login Error</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex;
         justify-content: center; align-items: center;
         min-height: 100vh; background: #f5f5f5; margin: 0; }}
  .card {{ background: #fff; border-radius: 12px; padding: 40px;
          box-shadow: 0 2px 16px rgba(0,0,0,0.1); text-align: center;
          max-width: 420px; }}
  .icon {{ width: 64px; height: 64px; background: #fce4ec; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          margin: 0 auto 20px; font-size: 32px; color: #c62828; }}
  h2 {{ color: #c62828; margin-bottom: 12px; }}
  p {{ color: #666; line-height: 1.6; }}
</style></head>
<body><div class="card">
  <div class="icon">&#10007;</div>
  <h2>Login Error</h2>
  <p>{message}</p>
</div></body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
```

**Step 4: Commit**

```bash
git add cloud-run-oauth-callback/
git commit -m "feat: add Cloud Run OAuth callback proxy service"
```

---

### Task 2: Agent Engine에 pending code 감지 함수 추가

**Files:**
- Modify: `sap_agent/agent.py` (lines 56-75 area, Secret Manager section)
- Test: `tests/test_sap_oauth.py`

**Step 1: 테스트 작성**

`tests/test_sap_oauth.py` 하단에 추가:

```python
class TestPendingOAuthCodeDetection:
    """Cloud Run callback → Secret Manager → Agent auto-detect."""

    @patch("sap_agent.agent._get_secret_manager")
    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_found(self, mock_get_sm):
        """Pending code in Secret Manager → returns code+state dict."""
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client
        mock_get_sm.return_value = mock_sm

        from datetime import datetime, timezone
        payload = json.dumps({
            "code": "test_auth_code_123",
            "state": "abc123def456ghi7_rest_of_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        mock_response = MagicMock()
        mock_response.payload.data = payload.encode("UTF-8")
        mock_client.access_secret_version.return_value = mock_response

        result = _check_pending_oauth_code("abc123def456ghi7_rest_of_state")

        assert result is not None
        assert result["code"] == "test_auth_code_123"
        assert result["state"] == "abc123def456ghi7_rest_of_state"

    @patch("sap_agent.agent._get_secret_manager")
    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_not_found(self, mock_get_sm):
        """No pending code → returns None."""
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client
        mock_get_sm.return_value = mock_sm
        mock_client.access_secret_version.side_effect = Exception("NOT_FOUND")

        result = _check_pending_oauth_code("nonexistent_state")
        assert result is None

    @patch("sap_agent.agent._get_secret_manager")
    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_expired(self, mock_get_sm):
        """Pending code older than 10 minutes → returns None."""
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client
        mock_get_sm.return_value = mock_sm

        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        payload = json.dumps({
            "code": "expired_code",
            "state": "expired_state_1234",
            "timestamp": old_time,
        })
        mock_response = MagicMock()
        mock_response.payload.data = payload.encode("UTF-8")
        mock_client.access_secret_version.return_value = mock_response

        result = _check_pending_oauth_code("expired_state_1234")
        assert result is None

    @patch("sap_agent.agent._get_secret_manager")
    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_state_mismatch(self, mock_get_sm):
        """State in secret doesn't match requested state → returns None."""
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client
        mock_get_sm.return_value = mock_sm

        from datetime import datetime, timezone
        payload = json.dumps({
            "code": "some_code",
            "state": "different_full_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        mock_response = MagicMock()
        mock_response.payload.data = payload.encode("UTF-8")
        mock_client.access_secret_version.return_value = mock_response

        result = _check_pending_oauth_code("abc123def456ghi7_but_different")
        assert result is None
```

**Step 2: 테스트 실행 확인 (실패)**

```bash
pytest tests/test_sap_oauth.py::TestPendingOAuthCodeDetection -v
```
Expected: FAIL — `_check_pending_oauth_code` not defined

**Step 3: 구현 — `_check_pending_oauth_code` 및 `_cleanup_pending_oauth_secret`**

`sap_agent/agent.py`에 추가 (line ~60, `_TOKEN_SECRET_PREFIX` 선언 아래):

```python
_PENDING_SECRET_PREFIX = "sap-oauth-pending"
_PENDING_CODE_TTL_MINUTES = 10


def _pending_secret_id(state: str) -> str:
    """Create a Secret Manager-safe ID from the first 16 chars of state."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", state[:16])
    return f"{_PENDING_SECRET_PREFIX}-{safe}"


def _check_pending_oauth_code(state: str) -> Optional[dict]:
    """Check Secret Manager for a pending OAuth code stored by Cloud Run.

    Args:
        state: The full OAuth state parameter from Step 1.

    Returns:
        Dict with 'code', 'state', 'timestamp' if found and valid, else None.
    """
    sm = _get_secret_manager()
    if sm is None:
        return None

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return None

    secret_id = _pending_secret_id(state)

    try:
        client = sm.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        data = json.loads(response.payload.data.decode("UTF-8"))

        # Verify full state matches (secret ID uses only first 16 chars)
        if data.get("state") != state:
            logger.debug("_check_pending_oauth_code: state mismatch")
            return None

        # Check expiry
        from datetime import datetime, timezone, timedelta
        ts = datetime.fromisoformat(data["timestamp"])
        if datetime.now(timezone.utc) - ts > timedelta(minutes=_PENDING_CODE_TTL_MINUTES):
            logger.warning("_check_pending_oauth_code: expired (>%d min)", _PENDING_CODE_TTL_MINUTES)
            return None

        logger.info("_check_pending_oauth_code: found pending code for state=%.8s...", state)
        return data
    except Exception:
        return None


def _cleanup_pending_oauth_secret(state: str) -> None:
    """Delete the ephemeral pending code secret after successful exchange."""
    sm = _get_secret_manager()
    if sm is None:
        return

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return

    secret_id = _pending_secret_id(state)

    try:
        client = sm.SecretManagerServiceClient()
        secret_path = f"projects/{project_id}/secrets/{secret_id}"
        client.delete_secret(request={"name": secret_path})
        logger.info("_cleanup_pending_oauth_secret: deleted %s", secret_id)
    except Exception as e:
        logger.debug("_cleanup_pending_oauth_secret: %s", e)
```

**Step 4: 테스트 실행 확인 (통과)**

```bash
pytest tests/test_sap_oauth.py::TestPendingOAuthCodeDetection -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add sap_agent/agent.py tests/test_sap_oauth.py
git commit -m "feat: add pending OAuth code detection from Secret Manager"
```

---

### Task 3: `sap_authenticate()` Step 1 재호출 시 auto-detect 로직 통합

**Files:**
- Modify: `sap_agent/agent.py` (lines 1191-1245, Step 1 section)

**Step 1: 테스트 작성**

`tests/test_sap_oauth.py`에 추가:

```python
class TestAutoDetectPendingCode:
    """sap_authenticate() auto-detects code from Cloud Run callback."""

    @patch("sap_agent.agent._cleanup_pending_oauth_secret")
    @patch("sap_agent.agent._check_pending_oauth_code")
    @patch("sap_agent.agent._get_authenticator_for_session")
    @patch("sap_agent.agent._store_authenticator")
    @patch("sap_agent.agent._load_runtime_secrets")
    @patch("sap_agent.agent._cleanup_expired_authenticators")
    @patch.dict(os.environ, {
        "SAP_AUTH_TYPE": "sap_oauth",
        "SAP_HOST": "10.0.0.1",
        "SAP_OAUTH_CLIENT_ID": "cid",
        "SAP_OAUTH_CLIENT_SECRET": "csec",
        "SAP_OAUTH_TOKEN_URL": "https://sap/token",
        "SAP_OAUTH_AUTHORIZE_URL": "https://sap/authorize",
    })
    def test_auto_detect_triggers_code_exchange(
        self, mock_cleanup_exp, mock_load_runtime, mock_store,
        mock_get_auth, mock_check_pending, mock_cleanup_pending,
    ):
        """When cached auth has pending state and Cloud Run stored code,
        sap_authenticate() auto-detects and exchanges it."""
        from sap_agent.agent import sap_authenticate

        # Set up cached authenticator with pending auth
        mock_auth = MagicMock()
        mock_auth.uses_authorization_code = True
        mock_auth.has_valid_token_for_user.return_value = False

        mock_strategy = MagicMock()
        mock_strategy._pending_auth = {"test_state_value": ("verifier", "uid")}
        mock_strategy._last_auth_info = {
            "auth_url": "https://sap/authorize?...",
            "state": "test_state_value",
        }
        mock_strategy.get_user_token.return_value = None
        mock_auth._strategy = mock_strategy

        mock_get_auth.return_value = mock_auth

        # Cloud Run stored the code
        mock_check_pending.return_value = {
            "code": "cloud_run_captured_code",
            "state": "test_state_value",
            "timestamp": "2026-03-07T10:00:00+00:00",
        }

        # Mock the exchange
        mock_token = MagicMock()
        mock_token.sap_user = "TESTUSER"
        mock_auth.exchange_authorization_code = AsyncMock(return_value=mock_token)

        result = sap_authenticate()

        assert result["success"] is True
        assert "cloud_run_auto" in result.get("auth_source", "") or result["success"]
        mock_check_pending.assert_called_once_with("test_state_value")
        mock_cleanup_pending.assert_called_once_with("test_state_value")
```

**Step 2: 테스트 실행 확인 (실패)**

```bash
pytest tests/test_sap_oauth.py::TestAutoDetectPendingCode -v
```
Expected: FAIL — auto-detect logic not yet in `sap_authenticate()`

**Step 3: `sap_authenticate()` 수정**

`sap_agent/agent.py` 의 Step 1 분기 (line ~1191) 수정. 기존의 "Step 1: Generate SAP login URL" 섹션을 수정하여, auth URL이 이미 생성된 경우(cached auth) pending code를 먼저 확인합니다.

기존 코드 (line 1191-1216):
```python
            else:
                # Step 1: Generate SAP login URL
                # If cached authenticator already has a pending auth URL,
                # reuse it to prevent invalidating the previous session.
                if cached_auth is not None and cached_auth.uses_authorization_code:
                    strategy = cached_auth._strategy
                    if strategy._pending_auth and strategy._last_auth_info:
                        logger.info(...)
                        return {
                            "success": False,
                            "action_required": "sap_login",
                            ...
                        }
```

변경 후:
```python
            else:
                # Step 1: Generate SAP login URL or auto-detect Cloud Run callback
                if cached_auth is not None and cached_auth.uses_authorization_code:
                    strategy = cached_auth._strategy
                    if strategy._last_auth_info:
                        pending_state = strategy._last_auth_info.get("state")
                        # Auto-detect: check if Cloud Run captured the code
                        if pending_state:
                            pending = _check_pending_oauth_code(pending_state)
                            if pending:
                                logger.info(
                                    "Auto-detected OAuth code from Cloud Run "
                                    "callback (state=%.8s...)",
                                    pending_state,
                                )
                                authorization_code = pending["code"]
                                oauth_state = pending["state"]
                                # Fall through to Step 2 (code exchange)
                                # which is handled above at line 1137

                if authorization_code and oauth_state:
                    # Step 2 (moved here to handle both manual and auto-detect)
                    if cached_auth is not None and cached_auth.uses_authorization_code:
                        authenticator = cached_auth
                    else:
                        from sap_agent.sap_gw_connector.config import settings
                        settings.config = None
                        from sap_agent.sap_gw_connector.config.settings import get_config
                        from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

                        config = get_config(require_sap=True)
                        authenticator = SAPAuthenticator(config.sap)

                    logger.info(
                        "Step 2 exchange start: state=%.8s..., "
                        "redirect_uri=%s",
                        oauth_state or "",
                        os.getenv("SAP_OAUTH_REDIRECT_URI", "(not set)"),
                    )

                    async def _exchange_code():
                        return await authenticator.exchange_authorization_code(
                            authorization_code, oauth_state, user_id=user_id
                        )

                    token = asyncio.get_event_loop().run_until_complete(
                        _exchange_code()
                    )

                    _store_authenticator(user_id, authenticator, tool_context)

                    # Cleanup pending secret if it came from Cloud Run
                    _cleanup_pending_oauth_secret(oauth_state)

                    logger.info(
                        "Step 2 success: sap_user=%s, user_id=%s",
                        token.sap_user, user_id,
                    )
                    return {
                        "success": True,
                        "message": (
                            f"SAP OAuth login successful at "
                            f"{host}:{port} (client {client})"
                        ),
                        "host": host,
                        "port": port,
                        "client": client,
                        "auth_type": "sap_oauth",
                        "sap_user": token.sap_user,
                        "user_id": user_id,
                    }

                # No code available — return login URL
                if cached_auth is not None and cached_auth.uses_authorization_code:
                    strategy = cached_auth._strategy
                    if strategy._pending_auth and strategy._last_auth_info:
                        return {
                            "success": False,
                            "action_required": "sap_login",
                            "auth_url": strategy._last_auth_info["auth_url"],
                            "oauth_state": strategy._last_auth_info["state"],
                            "message": (
                                "SAP login required. Please open the following "
                                "URL in your browser to log in with your SAP "
                                "credentials. After login, you can return to "
                                "this chat — the agent will automatically "
                                "detect your login.\n\n"
                                f"Login URL: {strategy._last_auth_info['auth_url']}"
                            ),
                        }

                from sap_agent.sap_gw_connector.config import settings
                settings.config = None

                from sap_agent.sap_gw_connector.config.settings import get_config
                from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

                config = get_config(require_sap=True)
                authenticator = SAPAuthenticator(config.sap)

                auth_info = authenticator.generate_sap_auth_url(user_id)
                _store_authenticator(user_id, authenticator, tool_context)

                return {
                    "success": False,
                    "action_required": "sap_login",
                    "auth_url": auth_info["auth_url"],
                    "oauth_state": auth_info["state"],
                    "message": (
                        "SAP login required. Please open the following URL "
                        "in your browser to log in with your SAP credentials. "
                        "After login, you can return to this chat — "
                        "the agent will automatically detect your login.\n\n"
                        f"Login URL: {auth_info['auth_url']}"
                    ),
                }
```

Note: The key change is that the message no longer says "copy the full URL or code=...&state=..." — it now says "return to this chat — the agent will automatically detect your login."

**Step 4: 테스트 실행 확인 (통과)**

```bash
pytest tests/test_sap_oauth.py -v
```
Expected: ALL PASS (기존 테스트 + 새 테스트)

**Step 5: Commit**

```bash
git add sap_agent/agent.py tests/test_sap_oauth.py
git commit -m "feat: auto-detect OAuth code from Cloud Run callback in sap_authenticate"
```

---

### Task 4: Secret Manager redirect_uri를 Cloud Run URL로 업데이트

**Files:**
- No code changes — deployment configuration only

**Step 1: Cloud Run 배포**

```bash
cd cloud-run-oauth-callback

gcloud run deploy sap-oauth-callback \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project) \
  --memory 256Mi \
  --min-instances 0 \
  --max-instances 2
```

**Step 2: Cloud Run URL 확인**

```bash
CALLBACK_URL=$(gcloud run services describe sap-oauth-callback \
  --region us-central1 \
  --format "value(status.url)")
echo "Callback URL: ${CALLBACK_URL}/callback"
```

**Step 3: Secret Manager 업데이트**

```bash
# 현재 credentials 백업
gcloud secrets versions access latest --secret=sap-credentials > /tmp/sap-creds-backup.json

# oauth_redirect_uri 업데이트
python3 -c "
import json, sys
creds = json.load(open('/tmp/sap-creds-backup.json'))
creds['oauth_redirect_uri'] = '${CALLBACK_URL}/callback'
json.dump(creds, sys.stdout)
" | gcloud secrets versions add sap-credentials --data-file=-
```

**Step 4: SAP SOAUTH2 redirect_uri 업데이트**

SAP Transaction SOAUTH2에서 OAuth Client의 Redirect URI를:
```
${CALLBACK_URL}/callback
```
으로 등록 (기존 GE URL 대체 또는 추가).

**Step 5: IAM 권한 설정**

```bash
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

# Cloud Run 기본 SA에 Secret Manager 쓰기 권한
CLOUD_RUN_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CLOUD_RUN_SA}" \
  --role="roles/secretmanager.admin" \
  --condition="expression=resource.name.startsWith('projects/${PROJECT_NUMBER}/secrets/sap-oauth-pending'),title=pending-secrets-only"
```

Note: Agent Engine SA는 이미 `secretmanager.secretAccessor` 권한이 있으므로 pending secret 읽기 가능. 삭제를 위해서는 `secretmanager.admin` 또는 커스텀 역할이 필요할 수 있으므로 확인 필요.

**Step 6: Commit (docs update)**

```bash
git add docs/plans/
git commit -m "docs: add Cloud Run OAuth callback deployment steps"
```

---

### Task 5: E2E 테스트 및 문서 업데이트

**Files:**
- Modify: `docs/AUTH_SAP_OAUTH.md` (Known Constraints 섹션 업데이트)

**Step 1: Cloud Run health check**

```bash
curl -s "${CALLBACK_URL}/health"
# Expected: {"status": "ok"}
```

**Step 2: Cloud Run callback 시뮬레이션**

```bash
# 테스트 code/state로 callback 시뮬레이션
curl -s "${CALLBACK_URL}/callback?code=test_code_123&state=test_state_abcdef"
# Expected: HTML with "SAP Login Complete"

# Secret Manager에 저장되었는지 확인
gcloud secrets versions access latest --secret=sap-oauth-pending-test_state_abcde
# Expected: {"code": "test_code_123", "state": "test_state_abcdef", "timestamp": "..."}

# 정리
gcloud secrets delete sap-oauth-pending-test_state_abcde --quiet
```

**Step 3: Agent Engine 재배포**

```bash
python scripts/deploy_agent_engine.py \
  --project $(gcloud config get-value project) \
  --update projects/PROJECT_NUM/locations/us-central1/reasoningEngines/ENGINE_ID
```

**Step 4: E2E 테스트**

1. Gemini Enterprise에서 에이전트에게 "항공편 조회해줘" 요청
2. Agent가 SAP 로그인 URL 반환 (redirect_uri = Cloud Run URL)
3. URL 클릭 → SAP 로그인 → Cloud Run "로그인 완료" 페이지 확인
4. 채팅으로 돌아가서 "로그인했어" 또는 다시 "항공편 조회" 요청
5. Agent가 자동으로 code 감지 → token exchange → 결과 반환 확인

**Step 5: `docs/AUTH_SAP_OAUTH.md` Known Constraints 업데이트**

기존:
```
| OAuth callback endpoint | **UNRESOLVED** | User must manually copy code |
```

변경:
```
| OAuth callback endpoint | **RESOLVED** | Cloud Run callback proxy + auto-detect |
```

UX 메트릭 업데이트:
```
| User Auth Steps | 3 | 2 | -1 step |
```

**Step 6: Commit**

```bash
git add docs/AUTH_SAP_OAUTH.md
git commit -m "docs: update OAuth callback status to RESOLVED with Cloud Run proxy"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Cloud Run 서비스 생성 | `cloud-run-oauth-callback/` (3 files) |
| 2 | Pending code 감지 함수 | `agent.py`, `test_sap_oauth.py` |
| 3 | sap_authenticate() auto-detect 통합 | `agent.py`, `test_sap_oauth.py` |
| 4 | 배포 + Secret Manager + SAP 설정 | deployment config |
| 5 | E2E 테스트 + 문서 업데이트 | `AUTH_SAP_OAUTH.md` |
