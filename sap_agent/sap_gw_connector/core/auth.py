"""SAP authentication handling with Strategy Pattern"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, TYPE_CHECKING

import aiohttp

from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig
from sap_agent.sap_gw_connector.core.exceptions import (
    SAPAuthenticationError,
    SAPConnectionError,
)

if TYPE_CHECKING:
    from sap_agent.sap_gw_connector.config.schemas import (
        AuthEndpointConfig,
        ServicesYAMLConfig,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token hierarchy
# ---------------------------------------------------------------------------


@dataclass
class AuthToken:
    """Base authentication token"""

    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() >= self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_expired

    @property
    def cookies(self) -> Dict[str, str]:
        """Cookie jar for request sessions. Override in subclasses."""
        return {}


@dataclass
class SAPUserToken(AuthToken):
    """Per-user SAP access token obtained via OAuth Authorization Code flow.

    This token represents an individual user's SAP authorization, obtained
    by exchanging an OAuth authorization code for a user-specific SAP token.
    """

    access_token: str = field(default="", repr=False)
    refresh_token: Optional[str] = field(default=None, repr=False)
    token_type: str = "Bearer"
    scope: Optional[str] = None
    sap_user: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return bool(self.access_token and not self.is_expired)


# ---------------------------------------------------------------------------
# Strategy ABC
# ---------------------------------------------------------------------------


class AuthStrategy(ABC):
    """Abstract base for authentication strategies"""

    @abstractmethod
    async def get_valid_token(self) -> AuthToken:
        """Get a valid authentication token, refreshing if needed"""
        ...

    @abstractmethod
    def get_auth_headers(self, token: AuthToken) -> Dict[str, str]:
        """Get HTTP headers for authenticated requests"""
        ...

    @abstractmethod
    async def invalidate_token(self) -> None:
        """Invalidate the current cached token"""
        ...

    @property
    def requires_csrf(self) -> bool:
        """Whether this strategy requires CSRF tokens for write operations"""
        return False


# ---------------------------------------------------------------------------
# SAP OAuth Authorization Code strategy (Option 1)
# ---------------------------------------------------------------------------


class SAPAuthorizationCodeStrategy(AuthStrategy):
    """OAuth 2.0 Authorization Code flow with PKCE for per-user SAP access.

    Flow:
    1. Agent generates an authorization URL with PKCE challenge
    2. User opens URL in browser -> SAP login page -> SAP issues auth code
    3. Agent exchanges auth code for access_token + refresh_token
    4. Subsequent requests use access_token (auto-refreshed via refresh_token)

    Each user gets their own SAP token tied to their SAP user identity,
    so all OData requests execute under that user's PFCG authorization.
    """

    _MAX_CACHED_USER_TOKENS = 1000

    def __init__(self, config: SAPConnectionConfig):
        self.config = config
        self._current_token: Optional[SAPUserToken] = None
        self._current_user_id: Optional[str] = None
        # Per-user token cache: user_id -> SAPUserToken
        self._user_tokens: Dict[str, SAPUserToken] = {}
        # PKCE state: state_param -> (code_verifier, user_id)
        self._pending_auth: Dict[str, tuple] = {}
        self._auth_lock = asyncio.Lock()
        self._last_auth_info: Optional[Dict[str, str]] = None

    @property
    def requires_csrf(self) -> bool:
        return self.config.oauth_csrf_for_writes

    def _derive_code_verifier(self, state: str) -> str:
        """Derive a deterministic PKCE code_verifier from state + client_secret.

        In Agent Engine's serverless environment, in-memory state and ADK
        session state are not reliably preserved between tool calls.  By
        deriving code_verifier deterministically via HMAC, we can regenerate
        it from the state parameter returned in the redirect callback without
        storing anything between requests.
        """
        import hmac
        import hashlib
        import base64

        secret_key = (self.config.oauth_client_secret or "default").encode()
        verifier_bytes = hmac.new(
            secret_key, state.encode(), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()

    def generate_auth_url(self, user_id: str) -> Dict[str, str]:
        """Generate SAP OAuth authorization URL with PKCE.

        Args:
            user_id: Unique identifier for the user session.
                     Used to associate the callback with the user.

        Returns:
            Dict with 'auth_url' and 'state' for the user to open in browser.
        """
        import hashlib
        import secrets
        import base64
        import urllib.parse

        authorize_url = self.config.oauth_authorize_url
        if not authorize_url:
            raise SAPAuthenticationError(
                "oauth_authorize_url is not configured"
            )

        state = secrets.token_urlsafe(32)

        # PKCE: derive code_verifier deterministically from state so it
        # can be regenerated during code exchange without in-memory storage.
        code_verifier = self._derive_code_verifier(state)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        # Also store in memory for same-instance fast path
        self._pending_auth[state] = (code_verifier, user_id)

        params = {
            "response_type": "code",
            "client_id": self.config.oauth_client_id or "",
            "redirect_uri": self.config.oauth_redirect_uri or "",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if self.config.oauth_scope:
            params["scope"] = self.config.oauth_scope

        separator = "&" if "?" in authorize_url else "?"
        auth_url = f"{authorize_url}{separator}{urllib.parse.urlencode(params)}"

        self._last_auth_info = {"auth_url": auth_url, "state": state}

        logger.info(
            "Generated SAP OAuth authorization URL for user %s", user_id
        )
        return self._last_auth_info

    async def exchange_code(
        self, authorization_code: str, state: str,
        user_id: Optional[str] = None,
    ) -> SAPUserToken:
        """Exchange authorization code for per-user SAP access token.

        Args:
            authorization_code: The code received from SAP's redirect callback.
            state: The state parameter to verify and look up PKCE verifier.
            user_id: User ID fallback when in-memory pending auth is lost.

        Returns:
            SAPUserToken with the user's SAP access credentials.
        """
        async with self._auth_lock:
            # Try in-memory fast path first
            pending = self._pending_auth.pop(state, None)
            if pending is not None:
                code_verifier, user_id = pending
            else:
                # Derive code_verifier deterministically from state
                # (in-memory state lost due to container restart)
                code_verifier = self._derive_code_verifier(state)
                user_id = user_id or "default_user"
                logger.info(
                    "Regenerated PKCE code_verifier from state for user %s",
                    user_id,
                )

            token_url = self.config.oauth_token_url
            if not token_url:
                raise SAPAuthenticationError(
                    "oauth_token_url is not configured"
                )

            data = {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": self.config.oauth_redirect_uri or "",
                "code_verifier": code_verifier,
                "client_id": self.config.oauth_client_id or "",
            }

            user_token = await self._token_request(data, user_id)

            # Cache the token
            self._cache_token(user_id, user_token)
            self._current_user_id = user_id
            logger.info(
                "SAP OAuth authorization code exchanged for user %s "
                "(SAP user: %s)",
                user_id,
                user_token.sap_user,
            )
            return user_token

    async def refresh_user_token(self, user_id: str) -> SAPUserToken:
        """Refresh an expired SAP access token using the refresh token.

        Args:
            user_id: The user whose token should be refreshed.

        Returns:
            New SAPUserToken with refreshed access credentials.
        """
        cached = self._user_tokens.get(user_id)
        if cached is None or cached.refresh_token is None:
            raise SAPAuthenticationError(
                "No refresh token available. User must re-authenticate "
                "via SAP login."
            )

        data = {
            "grant_type": "refresh_token",
            "refresh_token": cached.refresh_token,
            "client_id": self.config.oauth_client_id or "",
        }

        new_token = await self._token_request(data, user_id)
        # Preserve refresh_token if SAP didn't issue a new one
        if not new_token.refresh_token and cached.refresh_token:
            new_token = SAPUserToken(
                access_token=new_token.access_token,
                refresh_token=cached.refresh_token,
                token_type=new_token.token_type,
                scope=new_token.scope,
                sap_user=new_token.sap_user,
                expires_at=new_token.expires_at,
            )

        self._cache_token(user_id, new_token)
        logger.info("SAP token refreshed for user %s", user_id)
        return new_token

    async def _token_request(
        self, data: Dict[str, str], user_id: str
    ) -> SAPUserToken:
        """Send token request to SAP OAuth token endpoint."""
        token_url = self.config.oauth_token_url
        timeout = aiohttp.ClientTimeout(total=self.config.timeout)

        ssl_param: object = True
        if not self.config.verify_ssl:
            import ssl

            ssl_param = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_param.check_hostname = False
            ssl_param.verify_mode = ssl.CERT_NONE
            ssl_param.set_ciphers("DEFAULT@SECLEVEL=0")

        auth = aiohttp.BasicAuth(
            login=self.config.oauth_client_id or "",
            password=self.config.oauth_client_secret or "",
        )

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=aiohttp.TCPConnector(ssl=ssl_param),
        ) as session:
            try:
                async with session.post(
                    token_url,
                    data=data,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    auth=auth,
                ) as response:
                    if response.status == 200:
                        token_data = await response.json()
                        expires_in = token_data.get("expires_in", 3600)
                        expires_at = datetime.utcnow() + timedelta(
                            seconds=max(expires_in - 60, 60)
                        )

                        sap_user = token_data.get(
                            "sap_user",
                            token_data.get("user_name"),
                        )
                        if sap_user:
                            logger.info(
                                "SAP user identified: %s (user_id: %s)",
                                sap_user,
                                user_id,
                            )
                        else:
                            logger.warning(
                                "SAP token response did not include sap_user "
                                "or user_name field for user_id %s. "
                                "Authorization may use the OAuth client's "
                                "technical user.",
                                user_id,
                            )

                        return SAPUserToken(
                            access_token=token_data["access_token"],
                            refresh_token=token_data.get("refresh_token"),
                            token_type=token_data.get(
                                "token_type", "Bearer"
                            ),
                            scope=token_data.get("scope"),
                            sap_user=sap_user,
                            expires_at=expires_at,
                        )
                    else:
                        error_text = await response.text()
                        error_detail = error_text
                        try:
                            import json as _json

                            error_json = _json.loads(error_text)
                            error_code = error_json.get("error", "unknown")
                            error_desc = error_json.get(
                                "error_description", error_text
                            )
                            if error_code == "invalid_grant":
                                error_detail = (
                                    "Authorization code is invalid or expired. "
                                    "Please restart the SAP login flow."
                                )
                            elif error_code == "invalid_client":
                                error_detail = (
                                    "OAuth client credentials are invalid. "
                                    "Check SAP_OAUTH_CLIENT_ID and "
                                    "SAP_OAUTH_CLIENT_SECRET."
                                )
                            else:
                                error_detail = f"[{error_code}] {error_desc}"
                        except (ValueError, KeyError):
                            pass
                        raise SAPAuthenticationError(
                            f"SAP OAuth token request failed "
                            f"(HTTP {response.status}): {error_detail}"
                        )
            except aiohttp.ClientError as e:
                raise SAPConnectionError(
                    f"Connection error during SAP OAuth token request: {e}"
                )

    async def get_valid_token(self) -> SAPUserToken:
        """Get valid per-user SAP token, auto-refreshing if expired."""
        async with self._auth_lock:
            # 1. Return current token if still valid
            if self._current_token and self._current_token.is_valid:
                return self._current_token
            if self._current_user_id is None:
                raise SAPAuthenticationError(
                    "SAP OAuth login required. No user has authenticated yet. "
                    "Call sap_authenticate first to get the SAP login URL."
                )
            # 2. Try cached token for this user
            uid = self._current_user_id
            cached = self._user_tokens.get(uid)
            if cached and cached.is_valid:
                self._current_token = cached
                return cached

            # 3. Token expired -- auto-refresh with refresh_token
            if cached and cached.refresh_token:
                logger.info(
                    "SAP token expired for %s, refreshing via refresh_token",
                    uid,
                )
                try:
                    new_token = await self.refresh_user_token(uid)
                    return new_token
                except (SAPAuthenticationError, SAPConnectionError) as e:
                    logger.warning(
                        "SAP token refresh failed for %s: %s", uid, e
                    )
                    self._user_tokens.pop(uid, None)
                    raise SAPAuthenticationError(
                        "SAP session expired and refresh failed. "
                        "Please re-authenticate via SAP login."
                    ) from e

            # 4. No refresh token -- user must re-login
            raise SAPAuthenticationError(
                "SAP session expired. No refresh token available. "
                "Please re-authenticate via SAP login."
            )

    def get_auth_headers(self, token: AuthToken) -> Dict[str, str]:
        if not isinstance(token, SAPUserToken):
            raise TypeError(
                "SAPAuthorizationCodeStrategy requires SAPUserToken"
            )
        return {
            "Authorization": f"{token.token_type} {token.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def invalidate_token(self) -> None:
        async with self._auth_lock:
            if self._current_user_id:
                self._user_tokens.pop(self._current_user_id, None)
            self._current_token = None
            # Keep _current_user_id so refresh can be attempted
            logger.info("SAP OAuth token invalidated")

    def set_current_user(self, user_id: str) -> None:
        """Set the active user for subsequent get_valid_token() calls."""
        self._current_user_id = user_id
        cached = self._user_tokens.get(user_id)
        self._current_token = cached if cached and cached.is_valid else None

    def get_user_token(self, user_id: str) -> Optional[SAPUserToken]:
        """Get cached token for a specific user (may be expired)."""
        return self._user_tokens.get(user_id)

    def has_valid_token(self, user_id: str) -> bool:
        """Check if a user has a valid (non-expired) cached token."""
        cached = self._user_tokens.get(user_id)
        return cached is not None and cached.is_valid

    def _cache_token(self, user_id: str, token: SAPUserToken) -> None:
        """Cache a per-user token with eviction."""
        self.cleanup_expired_tokens()
        if len(self._user_tokens) >= self._MAX_CACHED_USER_TOKENS:
            oldest = next(iter(self._user_tokens))
            del self._user_tokens[oldest]
        self._user_tokens[user_id] = token
        self._current_token = token

    def cleanup_expired_tokens(self) -> int:
        """Remove expired tokens. Returns count removed."""
        expired = [
            uid for uid, t in self._user_tokens.items() if t.is_expired
        ]
        for uid in expired:
            del self._user_tokens[uid]
            self._pending_auth = {
                k: v for k, v in self._pending_auth.items() if v[1] != uid
            }
        if expired:
            logger.info("Cleaned up %d expired SAP OAuth tokens", len(expired))
        return len(expired)

    @property
    def active_user_count(self) -> int:
        return len(self._user_tokens)


# ---------------------------------------------------------------------------
# Facade (backward-compatible public API)
# ---------------------------------------------------------------------------


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
