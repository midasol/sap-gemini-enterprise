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
