# tests/test_adk_auth_flow.py
"""Test ADK auth integration in sap_authenticate."""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _mock_runtime_secrets_and_cleanup():
    """Prevent _load_runtime_secrets from hitting Secret Manager and clean up global state."""
    import sap_agent.agent as agent_mod

    with patch("sap_agent.agent._load_runtime_secrets"):
        yield

    # Clean up global state to prevent leaking between tests
    agent_mod._user_authenticators.clear()
    agent_mod._last_authenticated_uid = None


def test_sap_authenticate_falls_through_to_custom_oauth_when_no_adk_credential(monkeypatch):
    """When ADK get_auth_response returns None, fall through to custom OAuth flow."""
    monkeypatch.setenv("SAP_AUTH_TYPE", "sap_oauth")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_ID", "test_id")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("SAP_OAUTH_AUTHORIZE_URL", "https://sap/oauth/authorize")
    monkeypatch.setenv("SAP_OAUTH_TOKEN_URL", "https://sap/oauth/token")

    mock_auth_config = MagicMock()

    mock_tool_context = MagicMock()
    mock_tool_context.get_auth_response.return_value = None
    mock_tool_context.state = {}
    mock_tool_context._invocation_context.user_id = "default-user-id"
    mock_tool_context._invocation_context.session.id = "test-session"

    from sap_agent.agent import sap_authenticate

    with patch("sap_agent.sap_auth_config.build_sap_auth_config", return_value=mock_auth_config), \
         patch("sap_agent.agent._find_any_pending_oauth_code", return_value=None):
        result = sap_authenticate(tool_context=mock_tool_context)

    # Should NOT return adk_oauth — should fall through to custom OAuth flow
    # and return sap_login with auth_url
    assert result.get("action_required") == "sap_login"
    assert "auth_url" in result


def test_sap_authenticate_uses_adk_credential(monkeypatch):
    """When ADK auth response has access_token, use it for SAP."""
    monkeypatch.setenv("SAP_AUTH_TYPE", "sap_oauth")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_ID", "test_id")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("SAP_OAUTH_AUTHORIZE_URL", "https://sap/oauth/authorize")
    monkeypatch.setenv("SAP_OAUTH_TOKEN_URL", "https://sap/oauth/token")
    monkeypatch.setenv("SAP_HOST", "10.0.0.1")

    mock_credential = MagicMock()
    mock_credential.oauth2.access_token = "sap_access_token_123"
    mock_credential.oauth2.refresh_token = "sap_refresh_token_456"
    mock_credential.oauth2.expires_in = 3600

    mock_auth_config = MagicMock()

    mock_tool_context = MagicMock()
    mock_tool_context.get_auth_response.return_value = mock_credential
    mock_tool_context.state = {}
    mock_tool_context._invocation_context.user_id = "default-user-id"
    mock_tool_context._invocation_context.session.id = "test-session"

    from sap_agent.agent import sap_authenticate

    with patch("sap_agent.sap_auth_config.build_sap_auth_config", return_value=mock_auth_config), \
         patch("sap_agent.agent._build_authenticator_from_adk_credential"):
        result = sap_authenticate(tool_context=mock_tool_context)

    assert result["success"] is True
    assert result["auth_type"] == "sap_oauth_adk"


def test_sap_query_falls_through_when_no_adk_credential(monkeypatch):
    """sap_query falls through to normal flow when ADK credential is unavailable."""
    monkeypatch.setenv("SAP_AUTH_TYPE", "sap_oauth")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_ID", "test_id")
    monkeypatch.setenv("SAP_OAUTH_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("SAP_OAUTH_AUTHORIZE_URL", "https://sap/oauth/authorize")
    monkeypatch.setenv("SAP_OAUTH_TOKEN_URL", "https://sap/oauth/token")
    monkeypatch.setenv("SAP_HOST", "10.0.0.1")

    mock_auth_config = MagicMock()

    mock_tool_context = MagicMock()
    mock_tool_context.get_auth_response.return_value = None
    mock_tool_context.state = {}
    mock_tool_context._invocation_context.user_id = "default-user-id"
    mock_tool_context._invocation_context.session.id = "test-session"

    from sap_agent.agent import sap_query

    # Mock service config to avoid depending on services.yaml
    mock_service_info = MagicMock()
    mock_service_info.path = "/sap/opu/odata/sap/Z_TEST_SRV"
    mock_services_config = MagicMock()
    mock_services_config.get_service.return_value = mock_service_info

    with patch("sap_agent.sap_auth_config.build_sap_auth_config", return_value=mock_auth_config), \
         patch("sap_agent.agent._get_authenticator_for_session", return_value=None), \
         patch("sap_agent.agent.ensure_sap_config"), \
         patch("sap_agent.sap_gw_connector.config.loader.get_services_config", return_value=mock_services_config):
        result = sap_query(
            service="test_service",
            entity_set="TestSet",
            tool_context=mock_tool_context,
        )

    # Should NOT call request_credential (Gemini Enterprise doesn't support it)
    mock_tool_context.request_credential.assert_not_called()
    # Should return an error since no authenticator is available
    assert result.get("success") is False or "error" in result
