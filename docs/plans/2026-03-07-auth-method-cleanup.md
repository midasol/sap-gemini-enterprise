# Authentication Method Cleanup Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove OAuth 2.0 Client Credentials, Firebase + OAuth 2.0, and Basic Auth modes, keeping only SAP OAuth Authorization Code (Interactive) flow.

**Architecture:** The project uses a Strategy Pattern for authentication (`auth.py`). We will remove `BasicAuthStrategy`, `OAuthClientCredentialsStrategy`, `FirebaseOAuthStrategy`, `PrincipalPropagationStrategy` and their associated tokens/exceptions, keeping only `SAPAuthorizationCodeStrategy`. The `SAPAuthenticator` facade will be simplified to only create that one strategy. Config validation, `.env` comments, agent.py tool functions, tests, docs, and the Firebase web server will all be updated accordingly.

**Tech Stack:** Python, Pydantic, aiohttp, pytest, FastAPI

---

## Summary of Removals

| Component | What to Remove |
|-----------|---------------|
| **Tokens** | `BasicAuthToken`, `FirebaseUser`, `FirebaseSAPToken`, `SAPUserToken.firebase_uid/firebase_email` |
| **Strategies** | `BasicAuthStrategy`, `OAuthClientCredentialsStrategy`, `FirebaseOAuthStrategy`, `PrincipalPropagationStrategy` |
| **Exceptions** | `SAPFirebaseAuthError`, `SAPPrincipalPropagationError` |
| **Config fields** | `username`, `password`, `firebase_project_id`, `firebase_credentials_path`, `principal_propagation_enabled`, `principal_propagation_grant_type` |
| **Config auth_type values** | `"basic"`, `"oauth"`, `"firebase"` (keep only `"sap_oauth"`) |
| **Files to delete** | `sap_agent/web/auth_server.py`, `sap_agent/web/templates/firebase_login.html`, `sap_agent/web/__init__.py`, `tests/test_firebase_auth.py`, `tests/test_auth_server.py` |
| **Docs to delete** | `docs/AUTH_BASIC.md`, `docs/AUTH_OAUTH2.md`, `docs/AUTH_FIREBASE.md`, `docs/FIREBASE_OAUTH2_INTEGRATION_PLAN.md`, `docs/SAP_PRINCIPAL_PROPAGATION_ANALYSIS.md`, `docs/KR/AUTH_BASIC.md`, `docs/KR/AUTH_OAUTH2.md`, `docs/KR/AUTH_FIREBASE.md`, `docs/KR/FIREBASE_OAUTH2_INTEGRATION_PLAN.md` |
| **Agent.py** | Firebase init code, basic/oauth/firebase branches in `sap_authenticate()`, per-user Firebase cache globals |

## What to Keep

- `AuthToken` (base class)
- `OAuthToken` (used by SAPAuthorizationCodeStrategy's token request)
- `SAPUserToken` (per-user token for auth code flow)
- `SAPAuthorizationCodeStrategy` (the only strategy)
- `SAPAuthenticator` (simplified facade)
- `SAPAuthenticationError`, `SAPOAuthError`, `SAPConnectionError`, `SAPRequestError`, `SAPTimeoutError`, `SAPValidationError`

---

### Task 1: Clean up `core/exceptions.py` - Remove Firebase/PP exceptions

**Files:**
- Modify: `sap_agent/sap_gw_connector/core/exceptions.py`

**Step 1: Remove `SAPFirebaseAuthError` and `SAPPrincipalPropagationError` classes**

Remove lines 32-45 from `exceptions.py`:

```python
# DELETE these two classes:
# class SAPFirebaseAuthError(SAPAuthenticationError): ...
# class SAPPrincipalPropagationError(SAPAuthenticationError): ...
```

The file should keep: `SAPError`, `SAPAuthenticationError`, `SAPOAuthError`, `SAPConnectionError`, `SAPRequestError`, `SAPTimeoutError`, `SAPValidationError`.

**Step 2: Commit**

```bash
git add sap_agent/sap_gw_connector/core/exceptions.py
git commit -m "refactor: remove SAPFirebaseAuthError and SAPPrincipalPropagationError"
```

---

### Task 2: Clean up `core/auth.py` - Remove tokens, strategies, simplify facade

**Files:**
- Modify: `sap_agent/sap_gw_connector/core/auth.py`

**Step 1: Remove removed exception imports**

In the imports at line 13-18, remove `SAPFirebaseAuthError` and `SAPPrincipalPropagationError`:

```python
from sap_agent.sap_gw_connector.core.exceptions import (
    SAPAuthenticationError,
    SAPConnectionError,
)
```

**Step 2: Remove `BasicAuthToken` class (lines 54-68)**

Delete the entire `BasicAuthToken` dataclass.

**Step 3: Remove `FirebaseUser` class (lines 84-101)**

Delete the entire `FirebaseUser` dataclass.

**Step 4: Remove `FirebaseSAPToken` class (lines 104-115)**

Delete the entire `FirebaseSAPToken` dataclass.

**Step 5: Remove firebase-related fields from `SAPUserToken` (lines 118-136)**

Remove `firebase_uid` and `firebase_email` fields. Keep `access_token`, `refresh_token`, `token_type`, `scope`, `sap_user`, `expires_at`.

```python
@dataclass
class SAPUserToken(AuthToken):
    """Per-user SAP access token obtained via Authorization Code flow."""

    access_token: str = field(default="", repr=False)
    refresh_token: Optional[str] = field(default=None, repr=False)
    token_type: str = "Bearer"
    scope: Optional[str] = None
    sap_user: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return bool(self.access_token and not self.is_expired)
```

**Step 6: Remove `BasicAuthStrategy` class (lines 173-349)**

Delete the entire class.

**Step 7: Remove `OAuthClientCredentialsStrategy` class (lines 357-462)**

Delete the entire class.

**Step 8: Remove `FirebaseOAuthStrategy` class (lines 470-616)**

Delete the entire class.

**Step 9: Remove `PrincipalPropagationStrategy` class (lines 624-1176)**

Delete the entire class.

**Step 10: Remove firebase-related fields from `SAPAuthorizationCodeStrategy`**

In `SAPAuthorizationCodeStrategy._token_request()` (around line 1440), remove the `firebase_uid` and `firebase_email` fields from the `SAPUserToken` creation. They should just not be there since we removed those fields.

**Step 11: Simplify `SAPAuthenticator` facade**

Replace the entire facade with a simplified version:

```python
class SAPAuthenticator:
    """Facade that delegates to SAPAuthorizationCodeStrategy.

    Only supports auth_type='sap_oauth' (Authorization Code with PKCE).
    """

    def __init__(
        self,
        config: SAPConnectionConfig,
        auth_endpoint: Optional["AuthEndpointConfig"] = None,
        services_config: Optional["ServicesYAMLConfig"] = None,
    ):
        self.config = config
        if config.auth_type != "sap_oauth":
            raise SAPAuthenticationError(
                f"Unsupported auth_type '{config.auth_type}'. "
                f"Only 'sap_oauth' is supported."
            )
        self._strategy = SAPAuthorizationCodeStrategy(config)

    async def get_valid_token(self) -> AuthToken:
        return await self._strategy.get_valid_token()

    def get_auth_headers(self, token: AuthToken) -> Dict[str, str]:
        return self._strategy.get_auth_headers(token)

    async def invalidate_token(self) -> None:
        await self._strategy.invalidate_token()

    @property
    def requires_csrf(self) -> bool:
        return self._strategy.requires_csrf

    @property
    def uses_authorization_code(self) -> bool:
        return True

    def generate_sap_auth_url(self, user_id: str) -> Dict[str, str]:
        return self._strategy.generate_auth_url(user_id)

    async def exchange_authorization_code(
        self, authorization_code: str, state: str,
        user_id: Optional[str] = None,
    ) -> SAPUserToken:
        return await self._strategy.exchange_code(
            authorization_code, state, user_id=user_id
        )

    def set_current_user(self, user_id: str) -> None:
        self._strategy.set_current_user(user_id)

    def has_valid_token_for_user(self, user_id: str) -> bool:
        return self._strategy.has_valid_token(user_id)
```

Remove the deleted methods: `authenticate_with_firebase()`, `authenticate_user_with_propagation()`, `uses_principal_propagation`, `firebase_user`.

**Step 12: Clean up OAuthToken**

Keep `OAuthToken` since it may still be useful as a base concept, but update its docstring:

```python
@dataclass
class OAuthToken(AuthToken):
    """OAuth 2.0 access token"""
    ...
```

Actually, check if `OAuthToken` is used anywhere in the remaining code. If only `SAPUserToken` is used by `SAPAuthorizationCodeStrategy`, then `OAuthToken` can be removed too.

Looking at the code: `SAPAuthorizationCodeStrategy` uses `SAPUserToken`, not `OAuthToken`. So `OAuthToken` can be removed.

**Step 13: Verify the file compiles**

```bash
cd /Users/sanggyulee/my-project/python-project/sap-adk-agent
python -c "from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator, SAPAuthorizationCodeStrategy, SAPUserToken, AuthToken, AuthStrategy"
```

Expected: No import errors.

**Step 14: Commit**

```bash
git add sap_agent/sap_gw_connector/core/auth.py
git commit -m "refactor: remove Basic/OAuth/Firebase strategies, keep only SAPAuthorizationCodeStrategy"
```

---

### Task 3: Clean up `config/settings.py` - Simplify config

**Files:**
- Modify: `sap_agent/sap_gw_connector/config/settings.py`

**Step 1: Remove unused config fields from `SAPConnectionConfig`**

Remove these fields:
- `username` (line 18)
- `password` (line 19)
- `firebase_project_id` (lines 27-29)
- `firebase_credentials_path` (lines 30-32)
- `principal_propagation_enabled` (lines 33-40)
- `principal_propagation_grant_type` (lines 41-48)

**Step 2: Change `auth_type` default and validation**

Change `auth_type` default from `"basic"` to `"sap_oauth"`:

```python
auth_type: str = Field("sap_oauth", description="Authentication type (only 'sap_oauth' supported)")
```

Update `validate_auth_type`:

```python
@field_validator("auth_type")
@classmethod
def validate_auth_type(cls, v: str) -> str:
    if v.lower() != "sap_oauth":
        raise ValueError("Only auth_type 'sap_oauth' is supported")
    return v.lower()
```

**Step 3: Simplify `validate_credentials` model validator**

Replace the entire validator with just the sap_oauth check:

```python
@model_validator(mode="after")
def validate_credentials(self) -> "SAPConnectionConfig":
    if (
        not self.oauth_client_id
        or not self.oauth_client_secret
        or not self.oauth_token_url
    ):
        raise ValueError(
            "oauth_client_id, oauth_client_secret, and oauth_token_url "
            "are required for sap_oauth authentication"
        )
    if not self.oauth_authorize_url:
        raise ValueError(
            "oauth_authorize_url is required for sap_oauth authentication "
            "(SAP OAuth Authorization endpoint)"
        )
    return self
```

**Step 4: Simplify `validate_required_env_vars` in `AppConfig`**

```python
def validate_required_env_vars(self) -> None:
    required_vars = [
        "SAP_HOST",
        "SAP_OAUTH_CLIENT_ID",
        "SAP_OAUTH_CLIENT_SECRET",
        "SAP_OAUTH_TOKEN_URL",
        "SAP_OAUTH_AUTHORIZE_URL",
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")
```

**Step 5: Update `load_from_env` fallback (remove basic auth fallback)**

```python
@classmethod
def load_from_env(cls, require_sap: bool = True) -> "AppConfig":
    try:
        sap_config = SAPConnectionConfig()
    except Exception as e:
        if require_sap:
            raise e
        # Use minimal config for development/testing
        sap_config = SAPConnectionConfig(
            host="localhost",
            auth_type="sap_oauth",
            oauth_client_id="test",
            oauth_client_secret="test",
            oauth_token_url="https://localhost/token",
            oauth_authorize_url="https://localhost/authorize",
        )
    return cls(
        sap=sap_config,
        server=GWServerConfig(),
        security=SecurityConfig(),
    )
```

**Step 6: Verify**

```bash
python -c "from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig; c = SAPConnectionConfig(host='test', oauth_client_id='id', oauth_client_secret='sec', oauth_token_url='https://t/token', oauth_authorize_url='https://t/auth'); print(c.auth_type)"
```

Expected: `sap_oauth`

**Step 7: Commit**

```bash
git add sap_agent/sap_gw_connector/config/settings.py
git commit -m "refactor: simplify SAPConnectionConfig to only support sap_oauth"
```

---

### Task 4: Clean up `agent.py` - Remove Firebase/Basic/OAuth branches

**Files:**
- Modify: `sap_agent/agent.py`

**Step 1: Remove Firebase-related globals and imports**

Remove these globals (around lines 34-43):
- `_firebase_initialized`
- `_user_authenticators`
- `_user_authenticators_lock`
- `_last_authenticated_uid`
- `_MAX_CACHED_USERS`

**Step 2: Remove `_ensure_firebase_initialized()` function (around line 330)**

Delete the entire function.

**Step 3: Simplify `ensure_sap_config()` function**

Remove the branches for `basic` and `oauth`/`firebase` auth types. Only keep the `sap_oauth` path:

```python
def ensure_sap_config():
    """Ensure SAP configuration is loaded."""
    from sap_agent.sap_gw_connector.config import settings
    settings.config = None
    settings.get_config(require_sap=True)
```

**Step 4: Simplify `sap_authenticate()` function**

Remove the `firebase_id_token` parameter and all branches for `basic`, `oauth`, `firebase` auth types. Keep only the `sap_oauth` flow logic (the interactive Authorization Code flow with PKCE). This is the largest change in agent.py.

The function signature should be:

```python
def sap_authenticate(
    tool_context: Optional[ToolContext] = None,
) -> dict:
```

Keep only the `sap_oauth` branch logic that:
1. Gets/creates the authenticator
2. Auto-detects OAuth code from Cloud Run callback
3. Generates auth URL for user login
4. Exchanges authorization code for token

**Step 5: Remove Firebase UID lookups in `sap_query()` and `sap_get_entity()`**

In `_get_or_create_authenticated_client()` and similar helper functions, remove `firebase_uid` session state lookups. Replace with simpler user identification.

**Step 6: Remove the Firebase auth description from `sap_authenticate` docstring**

Update the docstring to only describe the SAP OAuth Authorization Code flow.

**Step 7: Verify**

```bash
python -c "from sap_agent.agent import sap_authenticate; print('OK')"
```

**Step 8: Commit**

```bash
git add sap_agent/agent.py
git commit -m "refactor: simplify agent.py to only support SAP OAuth Authorization Code flow"
```

---

### Task 5: Clean up `.env` file

**Files:**
- Modify: `sap_agent/.env`

**Step 1: Remove Basic Auth and Firebase comments/vars**

Remove:
- Option 1 (Basic Auth) description block
- Option 2 (OAuth 2.0 Client Credentials) description block
- Option 3 (Firebase + OAuth 2.0) description block
- `SAP_USERNAME` and `SAP_PASSWORD` commented lines
- `SAP_FIREBASE_PROJECT_ID` and `SAP_FIREBASE_CREDENTIALS_PATH` commented lines
- `AUTH_SERVER_URL` line

Keep:
- Google/Vertex AI config
- SAP server config
- OAuth 2.0 config (used by sap_oauth)
- `SAP_AUTH_TYPE=sap_oauth`

**Step 2: Update description header**

```
# =============================================================================
# SAP Authentication Configuration
# =============================================================================
# Authentication: SAP OAuth 2.0 Authorization Code with PKCE
#
# Required:
#   SAP_HOST               - SAP Gateway server hostname
#   SAP_OAUTH_CLIENT_ID    - OAuth 2.0 client ID
#   SAP_OAUTH_CLIENT_SECRET- OAuth 2.0 client secret
#   SAP_OAUTH_TOKEN_URL    - OAuth 2.0 token endpoint URL
#   SAP_OAUTH_AUTHORIZE_URL- SAP OAuth authorization endpoint URL
#
# Optional:
#   SAP_PORT               - Server port (default: 44300)
#   SAP_CLIENT             - SAP client number (default: 100)
#   SAP_OAUTH_REDIRECT_URI - OAuth redirect URI for callback
#   SAP_OAUTH_SCOPE        - OAuth scope
# =============================================================================
```

**Step 3: Commit**

```bash
git add sap_agent/.env
git commit -m "refactor: simplify .env to only document sap_oauth authentication"
```

---

### Task 6: Delete Firebase web server files

**Files:**
- Delete: `sap_agent/web/auth_server.py`
- Delete: `sap_agent/web/templates/firebase_login.html`
- Delete: `sap_agent/web/__init__.py`

**Step 1: Delete the files**

```bash
rm sap_agent/web/auth_server.py
rm sap_agent/web/templates/firebase_login.html
rm -r sap_agent/web/templates
rm sap_agent/web/__init__.py
rmdir sap_agent/web
```

**Step 2: Commit**

```bash
git add -A sap_agent/web/
git commit -m "refactor: remove Firebase web auth server"
```

---

### Task 7: Delete obsolete documentation

**Files:**
- Delete: `docs/AUTH_BASIC.md`
- Delete: `docs/AUTH_OAUTH2.md`
- Delete: `docs/AUTH_FIREBASE.md`
- Delete: `docs/FIREBASE_OAUTH2_INTEGRATION_PLAN.md`
- Delete: `docs/SAP_PRINCIPAL_PROPAGATION_ANALYSIS.md`
- Delete: `docs/KR/AUTH_BASIC.md`
- Delete: `docs/KR/AUTH_OAUTH2.md`
- Delete: `docs/KR/AUTH_FIREBASE.md`
- Delete: `docs/KR/FIREBASE_OAUTH2_INTEGRATION_PLAN.md`

**Step 1: Delete the docs**

```bash
rm docs/AUTH_BASIC.md docs/AUTH_OAUTH2.md docs/AUTH_FIREBASE.md
rm docs/FIREBASE_OAUTH2_INTEGRATION_PLAN.md docs/SAP_PRINCIPAL_PROPAGATION_ANALYSIS.md
rm docs/KR/AUTH_BASIC.md docs/KR/AUTH_OAUTH2.md docs/KR/AUTH_FIREBASE.md
rm docs/KR/FIREBASE_OAUTH2_INTEGRATION_PLAN.md
```

**Step 2: Commit**

```bash
git add -A docs/
git commit -m "docs: remove Basic Auth, OAuth Client Credentials, and Firebase auth documentation"
```

---

### Task 8: Update `auth_tool.py` description

**Files:**
- Modify: `sap_agent/sap_gw_connector/tools/auth_tool.py`

**Step 1: Update description**

```python
@property
def description(self) -> str:
    return "Authenticate with SAP Gateway using SAP OAuth 2.0 Authorization Code flow"
```

**Step 2: Commit**

```bash
git add sap_agent/sap_gw_connector/tools/auth_tool.py
git commit -m "refactor: update auth tool description for sap_oauth only"
```

---

### Task 9: Delete obsolete test files

**Files:**
- Delete: `tests/test_firebase_auth.py`
- Delete: `tests/test_auth_server.py`

**Step 1: Delete the files**

```bash
rm tests/test_firebase_auth.py tests/test_auth_server.py
```

**Step 2: Commit**

```bash
git add -A tests/
git commit -m "test: remove Firebase auth and auth server tests"
```

---

### Task 10: Update remaining test files

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_integration.py`

**Step 1: Update `tests/conftest.py`**

Remove `basic_auth_env`, `oauth_env`, `basic_config`, `oauth_config` fixtures. Keep `sap_oauth_env` and `sap_oauth_config`:

```python
"""Shared test fixtures for SAP OAuth tests."""

import os

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure clean environment for each test."""
    sap_vars = [k for k in os.environ if k.startswith("SAP_")]
    for var in sap_vars:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sap_oauth_env(monkeypatch):
    """Set up environment for SAP OAuth Authorization Code flow."""
    monkeypatch.setenv("SAP_HOST", "sap.example.com")
    monkeypatch.setenv("SAP_AUTH_TYPE", "sap_oauth")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("SAP_OAUTH_TOKEN_URL", "https://sap.example.com:44300/sap/bc/sec/oauth2/token")
    monkeypatch.setenv("SAP_OAUTH_AUTHORIZE_URL", "https://sap.example.com:44300/sap/bc/sec/oauth2/authorize")
    monkeypatch.setenv("SAP_OAUTH_REDIRECT_URI", "http://localhost:8080/callback")


@pytest.fixture
def sap_oauth_config():
    """Create a SAP OAuth Authorization Code SAPConnectionConfig."""
    from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig

    return SAPConnectionConfig(
        host="sap.example.com",
        auth_type="sap_oauth",
        oauth_client_id="test-client-id",
        oauth_client_secret="test-client-secret",
        oauth_token_url="https://sap.example.com:44300/sap/bc/sec/oauth2/token",
        oauth_authorize_url="https://sap.example.com:44300/sap/bc/sec/oauth2/authorize",
        oauth_redirect_uri="http://localhost:8080/callback",
    )
```

**Step 2: Rewrite `tests/test_auth.py`**

Remove all `TestBasicAuthToken`, `TestOAuthToken`, `TestBaseAuthToken`, `TestSAPAuthenticator` (basic/oauth strategy tests), `TestOAuthStrategy`, `TestAuthHeaders` classes.

Replace with tests for `SAPAuthorizationCodeStrategy` and the simplified `SAPAuthenticator`:

```python
"""Tests for SAPAuthorizationCodeStrategy and SAPAuthenticator."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sap_agent.sap_gw_connector.core.auth import (
    AuthToken,
    SAPAuthenticator,
    SAPAuthorizationCodeStrategy,
    SAPUserToken,
)


class TestSAPUserToken:
    def test_valid_token(self):
        token = SAPUserToken(
            access_token="test-token",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        assert token.is_valid
        assert not token.is_expired

    def test_expired_token(self):
        token = SAPUserToken(
            access_token="test-token",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        assert token.is_expired
        assert not token.is_valid

    def test_empty_access_token_invalid(self):
        token = SAPUserToken(
            access_token="",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        assert not token.is_valid


class TestSAPAuthenticator:
    def test_creates_authorization_code_strategy(self, sap_oauth_config):
        authenticator = SAPAuthenticator(sap_oauth_config)
        assert isinstance(authenticator._strategy, SAPAuthorizationCodeStrategy)

    def test_rejects_non_sap_oauth(self):
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        # This should fail at config validation now, but test the authenticator too
        with pytest.raises((ValueError, SAPAuthenticationError)):
            from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
            config = SAPConnectionConfig(
                host="test",
                auth_type="basic",
                username="u",
                password="p",
            )

    def test_uses_authorization_code(self, sap_oauth_config):
        authenticator = SAPAuthenticator(sap_oauth_config)
        assert authenticator.uses_authorization_code is True

    def test_generate_auth_url(self, sap_oauth_config):
        authenticator = SAPAuthenticator(sap_oauth_config)
        result = authenticator.generate_sap_auth_url("user-123")
        assert "auth_url" in result
        assert "state" in result
        assert "authorize" in result["auth_url"]
```

**Step 3: Rewrite `tests/test_config.py`**

Remove `TestBasicAuthConfig`, `TestOAuthConfig`, basic/oauth env tests. Replace with sap_oauth-only tests:

```python
"""Tests for SAPConnectionConfig validation (sap_oauth only)."""

import pytest
from pydantic import ValidationError

from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig


class TestSAPOAuthConfig:
    def test_valid_config(self):
        config = SAPConnectionConfig(
            host="sap.example.com",
            oauth_client_id="client-id",
            oauth_client_secret="secret",
            oauth_token_url="https://sap/token",
            oauth_authorize_url="https://sap/authorize",
        )
        assert config.auth_type == "sap_oauth"

    def test_default_auth_type_is_sap_oauth(self):
        config = SAPConnectionConfig(
            host="sap.example.com",
            oauth_client_id="client-id",
            oauth_client_secret="secret",
            oauth_token_url="https://sap/token",
            oauth_authorize_url="https://sap/authorize",
        )
        assert config.auth_type == "sap_oauth"

    def test_missing_authorize_url_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            SAPConnectionConfig(
                host="sap.example.com",
                oauth_client_id="id",
                oauth_client_secret="sec",
                oauth_token_url="https://sap/token",
            )

    def test_missing_client_id_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            SAPConnectionConfig(
                host="sap.example.com",
                oauth_client_secret="sec",
                oauth_token_url="https://sap/token",
                oauth_authorize_url="https://sap/authorize",
            )

    def test_invalid_auth_type_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            SAPConnectionConfig(
                host="sap.example.com",
                auth_type="basic",
                oauth_client_id="id",
                oauth_client_secret="sec",
                oauth_token_url="https://sap/token",
                oauth_authorize_url="https://sap/authorize",
            )


class TestEnvVarIntegration:
    def test_env_vars_sap_oauth(self, sap_oauth_env):
        config = SAPConnectionConfig()
        assert config.host == "sap.example.com"
        assert config.auth_type == "sap_oauth"
        assert config.oauth_authorize_url is not None
```

**Step 4: Rewrite `tests/test_integration.py`**

Remove `TestBackwardCompatibility`, `TestOAuthIntegration` (client credentials), basic/oauth related tests. Keep only sap_oauth integration tests:

```python
"""Integration tests for SAP OAuth Authorization Code flow."""

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, patch


class TestSAPOAuthIntegration:
    def test_sap_oauth_full_flow(self):
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthenticator,
            SAPAuthorizationCodeStrategy,
        )

        config = SAPConnectionConfig(
            host="sap.example.com",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            oauth_token_url="https://sap/token",
            oauth_authorize_url="https://sap/authorize",
        )
        authenticator = SAPAuthenticator(config)
        assert isinstance(authenticator._strategy, SAPAuthorizationCodeStrategy)

    def test_generate_and_exchange(self):
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
        from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

        config = SAPConnectionConfig(
            host="sap.example.com",
            oauth_client_id="client-id",
            oauth_client_secret="secret",
            oauth_token_url="https://sap/token",
            oauth_authorize_url="https://sap/authorize",
            oauth_redirect_uri="http://localhost/callback",
        )
        auth = SAPAuthenticator(config)
        result = auth.generate_sap_auth_url("user-1")
        assert "auth_url" in result
        assert "state" in result
        assert "code_challenge" in result["auth_url"]


class TestTokenPolymorphism:
    def test_sap_user_token(self):
        from datetime import datetime, timedelta
        from sap_agent.sap_gw_connector.core.auth import SAPUserToken

        token = SAPUserToken(
            access_token="access-123",
            token_type="Bearer",
            sap_user="SAP_USER",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        assert token.is_valid
        assert token.cookies == {}


class TestErrorHandling:
    def test_oauth_error_is_authentication_error(self):
        from sap_agent.sap_gw_connector.core.exceptions import (
            SAPAuthenticationError,
            SAPOAuthError,
        )
        assert issubclass(SAPOAuthError, SAPAuthenticationError)

    def test_only_sap_oauth_accepted(self):
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
        with pytest.raises((ValueError, ValidationError)):
            SAPConnectionConfig(
                host="sap.example.com",
                auth_type="basic",
                username="u",
                password="p",
            )


class TestAuthToolIntegration:
    def test_auth_tool_description(self):
        from sap_agent.sap_gw_connector.tools.auth_tool import SAPAuthenticateTool
        tool = SAPAuthenticateTool()
        assert "SAP OAuth" in tool.description or "Authorization Code" in tool.description
```

**Step 5: Run tests**

```bash
cd /Users/sanggyulee/my-project/python-project/sap-adk-agent
python -m pytest tests/ -v --tb=short
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add tests/
git commit -m "test: rewrite tests for sap_oauth-only authentication"
```

---

### Task 11: Update README.md

**Files:**
- Modify: `README.md`

**Step 1: Update authentication references**

- Remove "Basic Auth Setup Guide", "OAuth 2.0 Setup Guide", "Firebase Auth Setup Guide" from Table of Contents
- Update "Key Features" table: change `sap_authenticate` description to only mention SAP OAuth Authorization Code
- Update "Technology Stack" Authentication row to: "SAP OAuth 2.0 Authorization Code with PKCE"
- Remove "Web Framework | FastAPI (for Firebase login page)" row
- Update architecture diagram: remove "(4 auth types)" references, show only 1 strategy
- Remove Basic Auth, OAuth, Firebase sections from "Getting Started"
- Remove Firebase/Basic/OAuth usage descriptions

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README for sap_oauth-only authentication"
```

---

### Task 12: Update remaining docs (QUICK_REFERENCE, DEPLOYMENT_GUIDE, etc.)

**Files:**
- Modify: `docs/QUICK_REFERENCE.md`
- Modify: `docs/DEPLOYMENT_GUIDE.md`
- Modify: `docs/KR/QUICK_REFERENCE.md`
- Modify: `docs/KR/DEPLOYMENT_GUIDE.md`
- Modify: `docs/KR/README.md`

**Step 1: Remove Basic Auth/OAuth/Firebase references from each doc**

In each file, remove sections about Basic Auth, OAuth Client Credentials, and Firebase authentication. Update to only reference SAP OAuth Authorization Code.

**Step 2: Commit**

```bash
git add docs/
git commit -m "docs: update all remaining docs for sap_oauth-only authentication"
```

---

### Task 13: Final verification

**Step 1: Run full test suite**

```bash
cd /Users/sanggyulee/my-project/python-project/sap-adk-agent
python -m pytest tests/ -v --tb=short
```

Expected: All tests pass.

**Step 2: Verify imports work**

```bash
python -c "
from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator, SAPAuthorizationCodeStrategy, SAPUserToken
from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError, SAPOAuthError
print('All imports OK')
"
```

**Step 3: Verify no stale references**

```bash
grep -r "BasicAuth\|FirebaseOAuth\|PrincipalPropagation\|firebase_admin\|_firebase_initialized\|auth_type.*basic\|auth_type.*firebase\|auth_type.*oauth[^_]" sap_agent/ tests/ --include="*.py" -l
```

Expected: No matches (empty output).

**Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "refactor: final cleanup for auth method consolidation"
```
