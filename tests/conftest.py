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
