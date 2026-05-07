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
                oauth_client_id="id",
                oauth_client_secret="sec",
                oauth_token_url="https://sap/token",
                oauth_authorize_url="https://sap/authorize",
            )


class TestEnvVarIntegration:
    def test_env_vars_sap_oauth(self, monkeypatch):
        monkeypatch.setenv("SAP_HOST", "sap.example.com")
        monkeypatch.setenv("SAP_AUTH_TYPE", "sap_oauth")
        monkeypatch.setenv("SAP_OAUTH_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("SAP_OAUTH_CLIENT_SECRET", "test-client-secret")
        monkeypatch.setenv("SAP_OAUTH_TOKEN_URL", "https://auth.example.com/oauth/token")
        monkeypatch.setenv("SAP_OAUTH_AUTHORIZE_URL", "https://auth.example.com/authorize")

        from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
        config = SAPConnectionConfig()
        assert config.host == "sap.example.com"
        assert config.auth_type == "sap_oauth"


class TestAuthToolIntegration:
    def test_auth_tool_description(self):
        from sap_agent.sap_gw_connector.tools.auth_tool import SAPAuthenticateTool
        tool = SAPAuthenticateTool()
        assert "SAP OAuth" in tool.description or "Authorization Code" in tool.description
