# tests/test_sap_auth_config.py
import pytest

from sap_agent.sap_auth_config import build_sap_auth_config


def test_build_sap_auth_config_returns_auth_config(monkeypatch):
    """AuthConfig with SAP OAuth endpoints is created from env vars."""
    monkeypatch.setenv("SAP_OAUTH_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("SAP_OAUTH_AUTHORIZE_URL", "https://sap.example.com/oauth/authorize")
    monkeypatch.setenv("SAP_OAUTH_TOKEN_URL", "https://sap.example.com/oauth/token")
    monkeypatch.setenv("SAP_OAUTH_SCOPE", "API_ACCESS")

    config = build_sap_auth_config()

    assert config is not None
    assert config.auth_scheme is not None
    assert config.raw_auth_credential is not None
    assert config.raw_auth_credential.oauth2.client_id == "test_client_id"
    assert config.raw_auth_credential.oauth2.client_secret == "test_client_secret"


def test_build_sap_auth_config_returns_none_when_missing_env(monkeypatch):
    """Returns None when required env vars are missing."""
    for key in ["SAP_OAUTH_CLIENT_ID", "SAP_OAUTH_CLIENT_SECRET",
                "SAP_OAUTH_AUTHORIZE_URL", "SAP_OAUTH_TOKEN_URL",
                "SAP_OAUTH_SCOPE"]:
        monkeypatch.delenv(key, raising=False)

    config = build_sap_auth_config()
    assert config is None
