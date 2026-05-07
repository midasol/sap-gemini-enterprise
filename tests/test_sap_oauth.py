"""Tests for SAP OAuth Authorization Code flow (Option 1).

Three test levels:
1. Unit tests: Code logic without any network calls
2. Integration tests: Full flow with mock SAP OAuth server
3. E2E tests: Real SAP connection (skipped unless SAP_E2E_TEST=1)
"""

import asyncio
import json
import os
import urllib.parse
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# Level 1: Unit Tests (no network, no SAP)
# ===========================================================================


class TestSAPOAuthConfig:
    """Settings validation for auth_type='sap_oauth'."""

    def test_valid_sap_oauth_config(self, sap_oauth_config):
        """All required fields present → config created successfully."""
        assert sap_oauth_config.auth_type == "sap_oauth"
        assert sap_oauth_config.oauth_authorize_url is not None
        assert sap_oauth_config.oauth_redirect_uri is not None

    def test_missing_authorize_url_raises(self):
        """Missing oauth_authorize_url → ValidationError."""
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig

        with pytest.raises(ValueError, match="oauth_authorize_url is required"):
            SAPConnectionConfig(
                host="sap.example.com",
                auth_type="sap_oauth",
                oauth_client_id="cid",
                oauth_client_secret="csec",
                oauth_token_url="https://sap.example.com/token",
                # oauth_authorize_url missing
            )

    def test_missing_token_url_raises(self):
        """Missing oauth_token_url → ValidationError."""
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig

        with pytest.raises(ValueError, match="oauth_client_id"):
            SAPConnectionConfig(
                host="sap.example.com",
                auth_type="sap_oauth",
                # missing oauth credentials
            )

    def test_sap_oauth_auth_type_validation(self):
        """auth_type='sap_oauth' is accepted by validator."""
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig

        config = SAPConnectionConfig(
            host="sap.example.com",
            auth_type="sap_oauth",
            oauth_client_id="cid",
            oauth_client_secret="csec",
            oauth_token_url="https://sap.example.com/token",
            oauth_authorize_url="https://sap.example.com/authorize",
        )
        assert config.auth_type == "sap_oauth"


class TestSAPAuthorizationCodeStrategy:
    """Unit tests for SAPAuthorizationCodeStrategy."""

    def test_strategy_created_for_sap_oauth(self, sap_oauth_config):
        """SAPAuthenticator creates SAPAuthorizationCodeStrategy."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthenticator,
            SAPAuthorizationCodeStrategy,
        )

        auth = SAPAuthenticator(sap_oauth_config)
        assert isinstance(auth._strategy, SAPAuthorizationCodeStrategy)
        assert auth.uses_authorization_code is True

    def test_generate_auth_url_structure(self, sap_oauth_config):
        """generate_auth_url returns dict with auth_url and state."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        result = strategy.generate_auth_url("user-123")

        assert "auth_url" in result
        assert "state" in result
        assert len(result["state"]) > 20  # cryptographically random

    def test_generate_auth_url_contains_pkce(self, sap_oauth_config):
        """Auth URL contains PKCE code_challenge and S256 method."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        result = strategy.generate_auth_url("user-123")

        parsed = urllib.parse.urlparse(result["auth_url"])
        params = urllib.parse.parse_qs(parsed.query)

        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["test-client-id"]
        assert params["code_challenge_method"] == ["S256"]
        assert "code_challenge" in params
        assert len(params["code_challenge"][0]) > 20

    def test_generate_auth_url_contains_redirect_uri(self, sap_oauth_config):
        """Auth URL contains the configured redirect_uri."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        result = strategy.generate_auth_url("user-123")

        parsed = urllib.parse.urlparse(result["auth_url"])
        params = urllib.parse.parse_qs(parsed.query)

        assert params["redirect_uri"] == ["http://localhost:8080/callback"]

    def test_generate_auth_url_stores_pending_state(self, sap_oauth_config):
        """State is stored in _pending_auth for later verification."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        result = strategy.generate_auth_url("user-123")

        state = result["state"]
        assert state in strategy._pending_auth
        code_verifier, user_id = strategy._pending_auth[state]
        assert user_id == "user-123"
        assert len(code_verifier) > 20

    def test_generate_auth_url_unique_per_call(self, sap_oauth_config):
        """Each call generates unique state and code_challenge."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        r1 = strategy.generate_auth_url("user-1")
        r2 = strategy.generate_auth_url("user-2")

        assert r1["state"] != r2["state"]
        assert r1["auth_url"] != r2["auth_url"]

    def test_facade_generate_sap_auth_url(self, sap_oauth_config):
        """SAPAuthenticator.generate_sap_auth_url delegates to strategy."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

        auth = SAPAuthenticator(sap_oauth_config)
        result = auth.generate_sap_auth_url("user-abc")

        assert "auth_url" in result
        assert "state" in result

    def test_generate_auth_url_caches_last_info(self, sap_oauth_config):
        """generate_auth_url stores result in _last_auth_info."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        assert strategy._last_auth_info is None

        result = strategy.generate_auth_url("user-123")

        assert strategy._last_auth_info is not None
        assert strategy._last_auth_info["auth_url"] == result["auth_url"]
        assert strategy._last_auth_info["state"] == result["state"]



class TestSAPUserTokenForAuthCode:
    """SAPUserToken behavior for authorization code flow."""

    def test_sap_user_token_valid(self):
        """SAPUserToken with access_token is valid when not expired."""
        from sap_agent.sap_gw_connector.core.auth import SAPUserToken

        token = SAPUserToken(
            access_token="test-token",
            refresh_token="test-refresh",
            sap_user="SAP_USER_001",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        assert token.is_valid
        assert token.sap_user == "SAP_USER_001"

    def test_sap_user_token_expired(self):
        """SAPUserToken past expires_at is expired."""
        from sap_agent.sap_gw_connector.core.auth import SAPUserToken

        token = SAPUserToken(
            access_token="test-token",
            refresh_token="test-refresh",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        assert token.is_expired
        assert not token.is_valid


class TestExchangeCodeValidation:
    """exchange_code state validation (no HTTP)."""

    @pytest.mark.asyncio
    async def test_unknown_state_derives_verifier(self, sap_oauth_config):
        """exchange_code with unknown state derives code_verifier deterministically."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        # With deterministic PKCE, unknown state no longer raises at
        # state validation; it derives code_verifier and attempts HTTP.
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value='{"error":"invalid_grant"}')
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(SAPAuthenticationError):
                await strategy.exchange_code("some-code", "bad-state")

    @pytest.mark.asyncio
    async def test_state_consumed_after_exchange(self, sap_oauth_config):
        """State is removed from _pending_auth after exchange attempt."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        result = strategy.generate_auth_url("user-1")
        state = result["state"]

        # Mock a failed HTTP response so exchange_code proceeds past state check
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value='{"error":"invalid_grant"}')
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(SAPAuthenticationError):
                await strategy.exchange_code("some-code", state)

        # State should be consumed even on failure
        assert state not in strategy._pending_auth


class TestGetValidTokenAuthCode:
    """get_valid_token behavior for authorization code strategy."""

    @pytest.mark.asyncio
    async def test_no_user_raises(self, sap_oauth_config):
        """get_valid_token without prior auth raises error."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        with pytest.raises(SAPAuthenticationError, match="No user has authenticated"):
            await strategy.get_valid_token()

    @pytest.mark.asyncio
    async def test_returns_cached_valid_token(self, sap_oauth_config):
        """get_valid_token returns cached token if still valid."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        valid_token = SAPUserToken(
            access_token="cached-access",
            refresh_token="cached-refresh",
            sap_user="SAP_USER",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        strategy._user_tokens["user-1"] = valid_token
        strategy._current_user_id = "user-1"
        strategy._current_token = valid_token

        token = await strategy.get_valid_token()
        assert token.access_token == "cached-access"

    @pytest.mark.asyncio
    async def test_expired_token_triggers_refresh(self, sap_oauth_config):
        """Expired token with refresh_token triggers automatic refresh."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        expired_token = SAPUserToken(
            access_token="expired-access",
            refresh_token="valid-refresh",
            sap_user="SAP_USER",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        strategy._user_tokens["user-1"] = expired_token
        strategy._current_user_id = "user-1"
        strategy._current_token = None

        # Mock successful refresh
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "access_token": "refreshed-access",
            "token_type": "Bearer",
            "expires_in": 3600,
            "user_name": "SAP_USER_REFRESHED",
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            token = await strategy.get_valid_token()

        assert token.access_token == "refreshed-access"
        assert token.is_valid

    @pytest.mark.asyncio
    async def test_expired_no_refresh_raises(self, sap_oauth_config):
        """Expired token without refresh_token raises re-login error."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        expired_token = SAPUserToken(
            access_token="expired-access",
            refresh_token=None,  # no refresh token
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        strategy._user_tokens["user-1"] = expired_token
        strategy._current_user_id = "user-1"
        strategy._current_token = None

        with pytest.raises(SAPAuthenticationError, match="No refresh token"):
            await strategy.get_valid_token()


class TestAuthHeaders:
    """get_auth_headers for authorization code strategy."""

    def test_auth_headers_bearer(self, sap_oauth_config):
        """Returns Authorization: Bearer header."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        token = SAPUserToken(
            access_token="my-sap-token",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        headers = strategy.get_auth_headers(token)
        assert headers["Authorization"] == "Bearer my-sap-token"

    def test_wrong_token_type_raises(self, sap_oauth_config):
        """Non-SAPUserToken raises TypeError."""
        from sap_agent.sap_gw_connector.core.auth import (
            AuthToken,
            SAPAuthorizationCodeStrategy,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        wrong_token = AuthToken(
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        with pytest.raises(TypeError):
            strategy.get_auth_headers(wrong_token)


class TestTokenCacheManagement:
    """Token cache eviction and cleanup."""

    def test_cleanup_expired_tokens(self, sap_oauth_config):
        """cleanup_expired_tokens removes only expired entries."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        strategy._user_tokens["expired-user"] = SAPUserToken(
            access_token="x",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        strategy._user_tokens["valid-user"] = SAPUserToken(
            access_token="y",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )

        removed = strategy.cleanup_expired_tokens()
        assert removed == 1
        assert "expired-user" not in strategy._user_tokens
        assert "valid-user" in strategy._user_tokens

    def test_cache_eviction_on_max(self, sap_oauth_config):
        """Oldest token evicted when cache reaches max size."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        strategy._MAX_CACHED_USER_TOKENS = 3

        for i in range(3):
            strategy._user_tokens[f"user-{i}"] = SAPUserToken(
                access_token=f"token-{i}",
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )

        # Adding 4th should evict user-0
        new_token = SAPUserToken(
            access_token="token-new",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        strategy._cache_token("user-new", new_token)

        assert "user-0" not in strategy._user_tokens
        assert "user-new" in strategy._user_tokens
        assert len(strategy._user_tokens) == 3

    def test_set_current_user(self, sap_oauth_config):
        """set_current_user switches active user context."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        token_a = SAPUserToken(
            access_token="token-a",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_b = SAPUserToken(
            access_token="token-b",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        strategy._user_tokens["user-a"] = token_a
        strategy._user_tokens["user-b"] = token_b

        strategy.set_current_user("user-a")
        assert strategy._current_user_id == "user-a"
        assert strategy._current_token.access_token == "token-a"

        strategy.set_current_user("user-b")
        assert strategy._current_user_id == "user-b"
        assert strategy._current_token.access_token == "token-b"


# ===========================================================================
# Level 2: Integration Tests (mock SAP OAuth server)
# ===========================================================================


def _mock_token_response(
    access_token="sap-access-token-123",
    refresh_token="sap-refresh-token-456",
    sap_user="SAP_USER_001",
    expires_in=3600,
    status=200,
):
    """Helper: create a mock aiohttp response for token endpoint."""
    mock_response = AsyncMock()
    mock_response.status = status
    if status == 200:
        mock_response.json = AsyncMock(return_value={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": "ZODATA_SRV",
            "user_name": sap_user,
        })
    else:
        mock_response.text = AsyncMock(return_value=json.dumps({
            "error": "invalid_grant",
            "error_description": "Authorization code expired",
        }))
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    return mock_response


def _mock_session(mock_response):
    """Helper: create a mock aiohttp session."""
    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


class TestFullAuthCodeFlow:
    """Integration: complete authorization code flow with mock server."""

    @pytest.mark.asyncio
    async def test_complete_flow(self, sap_oauth_config):
        """Step 1: auth URL → Step 2: code exchange → token obtained."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        # Step 1: Generate auth URL
        auth_info = strategy.generate_auth_url("user-123")
        state = auth_info["state"]

        # Verify PKCE is stored
        assert state in strategy._pending_auth

        # Step 2: Exchange code (mock SAP responding with token)
        mock_resp = _mock_token_response(sap_user="JSMITH")
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            token = await strategy.exchange_code("auth-code-xyz", state)

        # Verify token
        assert token.access_token == "sap-access-token-123"
        assert token.refresh_token == "sap-refresh-token-456"
        assert token.sap_user == "JSMITH"
        assert token.is_valid

        # Verify state is consumed
        assert state not in strategy._pending_auth

        # Verify token is cached
        assert strategy.has_valid_token("user-123")

    @pytest.mark.asyncio
    async def test_code_exchange_sends_pkce_verifier(self, sap_oauth_config):
        """Token request includes code_verifier for PKCE verification."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        auth_info = strategy.generate_auth_url("user-1")
        state = auth_info["state"]

        # Capture the code_verifier before exchange consumes the state
        code_verifier = strategy._pending_auth[state][0]

        mock_resp = _mock_token_response()
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            await strategy.exchange_code("code-123", state)

        # Verify the POST data included code_verifier
        call_kwargs = mock_sess.post.call_args
        post_data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
        assert post_data["code_verifier"] == code_verifier
        assert post_data["grant_type"] == "authorization_code"

    @pytest.mark.asyncio
    async def test_expired_code_returns_error(self, sap_oauth_config):
        """Expired/invalid authorization code → clear error message."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        auth_info = strategy.generate_auth_url("user-1")

        mock_resp = _mock_token_response(status=400)
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            with pytest.raises(SAPAuthenticationError, match="invalid|expired"):
                await strategy.exchange_code("expired-code", auth_info["state"])

    @pytest.mark.asyncio
    async def test_refresh_token_flow(self, sap_oauth_config):
        """After initial auth, expired token auto-refreshes."""
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthorizationCodeStrategy,
            SAPUserToken,
        )

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        # Simulate: user already authenticated but token expired
        expired_token = SAPUserToken(
            access_token="expired",
            refresh_token="valid-refresh",
            sap_user="JSMITH",
            expires_at=datetime.utcnow() - timedelta(minutes=5),
        )
        strategy._user_tokens["user-1"] = expired_token
        strategy._current_user_id = "user-1"

        # Mock refresh response
        mock_resp = _mock_token_response(
            access_token="new-access",
            refresh_token="new-refresh",
            sap_user="JSMITH",
        )
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            token = await strategy.get_valid_token()

        assert token.access_token == "new-access"
        assert token.is_valid

        # Verify refresh_token was sent
        call_kwargs = mock_sess.post.call_args
        post_data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
        assert post_data["grant_type"] == "refresh_token"
        assert post_data["refresh_token"] == "valid-refresh"

    @pytest.mark.asyncio
    async def test_multi_user_isolation(self, sap_oauth_config):
        """Multiple users have separate tokens and sessions."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)

        # User A authenticates
        auth_a = strategy.generate_auth_url("user-A")
        mock_resp_a = _mock_token_response(
            access_token="token-A", sap_user="SAP_A"
        )
        mock_sess_a = _mock_session(mock_resp_a)

        with patch("aiohttp.ClientSession", return_value=mock_sess_a):
            token_a = await strategy.exchange_code("code-A", auth_a["state"])

        # User B authenticates
        auth_b = strategy.generate_auth_url("user-B")
        mock_resp_b = _mock_token_response(
            access_token="token-B", sap_user="SAP_B"
        )
        mock_sess_b = _mock_session(mock_resp_b)

        with patch("aiohttp.ClientSession", return_value=mock_sess_b):
            token_b = await strategy.exchange_code("code-B", auth_b["state"])

        # Verify isolation
        assert token_a.access_token == "token-A"
        assert token_a.sap_user == "SAP_A"
        assert token_b.access_token == "token-B"
        assert token_b.sap_user == "SAP_B"

        # Switch user context
        strategy.set_current_user("user-A")
        t = await strategy.get_valid_token()
        assert t.access_token == "token-A"

        strategy.set_current_user("user-B")
        t = await strategy.get_valid_token()
        assert t.access_token == "token-B"


class TestFacadeIntegration:
    """SAPAuthenticator facade with SAPAuthorizationCodeStrategy."""

    @pytest.mark.asyncio
    async def test_facade_full_flow(self, sap_oauth_config):
        """Facade: generate URL → exchange code → get token."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

        auth = SAPAuthenticator(sap_oauth_config)

        # Step 1
        url_info = auth.generate_sap_auth_url("user-1")
        assert "auth_url" in url_info

        # Step 2
        mock_resp = _mock_token_response(sap_user="SAP_FACADE_USER")
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            token = await auth.exchange_authorization_code(
                "code-abc", url_info["state"]
            )

        assert token.sap_user == "SAP_FACADE_USER"
        assert auth.has_valid_token_for_user("user-1")

        # Step 3: get_valid_token works
        auth.set_current_user("user-1")
        t = await auth.get_valid_token()
        assert t.access_token == token.access_token


class TestAgentSapAuthenticate:
    """sap_authenticate() tool with auth_type=sap_oauth."""

    def test_step1_returns_auth_url(self, sap_oauth_env):
        """sap_authenticate without code returns auth_url."""
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import sap_authenticate

        with patch("sap_agent.agent._find_any_pending_oauth_code", return_value=None), \
             patch("sap_agent.agent._check_pending_oauth_code", return_value=None):
            result = sap_authenticate()

        assert result["success"] is False
        assert result["action_required"] == "sap_login"
        assert "auth_url" in result
        assert "oauth_state" in result
        assert "sap/bc/sec/oauth2/authorize" in result["auth_url"]

    def test_step2_exchanges_code(self, sap_oauth_env):
        """sap_authenticate with code + state exchanges for token."""
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import sap_authenticate

        # Step 1: get state
        with patch("sap_agent.agent._find_any_pending_oauth_code", return_value=None), \
             patch("sap_agent.agent._check_pending_oauth_code", return_value=None):
            step1 = sap_authenticate()
        state = step1["oauth_state"]

        # Step 2: exchange code (mock HTTP)
        mock_resp = _mock_token_response(sap_user="TEST_SAP_USER")
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess), \
             patch("sap_agent.agent._cleanup_pending_oauth_secret"):
            result = sap_authenticate(
                authorization_code="test-auth-code",
                oauth_state=state,
            )

        assert result["success"] is True
        assert result["auth_type"] == "sap_oauth"
        assert result["sap_user"] == "TEST_SAP_USER"


# ===========================================================================
# _parse_oauth_callback helper tests
# ===========================================================================


class TestParseOAuthCallback:
    """_parse_oauth_callback parses raw user input into (code, state)."""

    def test_query_string_format(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "code=abc123&state=xyz789"
        )
        assert code == "abc123"
        assert state == "xyz789"

    def test_full_url_format(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "https://example.com/callback?code=abc123&state=xyz789"
        )
        assert code == "abc123"
        assert state == "xyz789"

    def test_fragment_url_format(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "https://example.com/callback#code=abc123&state=xyz789"
        )
        assert code == "abc123"
        assert state == "xyz789"

    def test_plain_code_returns_as_is(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback("just-a-plain-code")
        assert code == "just-a-plain-code"
        assert state is None

    def test_url_encoded_values(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "code=t2lqsT9hH9GGiVjKIW58pWvI_Mrht6UMwqTwy_A4F5laNYHe"
            "&state=y1_z_OOWhZ9Zwhp2od_nPz0HIlpGhoam9oBRw5YfIQE"
        )
        assert code == "t2lqsT9hH9GGiVjKIW58pWvI_Mrht6UMwqTwy_A4F5laNYHe"
        assert state == "y1_z_OOWhZ9Zwhp2od_nPz0HIlpGhoam9oBRw5YfIQE"

    def test_empty_string(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback("")
        assert code == ""
        assert state is None


class TestStep1ReuseExistingAuth:
    """sap_authenticate reuses pending auth URL instead of generating new."""

    @staticmethod
    def _clear_agent_cache():
        """Clear module-level authenticator cache to avoid cross-test pollution."""
        import sap_agent.agent as agent_mod
        agent_mod._user_authenticators.clear()
        agent_mod._last_authenticated_uid = None

    def test_step1_reuse_returns_same_url(self, sap_oauth_env):
        """Calling sap_authenticate() twice without code returns same auth URL."""
        self._clear_agent_cache()
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import sap_authenticate

        with patch("sap_agent.agent._find_any_pending_oauth_code", return_value=None), \
             patch("sap_agent.agent._check_pending_oauth_code", return_value=None):
            # First call: generates auth URL
            result1 = sap_authenticate()
            assert result1["action_required"] == "sap_login"
            url1 = result1["auth_url"]
            state1 = result1["oauth_state"]

            # Second call: should return the SAME URL (not generate new)
            result2 = sap_authenticate()
            assert result2["action_required"] == "sap_login"
            assert result2["auth_url"] == url1
            assert result2["oauth_state"] == state1

    def test_step1_reuse_then_exchange_succeeds(self, sap_oauth_env):
        """After reuse, code exchange with original state still works."""
        self._clear_agent_cache()
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import sap_authenticate

        with patch("sap_agent.agent._find_any_pending_oauth_code", return_value=None), \
             patch("sap_agent.agent._check_pending_oauth_code", return_value=None):
            # Step 1: generate auth URL
            result1 = sap_authenticate()
            state = result1["oauth_state"]

            # Step 1 again (re-triggered by LLM): should reuse
            result2 = sap_authenticate()
            assert result2["oauth_state"] == state

        # Step 2: exchange code with original state
        mock_resp = _mock_token_response(sap_user="REUSE_USER")
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess), \
             patch("sap_agent.agent._cleanup_pending_oauth_secret"):
            result3 = sap_authenticate(
                authorization_code="test-code",
                oauth_state=state,
            )

        assert result3["success"] is True
        assert result3["sap_user"] == "REUSE_USER"


# ===========================================================================
# Cross-Worker Token Persistence
# ===========================================================================


class TestCrossWorkerTokenPersistence:
    """Token data persists in ADK session state for cross-worker recovery."""

    @staticmethod
    def _clear_agent_cache():
        import sap_agent.agent as agent_mod
        agent_mod._user_authenticators.clear()
        agent_mod._last_authenticated_uid = None

    @pytest.fixture(autouse=True)
    def _mock_secret_manager(self):
        """Prevent real Secret Manager calls during unit tests."""
        with patch("sap_agent.agent._save_token_to_secret", return_value=True), \
             patch("sap_agent.agent._load_token_from_secret", return_value=None):
            yield

    def test_store_persists_token_to_session_state(self, sap_oauth_env):
        """_store_authenticator writes sap_token_data to tool_context.state."""
        self._clear_agent_cache()
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import _store_authenticator
        from sap_agent.sap_gw_connector.config.settings import get_config
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthenticator,
            SAPUserToken,
        )

        config = get_config(require_sap=True)
        authenticator = SAPAuthenticator(config.sap)

        # Inject a token into the strategy
        token = SAPUserToken(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            token_type="Bearer",
            scope="ZODATA_SRV",
            sap_user="TEST_USER",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        authenticator._strategy._cache_token("user1", token)
        authenticator._strategy.set_current_user("user1")

        # Mock tool_context with state dict
        tool_context = MagicMock()
        tool_context.state = {}

        _store_authenticator("user1", authenticator, tool_context)

        assert "sap_token_data" in tool_context.state
        td = tool_context.state["sap_token_data"]
        assert td["access_token"] == "test-access-token"
        assert td["refresh_token"] == "test-refresh-token"
        assert td["sap_user"] == "TEST_USER"
        assert td["user_id"] == "user1"
        assert "expires_at" in td

    def test_get_reconstructs_from_session_state_on_cache_miss(
        self, sap_oauth_env,
    ):
        """_get_authenticator_for_session reconstructs from state on miss."""
        self._clear_agent_cache()

        from sap_agent.agent import _get_authenticator_for_session

        tool_context = MagicMock()
        tool_context.state = {
            "user_id": "user1",
            "sap_token_data": {
                "access_token": "restored-token",
                "refresh_token": "restored-refresh",
                "token_type": "Bearer",
                "scope": "ZODATA_SRV",
                "sap_user": "RESTORED_USER",
                "expires_at": (
                    datetime.utcnow() + timedelta(hours=1)
                ).isoformat(),
                "user_id": "user1",
            },
        }

        auth = _get_authenticator_for_session(tool_context)

        assert auth is not None
        assert auth.uses_authorization_code
        strategy = auth._strategy
        assert strategy._current_user_id == "user1"
        cached_token = strategy._user_tokens.get("user1")
        assert cached_token is not None
        assert cached_token.access_token == "restored-token"
        assert cached_token.sap_user == "RESTORED_USER"

    def test_get_returns_none_for_expired_no_refresh(self, sap_oauth_env):
        """Expired token without refresh_token returns None."""
        self._clear_agent_cache()

        from sap_agent.agent import _get_authenticator_for_session

        tool_context = MagicMock()
        tool_context.state = {
            "user_id": "user1",
            "sap_token_data": {
                "access_token": "expired-token",
                "refresh_token": None,
                "token_type": "Bearer",
                "sap_user": "EXPIRED_USER",
                "expires_at": (
                    datetime.utcnow() - timedelta(hours=1)
                ).isoformat(),
                "user_id": "user1",
            },
        }

        auth = _get_authenticator_for_session(tool_context)
        assert auth is None

    def test_get_reconstructs_expired_with_refresh_token(self, sap_oauth_env):
        """Expired token WITH refresh_token is still reconstructed."""
        self._clear_agent_cache()

        from sap_agent.agent import _get_authenticator_for_session

        tool_context = MagicMock()
        tool_context.state = {
            "user_id": "user1",
            "sap_token_data": {
                "access_token": "expired-token",
                "refresh_token": "valid-refresh-token",
                "token_type": "Bearer",
                "sap_user": "REFRESH_USER",
                "expires_at": (
                    datetime.utcnow() - timedelta(hours=1)
                ).isoformat(),
                "user_id": "user1",
            },
        }

        auth = _get_authenticator_for_session(tool_context)
        assert auth is not None
        assert auth.uses_authorization_code

    def test_full_flow_store_then_reconstruct(self, sap_oauth_env):
        """Full round-trip: store token, clear cache, reconstruct."""
        self._clear_agent_cache()
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import (
            _store_authenticator,
            _get_authenticator_for_session,
        )
        from sap_agent.sap_gw_connector.config.settings import get_config
        from sap_agent.sap_gw_connector.core.auth import (
            SAPAuthenticator,
            SAPUserToken,
        )

        config = get_config(require_sap=True)
        authenticator = SAPAuthenticator(config.sap)
        token = SAPUserToken(
            access_token="roundtrip-token",
            refresh_token="roundtrip-refresh",
            token_type="Bearer",
            scope="ZODATA_SRV",
            sap_user="ROUNDTRIP_USER",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        authenticator._strategy._cache_token("user1", token)
        authenticator._strategy.set_current_user("user1")

        # Store with session state
        tool_context = MagicMock()
        tool_context.state = {}
        _store_authenticator("user1", authenticator, tool_context)

        # Simulate different worker: clear in-memory cache
        self._clear_agent_cache()

        # Reconstruct from session state
        auth2 = _get_authenticator_for_session(tool_context)
        assert auth2 is not None
        t2 = auth2._strategy._user_tokens.get("user1")
        assert t2 is not None
        assert t2.access_token == "roundtrip-token"
        assert t2.sap_user == "ROUNDTRIP_USER"


# ===========================================================================
# Level 3: E2E Tests (real SAP server)
# ===========================================================================


@pytest.mark.skipif(
    os.getenv("SAP_E2E_TEST") != "1",
    reason="E2E tests require SAP_E2E_TEST=1 and real SAP credentials",
)
class TestE2ERealSAP:
    """End-to-end tests against a real SAP system.

    Requirements:
    - SAP_E2E_TEST=1
    - SAP_HOST, SAP_OAUTH_CLIENT_ID, SAP_OAUTH_CLIENT_SECRET
    - SAP_OAUTH_TOKEN_URL, SAP_OAUTH_AUTHORIZE_URL set to real values

    These tests generate an auth URL but cannot complete browser login
    automatically. They verify:
    1. Auth URL is generated correctly for the real SAP endpoint
    2. Token endpoint is reachable
    3. If SAP_E2E_AUTH_CODE is provided, the full exchange works
    """

    def test_auth_url_generation(self):
        """Generate auth URL pointing to real SAP server."""
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        config = SAPConnectionConfig(
            host=os.environ["SAP_HOST"],
            auth_type="sap_oauth",
            oauth_client_id=os.environ["SAP_OAUTH_CLIENT_ID"],
            oauth_client_secret=os.environ["SAP_OAUTH_CLIENT_SECRET"],
            oauth_token_url=os.environ["SAP_OAUTH_TOKEN_URL"],
            oauth_authorize_url=os.environ["SAP_OAUTH_AUTHORIZE_URL"],
            oauth_redirect_uri=os.getenv(
                "SAP_OAUTH_REDIRECT_URI", "http://localhost:8080/callback"
            ),
            verify_ssl=os.getenv("SAP_VERIFY_SSL", "false").lower() == "true",
        )

        strategy = SAPAuthorizationCodeStrategy(config)
        result = strategy.generate_auth_url("e2e-test-user")

        print(f"\n{'='*60}")
        print(f"SAP OAuth Login URL (open in browser):")
        print(f"{result['auth_url']}")
        print(f"State: {result['state']}")
        print(f"{'='*60}\n")

        assert os.environ["SAP_OAUTH_AUTHORIZE_URL"] in result["auth_url"]

    @pytest.mark.skipif(
        not os.getenv("SAP_E2E_AUTH_CODE"),
        reason="SAP_E2E_AUTH_CODE not set (requires manual browser login)",
    )
    @pytest.mark.asyncio
    async def test_code_exchange_real(self):
        """Exchange a real authorization code for SAP token.

        To run this test:
        1. Run test_auth_url_generation first, copy the URL
        2. Open URL in browser, log in with SAP credentials
        3. Copy the authorization code from the redirect URL
        4. Set SAP_E2E_AUTH_CODE=<code> SAP_E2E_STATE=<state>
        5. Run this test
        """
        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        config = SAPConnectionConfig(
            host=os.environ["SAP_HOST"],
            auth_type="sap_oauth",
            oauth_client_id=os.environ["SAP_OAUTH_CLIENT_ID"],
            oauth_client_secret=os.environ["SAP_OAUTH_CLIENT_SECRET"],
            oauth_token_url=os.environ["SAP_OAUTH_TOKEN_URL"],
            oauth_authorize_url=os.environ["SAP_OAUTH_AUTHORIZE_URL"],
            oauth_redirect_uri=os.getenv(
                "SAP_OAUTH_REDIRECT_URI", "http://localhost:8080/callback"
            ),
            verify_ssl=os.getenv("SAP_VERIFY_SSL", "false").lower() == "true",
        )

        strategy = SAPAuthorizationCodeStrategy(config)

        # We need to manually inject the PKCE state since we generated it
        # in a previous test run
        auth_code = os.environ["SAP_E2E_AUTH_CODE"]
        state = os.environ.get("SAP_E2E_STATE", "")

        if state:
            # Inject a dummy PKCE entry for the state
            # (In real usage, the same strategy instance holds it)
            code_verifier = os.environ.get("SAP_E2E_CODE_VERIFIER", "")
            if code_verifier:
                strategy._pending_auth[state] = (code_verifier, "e2e-user")

                token = await strategy.exchange_code(auth_code, state)

                print(f"\nSAP Token obtained:")
                print(f"  SAP User: {token.sap_user}")
                print(f"  Scope: {token.scope}")
                print(f"  Expires: {token.expires_at}")
                print(f"  Has refresh: {token.refresh_token is not None}")

                assert token.access_token
                assert token.is_valid


def _ensure_agent_module_importable():
    """Mock google.adk and related modules if not installed, so sap_agent.agent can be imported."""
    import sys
    modules_to_mock = [
        "google.adk", "google.adk.agents", "google.adk.agents.llm_agent",
        "google.adk.models", "google.adk.tools",
        "google.genai", "google.genai.types",
    ]
    for mod_name in modules_to_mock:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()


class TestPendingOAuthCodeDetection:
    """Cloud Run callback → Secret Manager → Agent auto-detect."""

    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_found(self):
        """Pending code in Secret Manager → returns code+state dict."""
        _ensure_agent_module_importable()
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client

        from datetime import datetime, timezone
        payload = json.dumps({
            "code": "test_auth_code_123",
            "state": "abc123def456ghi7_rest_of_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        mock_response = MagicMock()
        mock_response.payload.data = payload.encode("UTF-8")
        mock_client.access_secret_version.return_value = mock_response

        with patch("sap_agent.agent._get_secret_manager", return_value=mock_sm):
            result = _check_pending_oauth_code("abc123def456ghi7_rest_of_state")

        assert result is not None
        assert result["code"] == "test_auth_code_123"
        assert result["state"] == "abc123def456ghi7_rest_of_state"

    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_not_found(self):
        """No pending code → returns None."""
        _ensure_agent_module_importable()
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client
        mock_client.access_secret_version.side_effect = Exception("NOT_FOUND")

        with patch("sap_agent.agent._get_secret_manager", return_value=mock_sm):
            result = _check_pending_oauth_code("nonexistent_state")
        assert result is None

    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_expired(self):
        """Pending code older than 10 minutes → returns None."""
        _ensure_agent_module_importable()
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client

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

        with patch("sap_agent.agent._get_secret_manager", return_value=mock_sm):
            result = _check_pending_oauth_code("expired_state_1234")
        assert result is None

    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_check_pending_code_state_mismatch(self):
        """State in secret doesn't match requested state → returns None."""
        _ensure_agent_module_importable()
        from sap_agent.agent import _check_pending_oauth_code

        mock_client = MagicMock()
        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client

        from datetime import datetime, timezone
        payload = json.dumps({
            "code": "some_code",
            "state": "different_full_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        mock_response = MagicMock()
        mock_response.payload.data = payload.encode("UTF-8")
        mock_client.access_secret_version.return_value = mock_response

        with patch("sap_agent.agent._get_secret_manager", return_value=mock_sm):
            result = _check_pending_oauth_code("abc123def456ghi7_but_different")
        assert result is None
