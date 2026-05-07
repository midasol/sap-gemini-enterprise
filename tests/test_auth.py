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

    def test_cookies_empty(self):
        token = SAPUserToken(
            access_token="test",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        assert token.cookies == {}


class TestSAPAuthenticator:
    def test_creates_authorization_code_strategy(self, sap_oauth_config):
        authenticator = SAPAuthenticator(sap_oauth_config)
        assert isinstance(authenticator._strategy, SAPAuthorizationCodeStrategy)

    def test_rejects_non_sap_oauth(self):
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        # Config validation now rejects non-sap_oauth auth types
        with pytest.raises(ValueError):
            from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
            SAPConnectionConfig(
                host="test",
                auth_type="basic",
                oauth_client_id="id",
                oauth_client_secret="sec",
                oauth_token_url="https://t/token",
                oauth_authorize_url="https://t/auth",
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

    def test_requires_csrf(self, sap_oauth_config):
        authenticator = SAPAuthenticator(sap_oauth_config)
        assert authenticator.requires_csrf is False


class TestSAPAuthorizationCodeStrategy:
    def test_generate_auth_url_with_pkce(self, sap_oauth_config):
        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        result = strategy.generate_auth_url("user-1")
        assert "auth_url" in result
        assert "state" in result
        assert "code_challenge" in result["auth_url"]
        assert "S256" in result["auth_url"]

    def test_set_current_user(self, sap_oauth_config):
        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        strategy.set_current_user("user-1")
        assert strategy._current_user_id == "user-1"

    def test_has_valid_token_false_initially(self, sap_oauth_config):
        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        assert not strategy.has_valid_token("user-1")

    @pytest.mark.asyncio
    async def test_get_valid_token_raises_without_user(self, sap_oauth_config):
        from sap_agent.sap_gw_connector.core.exceptions import SAPAuthenticationError

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        with pytest.raises(SAPAuthenticationError, match="No user has authenticated"):
            await strategy.get_valid_token()

    def test_get_auth_headers(self, sap_oauth_config):
        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        token = SAPUserToken(
            access_token="my-token",
            token_type="Bearer",
            sap_user="SAP_USER",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        headers = strategy.get_auth_headers(token)
        assert headers["Authorization"] == "Bearer my-token"
        assert headers["Accept"] == "application/json"

    def test_get_auth_headers_wrong_token_type(self, sap_oauth_config):
        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        token = AuthToken(expires_at=datetime.utcnow() + timedelta(hours=1))
        with pytest.raises(TypeError):
            strategy.get_auth_headers(token)
