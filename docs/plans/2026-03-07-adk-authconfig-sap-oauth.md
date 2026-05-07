# ADK AuthConfig SAP OAuth Integration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Gemini Enterprise 사용자가 Agent Engine Extension 호출 시 ADK의 공식 OAuth 흐름을 통해 SAP 인증을 수행하도록 전환하여, per-user 토큰 격리 및 보안 문제를 해결한다.

**Architecture:** `sap_authenticate`를 ADK의 `AuthenticatedFunctionTool` 패턴으로 전환한다. SAP OAuth endpoints를 ADK `AuthConfig`로 구성하고, `tool_context.request_credential()` / `tool_context.get_auth_response()`를 사용하여 Gemini Enterprise가 `adk_request_credential` 이벤트를 처리하도록 한다. SAP access_token은 ADK credential 시스템을 통해 전달되며, 기존 커스텀 OAuth 흐름(Cloud Run callback, Secret Manager pending code)은 fallback으로 유지한다.

**Tech Stack:** Google ADK AuthConfig/AuthCredential/AuthenticatedFunctionTool, SAP OAuth 2.0 Authorization Code, Vertex AI Agent Engine, fastapi.openapi.models.OAuth2

---

## Background

### 현재 문제
- Gemini Enterprise → Agent Engine 호출 시 `user_id="default-user-id"` (SDK 기본값)
- `session_id`도 매 요청마다 새로 생성 (비영속)
- 모든 유저가 동일한 SAP 토큰을 공유하는 보안 취약점

### ADK Auth 메커니즘
1. Tool이 `tool_context.request_credential(auth_config)` 호출
2. ADK가 `adk_request_credential` function call 이벤트 생성
3. Gemini Enterprise(클라이언트)가 OAuth consent 흐름 처리
4. 사용자 인증 후 auth code → ADK가 token exchange → `credential` 반환
5. Tool이 `tool_context.get_auth_response(auth_config)`로 credential 수신

### 핵심 파일
- `sap_agent/agent.py` — 메인 에이전트, SAP 도구 함수들
- `sap_agent/sap_gw_connector/core/auth.py` — SAP 인증 전략
- `sap_agent/sap_gw_connector/config/settings.py` — SAP 연결 설정
- `scripts/deploy_agent_engine.py` — 배포 스크립트

---

## Task 1: SAP OAuth AuthConfig 생성 모듈

SAP OAuth endpoints를 ADK AuthConfig로 구성하는 헬퍼 모듈을 생성한다.

**Files:**
- Create: `sap_agent/sap_auth_config.py`
- Test: `tests/test_sap_auth_config.py`

**Step 1: Write the failing test**

```python
# tests/test_sap_auth_config.py
import os
import pytest


def test_build_sap_auth_config_returns_auth_config():
    """AuthConfig with SAP OAuth endpoints is created from env vars."""
    os.environ["SAP_OAUTH_CLIENT_ID"] = "test_client_id"
    os.environ["SAP_OAUTH_CLIENT_SECRET"] = "test_client_secret"
    os.environ["SAP_OAUTH_AUTHORIZE_URL"] = "https://sap.example.com/oauth/authorize"
    os.environ["SAP_OAUTH_TOKEN_URL"] = "https://sap.example.com/oauth/token"
    os.environ["SAP_OAUTH_SCOPE"] = "API_ACCESS"

    from sap_agent.sap_auth_config import build_sap_auth_config

    config = build_sap_auth_config()

    assert config is not None
    assert config.auth_scheme is not None
    assert config.raw_auth_credential is not None
    assert config.raw_auth_credential.oauth2.client_id == "test_client_id"
    assert config.raw_auth_credential.oauth2.client_secret == "test_client_secret"


def test_build_sap_auth_config_returns_none_when_missing_env():
    """Returns None when required env vars are missing."""
    for key in ["SAP_OAUTH_CLIENT_ID", "SAP_OAUTH_CLIENT_SECRET",
                "SAP_OAUTH_AUTHORIZE_URL", "SAP_OAUTH_TOKEN_URL"]:
        os.environ.pop(key, None)

    from sap_agent.sap_auth_config import build_sap_auth_config

    config = build_sap_auth_config()
    assert config is None
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sap_auth_config.py -v`
Expected: FAIL with "ModuleNotFoundError" or "ImportError"

**Step 3: Write minimal implementation**

```python
# sap_agent/sap_auth_config.py
"""Build ADK AuthConfig for SAP OAuth Authorization Code flow."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def build_sap_auth_config() -> Optional["AuthConfig"]:
    """Build an ADK AuthConfig from SAP OAuth environment variables.

    Returns AuthConfig if all required env vars are set, else None.
    """
    client_id = os.getenv("SAP_OAUTH_CLIENT_ID")
    client_secret = os.getenv("SAP_OAUTH_CLIENT_SECRET")
    authorize_url = os.getenv("SAP_OAUTH_AUTHORIZE_URL")
    token_url = os.getenv("SAP_OAUTH_TOKEN_URL")
    scope = os.getenv("SAP_OAUTH_SCOPE", "")
    redirect_uri = os.getenv("SAP_OAUTH_REDIRECT_URI", "")

    if not all([client_id, client_secret, authorize_url, token_url]):
        logger.warning("SAP OAuth env vars incomplete, skipping AuthConfig")
        return None

    from google.adk.auth.auth_credential import (
        AuthCredential,
        AuthCredentialTypes,
        OAuth2Auth,
    )
    from google.adk.auth.auth_tool import AuthConfig
    from fastapi.openapi.models import OAuth2, OAuthFlowAuthorizationCode, OAuthFlows

    auth_scheme = OAuth2(
        flows=OAuthFlows(
            authorizationCode=OAuthFlowAuthorizationCode(
                authorizationUrl=authorize_url,
                tokenUrl=token_url,
                scopes={s: s for s in scope.split() if s} if scope else {},
            ),
        ),
    )

    raw_credential = AuthCredential(
        auth_type=AuthCredentialTypes.OAUTH2,
        oauth2=OAuth2Auth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri or None,
        ),
    )

    return AuthConfig(
        auth_scheme=auth_scheme,
        raw_auth_credential=raw_credential,
    )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sap_auth_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add sap_agent/sap_auth_config.py tests/test_sap_auth_config.py
git commit -m "feat: add SAP OAuth ADK AuthConfig builder"
```

---

## Task 2: sap_authenticate를 ADK auth 흐름으로 전환

기존 `sap_authenticate`에서 ADK `request_credential`/`get_auth_response` 패턴을 추가한다. ADK auth가 가능하면 우선 사용하고, 기존 커스텀 흐름은 fallback으로 유지한다.

**Files:**
- Modify: `sap_agent/agent.py` (sap_authenticate 함수)
- Test: `tests/test_adk_auth_flow.py`

**Step 1: Write the failing test**

```python
# tests/test_adk_auth_flow.py
"""Test ADK auth integration in sap_authenticate."""
import os
import pytest
from unittest.mock import MagicMock, patch


def test_sap_authenticate_requests_credential_when_no_auth():
    """When no cached token and ADK auth available, request_credential is called."""
    os.environ["SAP_AUTH_TYPE"] = "sap_oauth"
    os.environ["SAP_OAUTH_CLIENT_ID"] = "test_id"
    os.environ["SAP_OAUTH_CLIENT_SECRET"] = "test_secret"
    os.environ["SAP_OAUTH_AUTHORIZE_URL"] = "https://sap/oauth/authorize"
    os.environ["SAP_OAUTH_TOKEN_URL"] = "https://sap/oauth/token"

    mock_tool_context = MagicMock()
    mock_tool_context.get_auth_response.return_value = None
    mock_tool_context.state = {}
    # Simulate invocation_context with default-user-id
    mock_tool_context._invocation_context.user_id = "default-user-id"
    mock_tool_context._invocation_context.session.id = "test-session"

    from sap_agent.agent import sap_authenticate

    result = sap_authenticate(tool_context=mock_tool_context)

    # Should request credential via ADK when user_id is default
    if result.get("action_required") == "adk_oauth":
        mock_tool_context.request_credential.assert_called_once()


def test_sap_authenticate_uses_adk_credential():
    """When ADK auth response has access_token, use it for SAP."""
    os.environ["SAP_AUTH_TYPE"] = "sap_oauth"
    os.environ["SAP_OAUTH_CLIENT_ID"] = "test_id"
    os.environ["SAP_OAUTH_CLIENT_SECRET"] = "test_secret"
    os.environ["SAP_OAUTH_AUTHORIZE_URL"] = "https://sap/oauth/authorize"
    os.environ["SAP_OAUTH_TOKEN_URL"] = "https://sap/oauth/token"

    mock_credential = MagicMock()
    mock_credential.oauth2.access_token = "sap_access_token_123"
    mock_credential.oauth2.refresh_token = "sap_refresh_token_456"

    mock_tool_context = MagicMock()
    mock_tool_context.get_auth_response.return_value = mock_credential
    mock_tool_context.state = {}
    mock_tool_context._invocation_context.user_id = "default-user-id"
    mock_tool_context._invocation_context.session.id = "test-session"

    from sap_agent.agent import sap_authenticate

    result = sap_authenticate(tool_context=mock_tool_context)

    assert result.get("success") is True or result.get("action_required") != "sap_login"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adk_auth_flow.py -v`
Expected: FAIL (current sap_authenticate doesn't use ADK auth)

**Step 3: Modify sap_authenticate**

`sap_agent/agent.py`의 `sap_authenticate` 함수 시작 부분에 ADK auth 분기 추가:

```python
# In sap_authenticate(), after determining user_id:

# --- ADK Auth Flow (for Gemini Enterprise / AgentSpace) ---
# When user_id is "default-user-id", Gemini Enterprise didn't pass
# real identity. Use ADK's credential system to trigger OAuth via
# the client (Gemini Enterprise) which handles the redirect flow.
from sap_agent.sap_auth_config import build_sap_auth_config

sap_auth_config = build_sap_auth_config()

if sap_auth_config and tool_context is not None and hasattr(tool_context, "get_auth_response"):
    # Check if ADK already has a credential from a previous auth flow
    adk_credential = tool_context.get_auth_response(sap_auth_config)

    if adk_credential and adk_credential.oauth2 and adk_credential.oauth2.access_token:
        # ADK credential available — use it
        logger.info(
            "sap_authenticate: using ADK credential (access_token present, "
            "user_id=%s)", user_id,
        )
        # Derive a unique uid from the session_id when user_id is default
        effective_uid = user_id
        if user_id == "default-user-id" and tool_context is not None:
            try:
                sid = tool_context._invocation_context.session.id
                effective_uid = f"session-{sid}"
                logger.info(
                    "sap_authenticate: default-user-id detected, "
                    "using session-based uid=%s", effective_uid,
                )
            except Exception:
                pass

        # Build authenticator from ADK credential
        _build_authenticator_from_adk_credential(
            effective_uid, adk_credential, tool_context
        )
        return {
            "success": True,
            "message": f"Authenticated with SAP via ADK OAuth at {host}:{port}",
            "host": host,
            "port": port,
            "client": client,
            "auth_type": "sap_oauth_adk",
            "user_id": effective_uid,
        }

    # No credential yet — request one via ADK
    if hasattr(tool_context, "request_credential"):
        logger.info(
            "sap_authenticate: requesting credential via ADK auth flow"
        )
        tool_context.request_credential(sap_auth_config)
        return {
            "success": False,
            "action_required": "adk_oauth",
            "message": (
                "SAP authentication required. "
                "Please authorize access to SAP when prompted."
            ),
        }
```

**Step 4: Implement helper function `_build_authenticator_from_adk_credential`**

```python
# In sap_agent/agent.py, add before sap_authenticate:

def _build_authenticator_from_adk_credential(
    uid: str,
    adk_credential: Any,
    tool_context: Optional[Any] = None,
) -> None:
    """Build a SAPAuthenticator from an ADK-provided OAuth credential.

    This bridges the ADK auth system with our existing SAPAuthenticator
    infrastructure, allowing the rest of the code (sap_query, sap_get_entity)
    to work unchanged.
    """
    from datetime import datetime, timedelta
    from sap_agent.sap_gw_connector.core.auth import (
        SAPAuthenticator,
        SAPUserToken,
    )
    from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig

    config = SAPConnectionConfig.from_env()
    authenticator = SAPAuthenticator(config=config)
    strategy = authenticator._strategy

    expires_in = adk_credential.oauth2.expires_in or 3600
    token = SAPUserToken(
        access_token=adk_credential.oauth2.access_token,
        refresh_token=adk_credential.oauth2.refresh_token,
        token_type="Bearer",
        expires_at=datetime.utcnow() + timedelta(seconds=max(expires_in - 60, 60)),
    )

    strategy._cache_token(uid, token)
    strategy.set_current_user(uid)

    _store_authenticator(uid, authenticator, tool_context)
    logger.info(
        "_build_authenticator_from_adk_credential: cached for uid=%s", uid,
    )
```

**Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adk_auth_flow.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add sap_agent/agent.py tests/test_adk_auth_flow.py
git commit -m "feat: integrate ADK AuthConfig into sap_authenticate"
```

---

## Task 3: sap_query, sap_get_entity에 ADK credential fallback 추가

query/get_entity 도구에서도 인증이 없을 때 ADK auth를 시도하도록 한다.

**Files:**
- Modify: `sap_agent/agent.py` (sap_query, sap_get_entity 함수)

**Step 1: Write the failing test**

```python
# tests/test_adk_auth_flow.py (append)

def test_sap_query_triggers_adk_auth_when_no_authenticator():
    """sap_query returns auth required when no authenticator and ADK available."""
    os.environ["SAP_AUTH_TYPE"] = "sap_oauth"
    os.environ["SAP_OAUTH_CLIENT_ID"] = "test_id"
    os.environ["SAP_OAUTH_CLIENT_SECRET"] = "test_secret"
    os.environ["SAP_OAUTH_AUTHORIZE_URL"] = "https://sap/oauth/authorize"
    os.environ["SAP_OAUTH_TOKEN_URL"] = "https://sap/oauth/token"

    mock_tool_context = MagicMock()
    mock_tool_context.get_auth_response.return_value = None
    mock_tool_context.state = {}
    mock_tool_context._invocation_context.user_id = "default-user-id"
    mock_tool_context._invocation_context.session.id = "test-session"

    from sap_agent.agent import sap_query

    result = sap_query(
        service_path="/sap/opu/odata/sap/API_TEST",
        entity_set="TestSet",
        tool_context=mock_tool_context,
    )

    # Should either request credential or tell user to authenticate
    assert "error" in result or "action_required" in result
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adk_auth_flow.py::test_sap_query_triggers_adk_auth_when_no_authenticator -v`

**Step 3: Add ADK auth check in sap_query and sap_get_entity**

In both `sap_query` and `sap_get_entity`, in the `except SAPAuthenticationError` block and at the start when no authenticator is found, add:

```python
# At the point where "Please call sap_authenticate first" would be returned:
from sap_agent.sap_auth_config import build_sap_auth_config

sap_auth_config = build_sap_auth_config()
if sap_auth_config and tool_context is not None and hasattr(tool_context, "request_credential"):
    adk_cred = tool_context.get_auth_response(sap_auth_config)
    if adk_cred and adk_cred.oauth2 and adk_cred.oauth2.access_token:
        # Credential available, build authenticator and retry
        effective_uid = _get_uid_from_context(tool_context) or "default-user-id"
        if effective_uid == "default-user-id":
            try:
                effective_uid = f"session-{tool_context._invocation_context.session.id}"
            except Exception:
                pass
        _build_authenticator_from_adk_credential(effective_uid, adk_cred, tool_context)
        authenticator = _get_authenticator_for_session(tool_context)
        # Continue with query using the new authenticator
    else:
        tool_context.request_credential(sap_auth_config)
        return {
            "error": "SAP authentication required. Please authorize when prompted.",
            "action_required": "adk_oauth",
        }
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adk_auth_flow.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add sap_agent/agent.py tests/test_adk_auth_flow.py
git commit -m "feat: add ADK auth fallback to sap_query and sap_get_entity"
```

---

## Task 4: Agent instruction 및 배포 설정 업데이트

Agent instruction을 ADK auth 흐름에 맞게 업데이트하고, 배포 스크립트에 `authlib` 의존성을 추가한다.

**Files:**
- Modify: `sap_agent/agent.py` (AGENT_INSTRUCTION)
- Modify: `scripts/deploy_agent_engine.py` (REQUIREMENTS)

**Step 1: Update AGENT_INSTRUCTION**

`AGENT_INSTRUCTION`에서 SAP 인증 관련 안내를 업데이트:

```python
# Replace the authentication instruction section with:
"""
## Authentication Flow
1. When you need SAP data, call sap_authenticate or any SAP tool.
2. If the user hasn't authenticated yet:
   - In Gemini Enterprise: The system will prompt the user to authorize SAP access.
     Wait for authorization to complete, then retry.
   - In other environments: A SAP login URL will be provided for manual login.
3. After successful authentication, proceed with SAP queries.
4. If you receive action_required="adk_oauth", tell the user:
   "Please complete the SAP authorization prompt to continue."
"""
```

**Step 2: Add authlib to REQUIREMENTS**

In `scripts/deploy_agent_engine.py`, add to REQUIREMENTS list:

```python
"authlib>=1.3.0",
```

**Step 3: Commit**

```bash
git add sap_agent/agent.py scripts/deploy_agent_engine.py
git commit -m "feat: update agent instruction and deploy deps for ADK auth"
```

---

## Task 5: 디버그 로그로 ADK auth 흐름 검증 강화

배포 후 ADK auth 흐름을 검증할 수 있도록 핵심 지점에 로그를 추가한다.

**Files:**
- Modify: `sap_agent/agent.py`

**Step 1: Add logging to key ADK auth points**

```python
# In sap_authenticate, after the ADK auth branch:
logger.info(
    "sap_authenticate: ADK auth result — "
    "has_auth_config=%s, has_tool_context_auth=%s, "
    "adk_credential=%s, user_id=%s",
    sap_auth_config is not None,
    hasattr(tool_context, "get_auth_response") if tool_context else False,
    adk_credential is not None if 'adk_credential' in dir() else "N/A",
    user_id,
)
```

**Step 2: Commit**

```bash
git add sap_agent/agent.py
git commit -m "feat: add ADK auth flow debug logging"
```

---

## Task 6: 로컬 테스트 (adk web)

ADK auth 흐름을 로컬에서 `adk web`으로 테스트한다.

**Step 1: Run adk web**

```bash
cd sap_agent && ../.venv/bin/adk web .
```

**Step 2: Test in browser**

1. Open `http://localhost:8000`
2. Send: "SAP 서비스 목록 조회해줘"
3. Verify: `adk_request_credential` 이벤트가 발생하는지 확인
4. OAuth consent 프롬프트가 표시되면 SAP 로그인 수행
5. 인증 후 SAP 데이터가 반환되는지 확인

**Step 3: Check logs**

```bash
# Look for ADK auth flow logs
grep "ADK" sap_agent.log
```

---

## Task 7: Agent Engine 배포 및 Gemini Enterprise 테스트

**Step 1: Deploy to Agent Engine**

```bash
.venv/bin/python scripts/deploy_agent_engine.py \
  --project <YOUR_PROJECT_ID> \
  --update <AGENT_ENGINE_RESOURCE_NAME>
```

**Step 2: Test via Gemini Enterprise**

1. Gemini Enterprise에서 SAP agent를 호출
2. SAP 인증 프롬프트가 표시되는지 확인
3. 다른 사용자 계정으로 동일 테스트 수행
4. 두 사용자의 SAP 토큰이 격리되는지 Cloud Logging으로 확인

**Step 3: Verify logs**

```bash
gcloud logging read \
  'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND "ADK auth"' \
  --project=<YOUR_PROJECT_ID> --limit=20 --freshness=1h \
  --format="table(timestamp,textPayload)"
```

**Step 4: Commit final state**

```bash
git add -A
git commit -m "feat: complete ADK AuthConfig SAP OAuth integration"
```

---

## Risk & Unknowns

| Risk | Mitigation |
|------|-----------|
| Gemini Enterprise가 third-party OAuth redirect를 지원하지 않을 수 있음 | 기존 커스텀 OAuth 흐름을 fallback으로 유지. ADK auth 실패 시 자동 전환 |
| SAP OAuth PKCE 요구사항이 ADK authlib와 호환되지 않을 수 있음 | SAP OAuth 클라이언트에서 PKCE를 optional로 설정하거나, ADK auth 후 별도 PKCE token exchange 구현 |
| `authlib` 의존성이 Agent Engine에서 설치 실패할 수 있음 | REQUIREMENTS에 pinned version 사용, 배포 전 로컬 검증 |
| ADK credential이 turn 간 유지되지 않을 수 있음 (in-memory session) | session_id 기반 uid로 Secret Manager 토큰 저장 유지 |
| SAP token_url에 대한 네트워크 접근이 ADK auth handler에서 불가할 수 있음 (PSC 필요) | ADK가 token exchange를 수행하는 위치 확인. Agent Engine 내부에서 실행되면 PSC 통해 접근 가능 |

## Rollback Plan

ADK auth 통합이 실패할 경우:
1. `sap_auth_config.py`의 `build_sap_auth_config()`가 `None` 반환하도록 변경
2. 기존 커스텀 OAuth 흐름이 자동으로 활성화됨 (fallback 구조)
3. 코드 변경 없이 환경변수 제거만으로도 rollback 가능
