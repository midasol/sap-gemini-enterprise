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

    def test_optional_scope(self):
        config = SAPConnectionConfig(
            host="sap.example.com",
            oauth_client_id="id",
            oauth_client_secret="sec",
            oauth_token_url="https://sap/token",
            oauth_authorize_url="https://sap/authorize",
            oauth_scope="MY_SCOPE",
        )
        assert config.oauth_scope == "MY_SCOPE"

    def test_optional_redirect_uri(self):
        config = SAPConnectionConfig(
            host="sap.example.com",
            oauth_client_id="id",
            oauth_client_secret="sec",
            oauth_token_url="https://sap/token",
            oauth_authorize_url="https://sap/authorize",
            oauth_redirect_uri="http://localhost/callback",
        )
        assert config.oauth_redirect_uri == "http://localhost/callback"


class TestEnvVarIntegration:
    def test_env_vars_sap_oauth(self, sap_oauth_env):
        config = SAPConnectionConfig()
        assert config.host == "sap.example.com"
        assert config.auth_type == "sap_oauth"
        assert config.oauth_authorize_url is not None

    def test_env_prefix(self, monkeypatch):
        monkeypatch.setenv("SAP_HOST", "env-host.example.com")
        monkeypatch.setenv("SAP_PORT", "8443")
        monkeypatch.setenv("SAP_CLIENT", "200")
        monkeypatch.setenv("SAP_AUTH_TYPE", "sap_oauth")
        monkeypatch.setenv("SAP_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("SAP_OAUTH_CLIENT_SECRET", "csec")
        monkeypatch.setenv("SAP_OAUTH_TOKEN_URL", "https://t/token")
        monkeypatch.setenv("SAP_OAUTH_AUTHORIZE_URL", "https://t/auth")

        config = SAPConnectionConfig()
        assert config.host == "env-host.example.com"
        assert config.port == 8443
        assert config.client == "200"
