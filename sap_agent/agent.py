"""SAP Agent with Direct Python Tools for OData Queries.

This agent provides SAP OData integration using direct Python functions,
making it compatible with Agent Engine deployment.

Supports both local development and Agent Engine deployment environments.
"""

import os
import re
import json
import asyncio
import logging
import threading
from functools import cached_property
from pathlib import Path
from typing import Optional, Dict, Any, List

logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Enable nested event loops for Agent Engine compatibility
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass  # nest_asyncio not available, may fail in nested async contexts

# Import Google Secret Manager (lazy loading to avoid startup issues)
HAS_SECRET_MANAGER = False
secretmanager = None

# Per-user authenticator cache: user_id → SAPAuthenticator
_user_authenticators: Dict[str, "SAPAuthenticator"] = {}
_user_authenticators_lock = threading.Lock()
_last_authenticated_uid: Optional[str] = None
_MAX_CACHED_USERS = 1000

def _get_secret_manager():
    """Lazy load secret manager to avoid import-time permission issues."""
    global secretmanager, HAS_SECRET_MANAGER
    if secretmanager is None:
        try:
            from google.cloud import secretmanager as sm
            secretmanager = sm
            HAS_SECRET_MANAGER = True
        except ImportError:
            HAS_SECRET_MANAGER = False
    return secretmanager

# ---------------------------------------------------------------------------
# Secret Manager token persistence (cross-worker, cross-session)
# ---------------------------------------------------------------------------
_TOKEN_SECRET_PREFIX = "sap-oauth-token"
_TOKEN_SECRET_MAX_VERSIONS = 3

_PENDING_SECRET_PREFIX = "sap-oauth-pending"
_PENDING_CODE_TTL_MINUTES = 10

# Track the last token hash written to Secret Manager per user to avoid
# redundant writes when the token hasn't changed (e.g. after every sap_query).
_last_saved_token_hash: Dict[str, str] = {}


def _pending_secret_id(state: str) -> str:
    """Create a Secret Manager-safe ID from the first 16 chars of state."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", state[:16])
    return f"{_PENDING_SECRET_PREFIX}-{safe}"


def _check_pending_oauth_code(state: str) -> Optional[dict]:
    """Check Secret Manager for a pending OAuth code stored by Cloud Run.

    Args:
        state: The full OAuth state parameter from Step 1.

    Returns:
        Dict with 'code', 'state', 'timestamp' if found and valid, else None.
    """
    sm = _get_secret_manager()
    if sm is None:
        return None

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return None

    secret_id = _pending_secret_id(state)

    try:
        client = sm.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        data = json.loads(response.payload.data.decode("UTF-8"))

        # Verify full state matches (secret ID uses only first 16 chars)
        if data.get("state") != state:
            logger.debug("_check_pending_oauth_code: state mismatch")
            return None

        # Check expiry
        from datetime import datetime, timezone, timedelta
        ts = datetime.fromisoformat(data["timestamp"])
        if datetime.now(timezone.utc) - ts > timedelta(minutes=_PENDING_CODE_TTL_MINUTES):
            logger.warning("_check_pending_oauth_code: expired (>%d min)", _PENDING_CODE_TTL_MINUTES)
            return None

        logger.info("_check_pending_oauth_code: found pending code for state=%.8s...", state)
        return data
    except Exception:
        return None


def _find_any_pending_oauth_code() -> Optional[dict]:
    """Search Secret Manager for any valid pending OAuth code.

    Unlike _check_pending_oauth_code (which needs a known state),
    this function lists all sap-oauth-pending-* secrets and returns
    the first valid (non-expired) one.  Used when _last_auth_info
    is unavailable (e.g. after container restart / cross-worker routing).
    """
    sm = _get_secret_manager()
    if sm is None:
        return None

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return None

    try:
        client = sm.SecretManagerServiceClient()
        parent = f"projects/{project_id}"
        secrets = client.list_secrets(request={"parent": parent})

        from datetime import datetime, timezone, timedelta

        for secret in secrets:
            # Only check sap-oauth-pending-* secrets
            secret_id = secret.name.split("/")[-1]
            if not secret_id.startswith(_PENDING_SECRET_PREFIX):
                continue

            try:
                name = f"{secret.name}/versions/latest"
                response = client.access_secret_version(request={"name": name})
                data = json.loads(response.payload.data.decode("UTF-8"))

                ts = datetime.fromisoformat(data["timestamp"])
                if datetime.now(timezone.utc) - ts > timedelta(minutes=_PENDING_CODE_TTL_MINUTES):
                    continue  # expired

                logger.info(
                    "_find_any_pending_oauth_code: found pending code "
                    "(secret=%s, state=%.8s...)",
                    secret_id, data.get("state", ""),
                )
                return data
            except Exception:
                continue

        return None
    except Exception as e:
        logger.debug("_find_any_pending_oauth_code: %s", e)
        return None


def _cleanup_pending_oauth_secret(state: str) -> None:
    """Delete the ephemeral pending code secret after successful exchange."""
    sm = _get_secret_manager()
    if sm is None:
        return

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return

    secret_id = _pending_secret_id(state)

    try:
        client = sm.SecretManagerServiceClient()
        secret_path = f"projects/{project_id}/secrets/{secret_id}"
        client.delete_secret(request={"name": secret_path})
        logger.info("_cleanup_pending_oauth_secret: deleted %s", secret_id)
    except Exception as e:
        logger.debug("_cleanup_pending_oauth_secret: %s", e)


def _secret_name_for_uid(uid: str) -> str:
    """Return the Secret Manager secret ID for a given user.

    Sanitises the UID so it only contains characters allowed by Secret
    Manager (``[a-zA-Z0-9_-]``).  UIDs that are empty or contain only
    invalid characters fall back to ``sap-oauth-token-default``.
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", uid) if uid else "default"
    if not safe:
        safe = "default"
    return f"{_TOKEN_SECRET_PREFIX}-{safe}"


def _ensure_secret_exists(client, project_id: str, secret_id: str) -> str:
    """Create a Secret Manager secret if it does not exist yet.

    Returns the fully-qualified secret name.
    """
    parent = f"projects/{project_id}"
    secret_path = f"{parent}/secrets/{secret_id}"
    try:
        client.get_secret(request={"name": secret_path})
    except Exception:
        # Secret does not exist — create it
        try:
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
            logger.info("_ensure_secret_exists: created secret %s", secret_id)
        except Exception as e:
            # Another worker may have created it concurrently
            if "ALREADY_EXISTS" not in str(e):
                raise
    return secret_path


def _save_token_to_secret(token_data: dict) -> bool:
    """Save OAuth token data to a per-user Secret Manager secret.

    Each user gets their own secret (``sap-oauth-token-<uid>``).
    Old versions beyond _TOKEN_SECRET_MAX_VERSIONS are destroyed
    automatically.

    Skips the write if the token data hasn't changed since the last
    save (based on a hash of access_token + refresh_token + expires_at).
    """
    import hashlib

    sm = _get_secret_manager()
    if sm is None:
        return False

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return False

    uid = token_data.get("user_id", "default")

    # Check if the token has actually changed since last save
    hash_input = (
        f"{token_data.get('access_token', '')}"
        f"{token_data.get('refresh_token', '')}"
        f"{token_data.get('expires_at', '')}"
    )
    token_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
    if _last_saved_token_hash.get(uid) == token_hash:
        logger.debug(
            "_save_token_to_secret: skipped (unchanged) for uid=%s", uid,
        )
        return True

    secret_id = _secret_name_for_uid(uid)

    try:
        client = sm.SecretManagerServiceClient()
        parent = _ensure_secret_exists(client, project_id, secret_id)

        payload = json.dumps(token_data).encode("UTF-8")
        version = client.add_secret_version(
            request={"parent": parent, "payload": {"data": payload}}
        )
        _last_saved_token_hash[uid] = token_hash
        logger.info(
            "_save_token_to_secret: saved version %s for uid=%s (secret=%s)",
            version.name.split("/")[-1], uid, secret_id,
        )

        _cleanup_secret_versions(client, parent, _TOKEN_SECRET_MAX_VERSIONS)
        return True

    except Exception as e:
        logger.error("_save_token_to_secret: failed for uid=%s: %s", uid, e)
        return False


def _find_any_token_secret_uid() -> Optional[str]:
    """Scan Secret Manager for any ``sap-oauth-token-*`` secret.

    Returns the user_id extracted from the token data, or *None*.
    Used as a last-resort cross-session recovery when the current
    session has no cached token (e.g. new session on Agent Engine).
    """
    sm = _get_secret_manager()
    if sm is None:
        return None

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return None

    try:
        client = sm.SecretManagerServiceClient()
        parent = f"projects/{project_id}"
        prefix = f"{_TOKEN_SECRET_PREFIX}-"
        for secret in client.list_secrets(request={"parent": parent}):
            name = secret.name.split("/")[-1]
            if name.startswith(prefix) and name != _TOKEN_SECRET_PREFIX:
                # Found a per-user token secret — load it to get user_id
                try:
                    ver = f"{secret.name}/versions/latest"
                    resp = client.access_secret_version(
                        request={"name": ver}
                    )
                    data = json.loads(resp.payload.data.decode("UTF-8"))
                    found_uid = data.get("user_id")
                    if found_uid:
                        logger.info(
                            "_find_any_token_secret_uid: found uid=%s "
                            "from secret=%s",
                            found_uid, name,
                        )
                        return found_uid
                except Exception:
                    continue
    except Exception as e:
        logger.debug("_find_any_token_secret_uid: scan failed: %s", e)

    return None


def _load_token_from_secret(uid: Optional[str] = None) -> Optional[dict]:
    """Load the latest OAuth token data from a per-user Secret Manager secret.

    Args:
        uid: The user ID whose token to load.  If *None*, falls back to
             the legacy single-secret name for backward compatibility.
    """
    sm = _get_secret_manager()
    if sm is None:
        return None

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return None

    secret_id = _secret_name_for_uid(uid) if uid else _TOKEN_SECRET_PREFIX

    try:
        client = sm.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        token_data = json.loads(response.payload.data.decode("UTF-8"))
        logger.info(
            "_load_token_from_secret: loaded token for uid=%s (secret=%s)",
            token_data.get("user_id"), secret_id,
        )
        return token_data
    except Exception as e:
        # If per-user secret not found and uid was given, try legacy secret
        if uid and secret_id != _TOKEN_SECRET_PREFIX:
            logger.debug(
                "_load_token_from_secret: per-user secret not found for "
                "uid=%s, trying legacy secret",
                uid,
            )
            return _load_token_from_secret(uid=None)
        logger.debug("_load_token_from_secret: not available: %s", e)
        return None


def _cleanup_secret_versions(client, secret_name: str, keep: int) -> None:
    """Destroy old secret versions, keeping only the latest `keep` enabled."""
    try:
        versions = list(
            client.list_secret_versions(request={"parent": secret_name})
        )
        # Filter to enabled versions, sorted newest first
        enabled = sorted(
            [v for v in versions if v.state.name == "ENABLED"],
            key=lambda v: v.create_time,
            reverse=True,
        )
        for old in enabled[keep:]:
            client.destroy_secret_version(request={"name": old.name})
            logger.debug(
                "_cleanup_secret_versions: destroyed %s", old.name
            )
    except Exception as e:
        logger.debug("_cleanup_secret_versions: %s", e)


from google.adk.agents.llm_agent import Agent
from google.adk.models import Gemini
from google.genai import Client, types

# ToolContext for per-session state management (multi-user support)
try:
    from google.adk.tools import ToolContext
    HAS_TOOL_CONTEXT = True
except ImportError:
    HAS_TOOL_CONTEXT = False
    ToolContext = None  # type: ignore[assignment,misc]


# =============================================================================
# Custom Gemini Model for Global Endpoint
# =============================================================================
# Workaround for Agent Engine overriding GOOGLE_CLOUD_LOCATION
# See: https://github.com/google/adk-python/issues/3628#issuecomment-3595215761

class GlobalGemini(Gemini):
    """Gemini model subclass that forces global endpoint.

    Agent Engine reserves and overrides GOOGLE_CLOUD_LOCATION to deployment region,
    but Gemini 3 models require the global endpoint. This subclass overrides
    the api_client property to explicitly use location="global".
    """

    @cached_property
    def api_client(self) -> Client:
        """Provides the api client with explicit global location.

        Returns:
            The api client initialized with global location and http_options.
        """
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID", "")

        # Explicitly setting location to 'global' for Gemini 3 models
        location = "global"

        return Client(
            project=project,
            location=location,
            http_options=types.HttpOptions(
                headers=self._tracking_headers(),  # Call method to get dict
                retry_options=self.retry_options,
            )
        )


# =============================================================================
# Secret Management
# =============================================================================

def load_secrets_from_manager(force: bool = False) -> bool:
    """Load SAP credentials from Secret Manager if not in environment.

    Args:
        force: If True, reload secrets even if SAP_HOST is already set

    Returns:
        True if secrets were loaded successfully, False otherwise
    """
    # Only attempt if not already set
    if not force and os.getenv("SAP_HOST"):
        return True

    # Get secret manager (lazy load)
    sm = _get_secret_manager()
    if sm is None:
        logger.debug("Secret Manager not available.")
        return False

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        # Try to get project ID from metadata server (Agent Engine)
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                headers={"Metadata-Flavor": "Google"}
            )
            with urllib.request.urlopen(req, timeout=2) as response:
                project_id = response.read().decode()
                os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
        except Exception:
            pass

    if not project_id:
        logger.debug("PROJECT_ID not found in environment for Secret Manager.")
        return False

    logger.debug("Attempting to load secrets for project_id: %s", project_id)
    try:
        client = sm.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/sap-credentials/versions/latest"
        response = client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("UTF-8")
        secrets = json.loads(payload)

        logger.debug("Loaded secrets: %s", list(secrets.keys()))
        # Set environment variables
        for key, value in secrets.items():
            env_key = f"SAP_{key.upper()}"
            os.environ[env_key] = str(value)

        print("Successfully loaded SAP credentials from Secret Manager.")
        return True
    except Exception as e:
        print(f"Warning: Failed to load secrets from Secret Manager: {e}")
        return False


def _load_runtime_secrets():
    """Load secrets that are intentionally excluded from deploy-time env_vars.

    Some settings (e.g., oauth_redirect_uri) depend on values only known after
    deployment (e.g., Gemini Enterprise agent ID). These are excluded from
    deploy-time env_vars and read from Secret Manager at runtime instead.
    """
    # Only needed for sap_oauth auth type
    if os.getenv("SAP_AUTH_TYPE", "").lower() != "sap_oauth":
        return

    # Only load if redirect_uri is not already set
    if os.getenv("SAP_OAUTH_REDIRECT_URI"):
        return

    sm = _get_secret_manager()
    if sm is None:
        return

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    if not project_id:
        return

    try:
        client = sm.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/sap-credentials/versions/latest"
        response = client.access_secret_version(request={"name": name})
        secrets = json.loads(response.payload.data.decode("UTF-8"))

        runtime_keys = ["oauth_redirect_uri"]
        for key in runtime_keys:
            if key in secrets:
                env_key = f"SAP_{key.upper()}"
                os.environ[env_key] = str(secrets[key])
                print(f"Loaded {env_key} from Secret Manager (runtime)")
    except Exception as e:
        print(f"Warning: Could not load runtime secrets: {e}")


def ensure_sap_config():
    """Ensure SAP configuration is available. Call this before any SAP operation."""
    logger.debug("ensure_sap_config: SAP_HOST=%s, PROJECT_ID=%s",
                 os.getenv('SAP_HOST'), os.getenv('GOOGLE_CLOUD_PROJECT'))

    # Try to load secrets if not already loaded (from env_vars or Secret Manager)
    if not os.getenv("SAP_HOST"):
        try:
            success = load_secrets_from_manager()
            logger.debug("load_secrets_from_manager returned %s", success)
        except Exception as e:
            print(f"Warning: Could not load from Secret Manager: {e}")
        logger.debug("After loading - SAP_HOST=%s", os.getenv('SAP_HOST'))

    # Load runtime-only secrets (e.g., redirect_uri excluded from deploy env_vars)
    _load_runtime_secrets()

    # Reset cached config to pick up new environment variables
    from sap_agent.sap_gw_connector.config import settings
    settings.config = None

    # Verify env vars are set
    required = [
        "SAP_HOST", "SAP_OAUTH_CLIENT_ID", "SAP_OAUTH_CLIENT_SECRET",
        "SAP_OAUTH_TOKEN_URL", "SAP_OAUTH_AUTHORIZE_URL",
    ]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        raise RuntimeError(
            f"SAP credentials not configured. Please use sap_authenticate to provide your credentials."
        )


def configure_services_path():
    """Configure SAP services path for remote environment."""
    # If explicitly set, respect it
    if os.getenv("SAP_SERVICES_CONFIG_PATH"):
        return

    # Check for services.yaml in the uploaded agent_config directory
    # agent_config should be in the root of the working directory in Agent Engine
    root_dir = Path.cwd()
    services_yaml = root_dir / "agent_config" / "services.yaml"

    if services_yaml.exists():
        os.environ["SAP_SERVICES_CONFIG_PATH"] = str(services_yaml.resolve())
        print(f"Configured SAP services path: {services_yaml}")
    else:
        # Fallback: check relative to this file (for local dev)
        local_yaml = Path(__file__).parent / "services.yaml"
        if local_yaml.exists():
             os.environ["SAP_SERVICES_CONFIG_PATH"] = str(local_yaml.resolve())
             print(f"Configured SAP services path (local): {local_yaml}")
        else:
             print(f"Warning: services.yaml not found in {services_yaml} or {local_yaml}")


# Attempt to load secrets and config at startup
# Only try Secret Manager if SAP_HOST is not already set via env_vars
# This prevents permission errors during Agent Engine startup when env_vars are used
if not os.getenv("SAP_HOST"):
    # Silently try to load - don't crash if it fails
    try:
        load_secrets_from_manager()
    except Exception as e:
        print(f"Note: Could not load from Secret Manager (may use env_vars instead): {e}")
else:
    print(f"SAP credentials already configured via environment variables")
configure_services_path()


# =============================================================================
# Configuration
# =============================================================================

def get_model_name() -> str:
    """Get the LLM model name.

    Can be overridden via SAP_AGENT_MODEL environment variable.
    """
    return os.getenv("SAP_AGENT_MODEL", "gemini-3.1-pro-preview")


def get_model() -> GlobalGemini:
    """Get the LLM model instance with global endpoint.

    Uses GlobalGemini subclass to force global endpoint for Gemini 3 models,
    which is required even when Agent Engine overrides GOOGLE_CLOUD_LOCATION.
    """
    model_name = get_model_name()
    return GlobalGemini(model=model_name)


MODEL_NAME = get_model_name()  # For backward compatibility and logging
MODEL = get_model()  # Actual model instance with global endpoint


# =============================================================================
# SAP Client Utilities (Lazy Loading)
# =============================================================================

_sap_client_instance = None
_sap_client_lock = asyncio.Lock()


def get_services_config_path() -> Optional[Path]:
    """Get services configuration file path."""
    # Check environment variable first
    env_path = os.getenv("SAP_SERVICES_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # Check local path
    local_yaml = Path(__file__).parent / "services.yaml"
    if local_yaml.exists():
        return local_yaml

    # Check agent_config path (for Agent Engine)
    agent_config_yaml = Path.cwd() / "agent_config" / "services.yaml"
    if agent_config_yaml.exists():
        return agent_config_yaml

    return None


def _transform_response(data: Dict[str, Any], output_format: str = "json_compact") -> Dict[str, Any]:
    """Transform OData response based on requested format.

    Args:
        data: Raw OData response
        output_format: 'json' for raw response, 'json_compact' for cleaned response

    Returns:
        Transformed response with reduced token usage for json_compact format
    """
    if output_format == "json":
        return data

    # json_compact: Remove __metadata and __deferred navigation links
    results = data.get("d", {}).get("results", [])

    if not results:
        # Handle single entity response (no results array)
        if "d" in data and isinstance(data["d"], dict):
            entity = data["d"]
            clean_entity = {}
            for key, value in entity.items():
                # Skip metadata
                if key == "__metadata":
                    continue
                # Skip deferred navigation links
                if isinstance(value, dict) and "__deferred" in value:
                    continue
                clean_entity[key] = value
            return {"result": clean_entity}
        return data

    # Process results array
    clean_results: List[Dict[str, Any]] = []
    for item in results:
        clean_item: Dict[str, Any] = {}
        for key, value in item.items():
            # Skip metadata
            if key == "__metadata":
                continue
            # Skip deferred navigation links
            if isinstance(value, dict) and "__deferred" in value:
                continue
            # Keep expanded navigation properties (they have actual data)
            clean_item[key] = value
        clean_results.append(clean_item)

    return {"results": clean_results, "count": len(clean_results)}


# =============================================================================
# Multi-User Authenticator Management
# =============================================================================


def _get_uid_from_context(tool_context: Optional[Any]) -> Optional[str]:
    """Extract user ID from ToolContext using the best available source.

    Priority:
      1. invocation_context.user_id — real identity (skip ``default-user-id``)
      2. session state "user_id" — set after SAP OAuth authentication
      3. session-based UID (``session-{session_id}``) — stable within a session
      4. _last_authenticated_uid — in-memory fallback
    """
    if tool_context is None:
        logger.debug("_get_uid_from_context: tool_context is None")
        return None

    _session_id = None

    # 1. Gemini Enterprise / Agent Engine user_id (skip "default-user-id")
    try:
        _ctx = tool_context._invocation_context
        ctx_uid = _ctx.user_id
        _session_id = _ctx.session.id if _ctx.session else None
        logger.info(
            "_get_uid_from_context: invocation_context.user_id=%r, "
            "session_id=%s",
            ctx_uid, _session_id,
        )
        if ctx_uid and ctx_uid != "default-user-id":
            return ctx_uid
    except Exception as e:
        logger.debug(
            "_get_uid_from_context: invocation_context access failed: %s", e,
        )

    # 2. Session state (set after successful OAuth)
    if hasattr(tool_context, "state"):
        try:
            state_uid = tool_context.state.get("user_id")
            if state_uid:
                logger.info(
                    "_get_uid_from_context: using session state user_id=%s",
                    state_uid,
                )
                return state_uid
        except Exception as e:
            logger.debug(
                "_get_uid_from_context: session state access failed: %s", e,
            )

    # 3. Session-based UID — unique per session, stable within a session
    if _session_id:
        session_uid = f"session-{_session_id}"
        logger.info(
            "_get_uid_from_context: using session-based uid=%s", session_uid,
        )
        return session_uid

    logger.info("_get_uid_from_context: no user_id found in any source")
    return None


def _get_authenticator_for_session(
    tool_context: Optional[Any] = None,
) -> Optional["SAPAuthenticator"]:
    """Get the authenticator for the current user session.

    Looks up the user ID from ToolContext session state (if available),
    then retrieves the per-user authenticator from the in-memory cache.
    Falls back to the last authenticated user if ToolContext is not available.

    If in-memory cache misses (e.g., request routed to a different worker
    process in Agent Engine), attempts to reconstruct the authenticator
    from token data persisted in ADK session state.
    """
    global _last_authenticated_uid
    uid = None

    # Diagnostic: log session/state info for cross-turn persistence debugging
    if tool_context is not None:
        _diag_sid = _diag_uid = None
        _diag_state_keys = []
        try:
            _ctx = tool_context._invocation_context
            _diag_sid = _ctx.session.id if _ctx.session else None
            _diag_uid = _ctx.user_id
        except Exception:
            pass
        if hasattr(tool_context, "state"):
            try:
                _diag_state_keys = list(tool_context.state.keys())
            except Exception:
                pass
        logger.info(
            "_get_auth: diag session_id=%s, user_id=%s, "
            "state_keys=%s, cache_size=%d, last_uid=%s",
            _diag_sid, _diag_uid, _diag_state_keys,
            len(_user_authenticators), _last_authenticated_uid,
        )

    # Get UID from invocation context, session state, or fallback
    uid = _get_uid_from_context(tool_context)
    if uid is None:
        uid = _last_authenticated_uid

    if uid is None:
        logger.info(
            "_get_authenticator_for_session: no uid in state/cache, "
            "will try Secret Manager"
        )
        # Fall through to Secret Manager recovery below
        # (skip in-memory lookup and session state reconstruction)
        # No uid available — try legacy single secret as last resort
        token_data = _load_token_from_secret(uid=None)
        if token_data is not None:
            _sm_uid = token_data.get("user_id", "default_user")
            logger.info(
                "_get_authenticator_for_session: Secret Manager has "
                "token for uid=%s",
                _sm_uid,
            )
            try:
                authenticator = _reconstruct_authenticator_from_state(
                    _sm_uid, token_data
                )
                if authenticator is not None:
                    with _user_authenticators_lock:
                        _user_authenticators[_sm_uid] = authenticator
                    _last_authenticated_uid = _sm_uid
                    logger.info(
                        "_get_authenticator_for_session: recovered from "
                        "Secret Manager for uid=%s",
                        _sm_uid,
                    )
                    return authenticator
            except Exception as e:
                logger.error(
                    "_get_authenticator_for_session: Secret Manager "
                    "recovery failed: %s",
                    e,
                )
        return None

    with _user_authenticators_lock:
        entry = _user_authenticators.get(uid)
        logger.info(
            "_get_authenticator_for_session: uid=%s, found=%s, "
            "cache_keys=%s",
            uid, entry is not None, list(_user_authenticators.keys()),
        )
        if entry is not None:
            return entry

    # In-memory cache miss — try to reconstruct from ADK session state.
    # This handles cross-worker requests in Agent Engine where each
    # uvicorn worker has its own in-memory cache.
    if tool_context is not None and hasattr(tool_context, "state"):
        token_data = tool_context.state.get("sap_token_data")
        if token_data is not None:
            logger.info(
                "_get_authenticator_for_session: in-memory miss, "
                "reconstructing from session state for uid=%s",
                uid,
            )
            try:
                authenticator = _reconstruct_authenticator_from_state(
                    uid, token_data
                )
                if authenticator is not None:
                    with _user_authenticators_lock:
                        _user_authenticators[uid] = authenticator
                    _last_authenticated_uid = uid
                    logger.info(
                        "_get_authenticator_for_session: reconstructed "
                        "and cached for uid=%s",
                        uid,
                    )
                    return authenticator
            except Exception as e:
                logger.error(
                    "_get_authenticator_for_session: failed to "
                    "reconstruct from session state: %s",
                    e,
                )

    # Last resort: try Secret Manager (survives cross-worker AND cross-session)
    token_data = _load_token_from_secret(uid=uid)

    # If per-session secret not found, scan for any existing user token.
    # This handles cross-session recovery when Agent Engine creates new
    # sessions (new session_id = new UID, but token exists under old UID).
    if token_data is None:
        _found_uid = _find_any_token_secret_uid()
        if _found_uid:
            logger.info(
                "_get_authenticator_for_session: cross-session recovery, "
                "found token for uid=%s",
                _found_uid,
            )
            token_data = _load_token_from_secret(uid=_found_uid)
    if token_data is not None:
        _sm_uid = token_data.get("user_id", uid or "unknown")
        logger.info(
            "_get_authenticator_for_session: trying Secret Manager "
            "recovery for uid=%s",
            _sm_uid,
        )
        try:
            authenticator = _reconstruct_authenticator_from_state(
                _sm_uid, token_data
            )
            if authenticator is not None:
                with _user_authenticators_lock:
                    _user_authenticators[_sm_uid] = authenticator
                _last_authenticated_uid = _sm_uid
                logger.info(
                    "_get_authenticator_for_session: recovered from "
                    "Secret Manager for uid=%s",
                    _sm_uid,
                )
                return authenticator
        except Exception as e:
            logger.error(
                "_get_authenticator_for_session: Secret Manager "
                "recovery failed: %s",
                e,
            )

    return None


def _reconstruct_authenticator_from_state(
    uid: str, token_data: dict,
) -> Optional["SAPAuthenticator"]:
    """Reconstruct a SAPAuthenticator from token data persisted in session state.

    Called when in-memory cache misses due to cross-worker routing in
    Agent Engine. Creates a fresh SAPAuthenticator and injects the
    previously obtained token into its strategy.
    """
    from datetime import datetime
    from sap_agent.sap_gw_connector.config.settings import get_config
    from sap_agent.sap_gw_connector.core.auth import (
        SAPAuthenticator,
        SAPUserToken,
    )

    expires_at = datetime.fromisoformat(token_data["expires_at"])

    token = SAPUserToken(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        token_type=token_data.get("token_type", "Bearer"),
        scope=token_data.get("scope"),
        sap_user=token_data.get("sap_user"),
        expires_at=expires_at,
    )

    # If token is expired and has no refresh_token, reconstruction is futile
    if token.is_expired and not token.refresh_token:
        logger.info(
            "_reconstruct_authenticator: token expired with no "
            "refresh_token for uid=%s",
            uid,
        )
        return None

    config = get_config(require_sap=True)
    authenticator = SAPAuthenticator(config.sap)

    if authenticator.uses_authorization_code:
        strategy = authenticator._strategy
        user_id = token_data.get("user_id", uid)
        strategy._cache_token(user_id, token)
        strategy.set_current_user(user_id)
        logger.info(
            "_reconstruct_authenticator: success for uid=%s, "
            "sap_user=%s, expired=%s, has_refresh=%s",
            uid, token.sap_user, token.is_expired,
            token.refresh_token is not None,
        )
        return authenticator

    logger.warning(
        "_reconstruct_authenticator: strategy is not authorization_code"
    )
    return None


def _store_authenticator(
    uid: str,
    authenticator: "SAPAuthenticator",
    tool_context: Optional[Any] = None,
) -> None:
    """Store an authenticator for a user and update session state.

    Persists both in-memory (per-worker) and in ADK session state
    (cross-worker). The session state serialization allows other worker
    processes in Agent Engine to reconstruct the authenticator.
    """
    global _last_authenticated_uid

    with _user_authenticators_lock:
        # Enforce max cache size: evict oldest entries if at capacity
        while len(_user_authenticators) >= _MAX_CACHED_USERS:
            oldest_uid = next(iter(_user_authenticators))
            del _user_authenticators[oldest_uid]

        _user_authenticators[uid] = authenticator
        _last_authenticated_uid = uid

    # Store UID and token data in ADK session state for cross-worker recovery
    if tool_context is not None and hasattr(tool_context, "state"):
        tool_context.state["user_id"] = uid

        # Serialize token data for cross-worker persistence
        if authenticator.uses_authorization_code:
            strategy = authenticator._strategy
            token = strategy._current_token or strategy._user_tokens.get(uid)
            if token is not None:
                _token_dict = {
                    "access_token": token.access_token,
                    "refresh_token": token.refresh_token,
                    "token_type": token.token_type,
                    "scope": token.scope,
                    "sap_user": token.sap_user,
                    "expires_at": token.expires_at.isoformat(),
                    "user_id": uid,
                }
                tool_context.state["sap_token_data"] = _token_dict
                logger.info(
                    "_store_authenticator: persisted token to session "
                    "state for uid=%s, sap_user=%s",
                    uid, token.sap_user,
                )

                # Persist to Secret Manager for cross-worker/cross-session recovery
                _save_token_to_secret(_token_dict)


def _cleanup_expired_authenticators() -> None:
    """Remove authenticators with expired tokens from the cache.

    Keeps authenticators that have a refresh_token (they can be renewed).
    """
    with _user_authenticators_lock:
        expired_uids = []
        for uid, auth in _user_authenticators.items():
            try:
                if hasattr(auth, '_strategy'):
                    strategy = auth._strategy
                    # Check _user_tokens first (SAPAuthorizationCodeStrategy)
                    if hasattr(strategy, '_user_tokens'):
                        user_token = strategy._user_tokens.get(uid)
                        if user_token is not None and user_token.is_expired:
                            # Keep if it has a refresh_token (can be renewed)
                            if user_token.refresh_token:
                                continue
                            expired_uids.append(uid)
                    elif hasattr(strategy, '_current_token'):
                        token = strategy._current_token
                        if token is not None and token.is_expired:
                            if hasattr(token, 'refresh_token') and token.refresh_token:
                                continue
                            expired_uids.append(uid)
            except Exception:
                pass

        for uid in expired_uids:
            del _user_authenticators[uid]

    if expired_uids:
        print(f"Cleaned up {len(expired_uids)} expired authenticator(s)")


def _parse_oauth_callback(raw_input: str) -> tuple:
    """Parse authorization code and state from raw user input.

    Users may paste various formats after SAP OAuth redirect:
    - "code=abc123&state=xyz789" (query string)
    - "https://redirect.url?code=abc123&state=xyz789" (full URL)
    - "https://redirect.url#code=abc123&state=xyz789" (fragment)
    - "abc123" (just the code)

    Returns:
        (authorization_code, oauth_state) tuple.
        If parsing fails, returns (raw_input, None).
    """
    from urllib.parse import urlparse, parse_qs

    if not raw_input or ("code=" not in raw_input or "state=" not in raw_input):
        return raw_input, None

    if "://" in raw_input:
        parsed = urlparse(raw_input)
        query = parsed.query or parsed.fragment
        params = parse_qs(query)
    else:
        params = parse_qs(raw_input)

    code = params.get("code", [None])[0]
    state = params.get("state", [None])[0]
    if code and state:
        return code, state

    return raw_input, None


# =============================================================================
# SAP Tools as Python Functions
# =============================================================================

# Default SAP host per environment (set via environment variables)
_LOCAL_SAP_HOST = os.getenv("SAP_HOST_LOCAL", "")      # External IP for local development
_DEPLOYED_SAP_HOST = os.getenv("SAP_HOST_DEPLOYED", "")  # Internal IP via PSC for Agent Engine


def _is_agent_engine() -> bool:
    """Detect if running inside Vertex AI Agent Engine."""
    return bool(
        os.getenv("CLOUD_ML_PROJECT_ID")
        or os.getenv("AIP_REASONING_ENGINE_ID")
    )


def _build_authenticator_from_adk_credential(
    uid: str,
    adk_credential: Any,
    tool_context: Optional[Any] = None,
) -> None:
    """Build a SAPAuthenticator from an ADK-provided OAuth credential.

    This bridges the ADK auth system with our existing SAPAuthenticator
    infrastructure, allowing the rest of the code (sap_query, sap_get_entity)
    to work unchanged.
    """
    from datetime import datetime, timedelta
    from sap_agent.sap_gw_connector.config.settings import get_config
    from sap_agent.sap_gw_connector.core.auth import (
        SAPAuthenticator,
        SAPUserToken,
    )

    config = get_config(require_sap=True)
    authenticator = SAPAuthenticator(config.sap)
    strategy = authenticator._strategy

    expires_in = getattr(adk_credential.oauth2, 'expires_in', None) or 3600
    token = SAPUserToken(
        access_token=adk_credential.oauth2.access_token,
        refresh_token=getattr(adk_credential.oauth2, 'refresh_token', None),
        token_type="Bearer",
        expires_at=datetime.utcnow() + timedelta(seconds=max(expires_in - 60, 60)),
    )

    strategy._cache_token(uid, token)
    strategy.set_current_user(uid)

    _store_authenticator(uid, authenticator, tool_context)
    logger.info(
        "_build_authenticator_from_adk_credential: cached for uid=%s", uid,
    )


def sap_authenticate(
    authorization_code: Optional[str] = None,
    oauth_state: Optional[str] = None,
    host: Optional[str] = None,
    port: int = 44300,
    client: str = "100",
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Authenticate with SAP Gateway using OAuth Authorization Code flow.

    Call this tool FIRST before querying SAP data.

    SAP OAuth Authorization Code flow for per-user SAP access:
      Step 1: Call without authorization_code → returns SAP login URL.
      Step 2: After user logs in, call again (the agent auto-detects the
              login via Cloud Run callback) or pass authorization_code and
              oauth_state → exchanges code for per-user SAP token.

    Args:
        authorization_code: SAP OAuth authorization code (for step 2)
        oauth_state: OAuth state parameter returned with the authorization code
        host: SAP Gateway host (optional, auto-detected)
        port: SAP Gateway port (default: 44300)
        client: SAP client number (default: '100')

    Returns:
        Dictionary with authentication status or login URL
    """
    try:
        # Load runtime-only secrets (e.g., redirect_uri) from Secret Manager
        _load_runtime_secrets()

        # Auto-detect host based on environment
        if not host:
            env_host = os.getenv("SAP_HOST")
            if env_host:
                host = env_host
            elif _is_agent_engine():
                host = _DEPLOYED_SAP_HOST
            else:
                host = _LOCAL_SAP_HOST

        os.environ["SAP_HOST"] = host
        os.environ["SAP_PORT"] = str(port)
        os.environ["SAP_CLIENT"] = client

        # Periodically clean up expired authenticators
        _cleanup_expired_authenticators()

        # SAP OAuth Authorization Code flow (per-user SAP login)
        logger.info(
            "sap_authenticate(sap_oauth): has_code=%s, has_state=%s, "
            "has_tool_context=%s, user_id will be resolved next",
            bool(authorization_code), bool(oauth_state),
            tool_context is not None,
        )
        required = [
            "SAP_OAUTH_CLIENT_ID",
            "SAP_OAUTH_CLIENT_SECRET",
            "SAP_OAUTH_TOKEN_URL",
            "SAP_OAUTH_AUTHORIZE_URL",
        ]
        missing = [v for v in required if not os.getenv(v)]
        if missing:
            return {
                "success": False,
                "error": (
                    "SAP OAuth configuration incomplete. "
                    f"Missing: {', '.join(missing)}"
                ),
            }

        # Determine user_id from invocation context or fallback
        _ctx_uid = _get_uid_from_context(tool_context)
        if _ctx_uid:
            user_id = _ctx_uid
            _uid_source = "invocation_context/session_state"
        elif _last_authenticated_uid:
            user_id = _last_authenticated_uid
            _uid_source = "last_authenticated_uid (in-memory fallback)"
        else:
            user_id = "default_user"
            _uid_source = "default_user (no identity available)"
        logger.info(
            "sap_authenticate: resolved user_id=%r (source=%s)",
            user_id, _uid_source,
        )

        # --- ADK Auth Flow (for Gemini Enterprise / AgentSpace) ---
        from sap_agent.sap_auth_config import build_sap_auth_config

        sap_auth_config = build_sap_auth_config()

        if sap_auth_config and tool_context is not None and hasattr(tool_context, "get_auth_response"):
            adk_credential = tool_context.get_auth_response(sap_auth_config)

            if adk_credential and getattr(adk_credential, 'oauth2', None) and getattr(adk_credential.oauth2, 'access_token', None):
                logger.info(
                    "sap_authenticate: using ADK credential (access_token present, "
                    "user_id=%s)", user_id,
                )
                _build_authenticator_from_adk_credential(
                    user_id, adk_credential, tool_context
                )
                return {
                    "success": True,
                    "message": f"Authenticated with SAP via ADK OAuth at {host}:{port}",
                    "host": host,
                    "port": port,
                    "client": client,
                    "auth_type": "sap_oauth_adk",
                    "user_id": user_id,
                }

            # No ADK credential available.
            # Gemini Enterprise does not support third-party OAuth consent
            # via adk_request_credential, so we fall through to the existing
            # custom OAuth flow that generates a login URL for the user.
            logger.info(
                "sap_authenticate: ADK get_auth_response returned None, "
                "falling through to custom OAuth flow"
            )

        # ADK auth summary log for deployment verification
        logger.info(
            "sap_authenticate: ADK auth result — "
            "has_auth_config=%s, has_tool_context_auth=%s, "
            "user_id=%s",
            sap_auth_config is not None,
            hasattr(tool_context, "get_auth_response") if tool_context else False,
            user_id,
        )

        # Parse raw OAuth callback input (e.g., "code=...&state=...")
        if authorization_code and not oauth_state:
            logger.info("Parsing raw OAuth callback: %.50s...", authorization_code)
            authorization_code, oauth_state = _parse_oauth_callback(
                authorization_code
            )
            logger.info(
                "Parsed result: has_code=%s, has_state=%s",
                bool(authorization_code), bool(oauth_state),
            )

        # Check for cached authenticator
        cached_auth = _get_authenticator_for_session(tool_context)
        logger.info(
            "Cached auth lookup: found=%s, uses_auth_code=%s, user_id=%s",
            cached_auth is not None,
            getattr(cached_auth, "uses_authorization_code", False),
            user_id,
        )
        if cached_auth is not None and cached_auth.uses_authorization_code:
            if cached_auth.has_valid_token_for_user(user_id):
                return {
                    "success": True,
                    "message": (
                        f"Already authenticated with SAP at "
                        f"{host}:{port} (client {client})"
                    ),
                    "host": host,
                    "port": port,
                    "client": client,
                    "auth_type": "sap_oauth",
                    "user_id": user_id,
                }

            # Token expired but has refresh_token → try auto-refresh
            # before falling through to Step 1 (new login URL).
            strategy = cached_auth._strategy
            expired_token = strategy.get_user_token(user_id)
            if expired_token is not None and expired_token.refresh_token:
                logger.info(
                    "Token expired but has refresh_token for uid=%s, "
                    "attempting auto-refresh",
                    user_id,
                )
                try:
                    async def _refresh():
                        return await strategy.refresh_user_token(user_id)

                    new_token = asyncio.get_event_loop().run_until_complete(
                        _refresh()
                    )
                    _store_authenticator(user_id, cached_auth, tool_context)
                    logger.info(
                        "Auto-refresh success: sap_user=%s, user_id=%s",
                        new_token.sap_user, user_id,
                    )
                    return {
                        "success": True,
                        "message": (
                            f"SAP token refreshed at "
                            f"{host}:{port} (client {client})"
                        ),
                        "host": host,
                        "port": port,
                        "client": client,
                        "auth_type": "sap_oauth",
                        "user_id": user_id,
                    }
                except Exception as e:
                    logger.warning(
                        "Auto-refresh failed for uid=%s: %s, "
                        "will generate new login URL",
                        user_id, e,
                    )

        if authorization_code and oauth_state:
            # Step 2: Exchange authorization code for token.
            # Use cached authenticator if available; otherwise create
            # a new one.  The PKCE code_verifier is derived
            # deterministically from the state parameter, so it can
            # be regenerated even if in-memory state was lost.
            if cached_auth is not None and cached_auth.uses_authorization_code:
                authenticator = cached_auth
            else:
                from sap_agent.sap_gw_connector.config import settings
                settings.config = None
                from sap_agent.sap_gw_connector.config.settings import get_config
                from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

                config = get_config(require_sap=True)
                authenticator = SAPAuthenticator(config.sap)

            logger.info(
                "Step 2 exchange start: state=%.8s..., "
                "redirect_uri=%s, using_cached_auth=%s",
                oauth_state or "",
                os.getenv("SAP_OAUTH_REDIRECT_URI", "(not set)"),
                cached_auth is not None and cached_auth.uses_authorization_code,
            )

            async def _exchange_code():
                return await authenticator.exchange_authorization_code(
                    authorization_code, oauth_state, user_id=user_id
                )

            token = asyncio.get_event_loop().run_until_complete(
                _exchange_code()
            )

            # Update stored authenticator
            _store_authenticator(user_id, authenticator, tool_context)

            # Cleanup pending secret if it came from Cloud Run
            _cleanup_pending_oauth_secret(oauth_state)

            logger.info(
                "Step 2 success: sap_user=%s, user_id=%s",
                token.sap_user, user_id,
            )
            return {
                "success": True,
                "message": (
                    f"SAP OAuth login successful at "
                    f"{host}:{port} (client {client})"
                ),
                "host": host,
                "port": port,
                "client": client,
                "auth_type": "sap_oauth",
                "sap_user": token.sap_user,
                "user_id": user_id,
            }
        else:
            # Step 1: Generate SAP login URL or auto-detect Cloud Run callback
            # Check if Cloud Run callback already captured the code
            pending = None
            if cached_auth is not None and cached_auth.uses_authorization_code:
                strategy = cached_auth._strategy
                if strategy._last_auth_info:
                    # Fast path: we know the exact state to look for
                    pending_state = strategy._last_auth_info.get("state")
                    if pending_state:
                        pending = _check_pending_oauth_code(pending_state)

            # Fast path via session state (survives cross-worker routing)
            if (
                pending is None
                and tool_context is not None
                and hasattr(tool_context, "state")
            ):
                saved_state = tool_context.state.get("sap_oauth_state")
                if saved_state:
                    pending = _check_pending_oauth_code(saved_state)
                    if pending:
                        logger.info(
                            "Found pending code via session-state "
                            "fast path (state=%.8s...)",
                            saved_state,
                        )

            if pending is None:
                # Slow path: search all pending secrets
                # (needed after container restart, cross-worker routing,
                # or new session where cached_auth is unavailable)
                logger.info("Auto-detect: scanning pending secrets")
                pending = _find_any_pending_oauth_code()

            if pending:
                logger.info(
                    "Auto-detected OAuth code from Cloud Run "
                    "callback (state=%.8s..., google_user=%s)",
                    pending.get("state", ""),
                    pending.get("google_user_email"),
                )
                authorization_code = pending["code"]
                oauth_state = pending["state"]
                # Use Google user email as user_id if available
                _google_email = pending.get("google_user_email")
                if _google_email:
                    user_id = _google_email
                    logger.info(
                        "Using google_user_email as user_id: %s",
                        user_id,
                    )
                # Fall through to Step 2 below

            if authorization_code and oauth_state:
                # Step 2 (auto-detected from Cloud Run)
                if cached_auth is not None and cached_auth.uses_authorization_code:
                    authenticator = cached_auth
                else:
                    from sap_agent.sap_gw_connector.config import settings
                    settings.config = None
                    from sap_agent.sap_gw_connector.config.settings import get_config
                    from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

                    config = get_config(require_sap=True)
                    authenticator = SAPAuthenticator(config.sap)

                logger.info(
                    "Step 2 exchange start (auto-detected): state=%.8s..., "
                    "redirect_uri=%s, user_id=%s",
                    oauth_state or "",
                    os.getenv("SAP_OAUTH_REDIRECT_URI", "(not set)"),
                    user_id,
                )

                async def _exchange_code():
                    return await authenticator.exchange_authorization_code(
                        authorization_code, oauth_state, user_id=user_id
                    )

                token = asyncio.get_event_loop().run_until_complete(
                    _exchange_code()
                )

                _store_authenticator(user_id, authenticator, tool_context)

                # Cleanup pending secret
                _cleanup_pending_oauth_secret(oauth_state)

                logger.info(
                    "Step 2 success (auto-detected): sap_user=%s, user_id=%s",
                    token.sap_user, user_id,
                )
                return {
                    "success": True,
                    "message": (
                        f"SAP OAuth login successful at "
                        f"{host}:{port} (client {client})"
                    ),
                    "host": host,
                    "port": port,
                    "client": client,
                    "auth_type": "sap_oauth",
                    "sap_user": token.sap_user,
                    "user_id": user_id,
                }

            # No code available — return login URL
            if cached_auth is not None and cached_auth.uses_authorization_code:
                strategy = cached_auth._strategy
                if strategy._pending_auth and strategy._last_auth_info:
                    logger.info(
                        "Step 1 reuse: returning cached auth URL "
                        "(state=%.8s...)",
                        strategy._last_auth_info.get("state", ""),
                    )
                    return {
                        "success": False,
                        "action_required": "sap_login",
                        "auth_url": strategy._last_auth_info["auth_url"],
                        "oauth_state": strategy._last_auth_info["state"],
                        "message": (
                            "SAP login required. Please open the following "
                            "URL in your browser to log in with your SAP "
                            "credentials. After login, you can return to "
                            "this chat — the agent will automatically "
                            "detect your login.\n\n"
                            f"Login URL: {strategy._last_auth_info['auth_url']}"
                        ),
                    }

            from sap_agent.sap_gw_connector.config import settings
            settings.config = None

            from sap_agent.sap_gw_connector.config.settings import get_config
            from sap_agent.sap_gw_connector.core.auth import SAPAuthenticator

            config = get_config(require_sap=True)
            authenticator = SAPAuthenticator(config.sap)

            auth_info = authenticator.generate_sap_auth_url(user_id)

            # Store authenticator so Step 2 can retrieve PKCE state
            _store_authenticator(user_id, authenticator, tool_context)

            # Persist OAuth state to session for cross-worker recovery
            if tool_context is not None and hasattr(tool_context, "state"):
                tool_context.state["sap_oauth_state"] = auth_info["state"]

            return {
                "success": False,
                "action_required": "sap_login",
                "auth_url": auth_info["auth_url"],
                "oauth_state": auth_info["state"],
                "message": (
                    "SAP login required. Please open the following URL "
                    "in your browser to log in with your SAP credentials. "
                    "After login, you can return to this chat — "
                    "the agent will automatically detect your login.\n\n"
                    f"Login URL: {auth_info['auth_url']}"
                ),
            }
    except Exception as e:
        logger.error("sap_authenticate failed: %s", str(e), exc_info=True)
        return {"success": False, "error": str(e)}


def sap_list_services() -> Dict[str, Any]:
    """List all available SAP OData services configured in services.yaml.

    Returns:
        Dictionary containing:
        - success: Boolean indicating operation success
        - count: Number of services found
        - services: List of service configurations with id, name, path, version, description, and entities
        - source: Configuration source identifier
    """
    try:
        from sap_agent.sap_gw_connector.config.loader import get_services_config

        config_path = get_services_config_path()
        services_config = get_services_config(config_path)

        # Build service list with details
        services = []
        for service in services_config.services:
            services.append(
                {
                    "id": service.id,
                    "name": service.name,
                    "path": service.path,
                    "version": service.version,
                    "description": service.description,
                    "entities": [
                        {
                            "name": entity.name,
                            "key_field": entity.key_field,
                            "description": entity.description,
                        }
                        for entity in service.entities
                    ],
                }
            )

        return {
            "success": True,
            "count": len(services),
            "services": services,
            "source": "services.yaml configuration",
        }

    except Exception as e:
        logger.error("sap_list_services failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


def sap_query(
    service: str,
    entity_set: str,
    filter: Optional[str] = None,
    select: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    format: str = "json_compact",
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Query SAP OData service entity sets with optional filters.

    Args:
        service: OData service name (e.g., 'sales_order')
        entity_set: Entity set name to query (e.g., 'zsd004Set')
        filter: OData filter expression (optional, e.g., "Status eq 'OPEN'")
        select: Comma-separated list of fields to select (optional)
        top: Maximum number of records to return (optional)
        skip: Number of records to skip for pagination (optional)
        format: Output format - 'json' for raw OData response, 'json_compact' removes metadata (default)

    Returns:
        Dictionary containing query results with 'results' array and 'count',
        or error information if the query fails
    """
    try:
        # Ensure SAP credentials are loaded from Secret Manager
        ensure_sap_config()

        from sap_agent.sap_gw_connector.config.settings import get_config
        from sap_agent.sap_gw_connector.config.loader import get_services_config
        from sap_agent.sap_gw_connector.core.sap_client import SAPClient

        # Get SAP connection configuration
        config = get_config(require_sap=True)
        sap_config = config.sap

        config_path = get_services_config_path()
        services_config = get_services_config(config_path)

        # Find service path
        service_info = services_config.get_service(service)
        if not service_info:
            available = services_config.list_service_ids()
            return {
                "success": False,
                "error": f"Service '{service}' not found. Available: {', '.join(available)}"
            }
        service_path = service_info.path

        # Build query parameters
        filters = {"$filter": filter} if filter else None
        select_fields = select.split(",") if select else None

        # Get per-user authenticator from session context
        client_kwargs: Dict[str, Any] = {"config": sap_config}
        authenticator = _get_authenticator_for_session(tool_context)
        logger.info(
            "sap_query: authenticator=%s, has_tool_context=%s, "
            "cache_size=%d, last_uid=%s",
            authenticator is not None,
            tool_context is not None,
            len(_user_authenticators),
            _last_authenticated_uid,
        )
        if authenticator is not None:
            client_kwargs["authenticator"] = authenticator
        else:
            # --- ADK Auth Fallback ---
            from sap_agent.sap_auth_config import build_sap_auth_config

            sap_auth_config = build_sap_auth_config()
            if sap_auth_config and tool_context is not None and hasattr(tool_context, "get_auth_response"):
                adk_cred = tool_context.get_auth_response(sap_auth_config)
                if adk_cred and getattr(adk_cred, 'oauth2', None) and getattr(adk_cred.oauth2, 'access_token', None):
                    uid = _get_uid_from_context(tool_context)
                    _build_authenticator_from_adk_credential(uid, adk_cred, tool_context)
                    authenticator = _get_authenticator_for_session(tool_context)
                    if authenticator is not None:
                        client_kwargs["authenticator"] = authenticator
                else:
                    logger.info("sap_query: ADK credential not available, user must authenticate first")
            if authenticator is None:
                logger.warning(
                    "sap_query: NO authenticator found! "
                    "User must call sap_authenticate first."
                )

        # Execute query using async wrapper
        async def _execute_query():
            async with SAPClient(**client_kwargs) as client:
                result = await client.query_entity_set(
                    service_path=service_path,
                    entity_set=entity_set,
                    filters=filters,
                    select_fields=select_fields,
                    top=top,
                    skip=skip,
                )
                return result

        # Run async function
        result = asyncio.get_event_loop().run_until_complete(_execute_query())

        # Re-persist authenticator to session state after successful query.
        # This captures any token refreshes that occurred during the query,
        # ensuring other worker processes get the latest token data.
        if tool_context is not None and authenticator is not None:
            uid = None
            if hasattr(tool_context, "state"):
                uid = tool_context.state.get("user_id")
            if uid is None:
                uid = _last_authenticated_uid
            if uid:
                _store_authenticator(uid, authenticator, tool_context)

        # Transform response based on format
        return _transform_response(result, format)

    except Exception as e:
        logger.error("sap_query failed: %s", e, exc_info=True)
        error_msg = str(e)
        # Provide actionable guidance for principal propagation token expiry
        if "Re-authentication required" in error_msg or "renewal failed" in error_msg:
            return {
                "success": False,
                "error": error_msg,
                "action_required": "re_authenticate",
                "message": (
                    "Your SAP session has expired. Please call the "
                    "sap_authenticate tool again to re-authenticate."
                ),
            }
        return {"success": False, "error": error_msg}


def sap_get_entity(
    service: str,
    entity_set: str,
    entity_key: str,
    select: Optional[str] = None,
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Retrieve a single entity from SAP OData service by key.

    Args:
        service: OData service name (e.g., 'sales_order')
        entity_set: Entity set name (e.g., 'zsd004Set')
        entity_key: Entity key value (e.g., '91000092' for OrderID)
        select: Comma-separated list of fields to select (optional)

    Returns:
        Dictionary containing:
        - success: Boolean indicating operation success
        - service: Service name used
        - entity_set: Entity set queried
        - entity_key: Key used for lookup
        - data: The entity data if found
        - error: Error message if operation failed
    """
    try:
        # Ensure SAP credentials are loaded from Secret Manager
        ensure_sap_config()

        from sap_agent.sap_gw_connector.config.settings import get_config
        from sap_agent.sap_gw_connector.config.loader import get_services_config
        from sap_agent.sap_gw_connector.core.sap_client import SAPClient

        config = get_config(require_sap=True)

        config_path = get_services_config_path()
        services_config = get_services_config(config_path)

        # Validate service exists
        service_config = services_config.get_service(service)
        if not service_config:
            available_services = services_config.list_service_ids()
            return {
                "success": False,
                "error": f"Service '{service}' not found. Available: {', '.join(available_services)}",
            }

        # Validate entity exists in service
        entity_config = service_config.get_entity(entity_set)
        if not entity_config:
            available_entities = [e.name for e in service_config.entities]
            return {
                "success": False,
                "error": f"Entity set '{entity_set}' not found in service '{service}'. "
                         f"Available: {', '.join(available_entities)}",
            }

        # Use service path from configuration
        service_path = service_config.path

        # Parse select fields if provided
        select_fields = None
        if select:
            select_fields = [f.strip() for f in select.split(",")]

        # Get per-user authenticator from session context
        client_kwargs: Dict[str, Any] = {"config": config.sap}
        authenticator = _get_authenticator_for_session(tool_context)
        if authenticator is not None:
            client_kwargs["authenticator"] = authenticator
        else:
            # --- ADK Auth Fallback ---
            from sap_agent.sap_auth_config import build_sap_auth_config

            sap_auth_config = build_sap_auth_config()
            if sap_auth_config and tool_context is not None and hasattr(tool_context, "get_auth_response"):
                adk_cred = tool_context.get_auth_response(sap_auth_config)
                if adk_cred and getattr(adk_cred, 'oauth2', None) and getattr(adk_cred.oauth2, 'access_token', None):
                    uid = _get_uid_from_context(tool_context)
                    _build_authenticator_from_adk_credential(uid, adk_cred, tool_context)
                    authenticator = _get_authenticator_for_session(tool_context)
                    if authenticator is not None:
                        client_kwargs["authenticator"] = authenticator
                else:
                    logger.info("sap_get_entity: ADK credential not available, user must authenticate first")

        async def _execute_get():
            async with SAPClient(**client_kwargs) as client:
                # Authenticate first (skipped if authenticator already has valid token)
                auth_success = await client.authenticate()
                if not auth_success:
                    return {"success": False, "error": "Authentication failed"}

                # Get entity by key
                result = await client.get_entity(
                    service_path=service_path,
                    entity_set=entity_set,
                    entity_key=entity_key,
                    select_fields=select_fields,
                )

                return {
                    "success": True,
                    "service": service,
                    "entity_set": entity_set,
                    "entity_key": entity_key,
                    "key_field": entity_config.key_field,
                    "data": result,
                }

        # Run async function
        result = asyncio.get_event_loop().run_until_complete(_execute_get())

        # Re-persist authenticator to capture any token refreshes
        if tool_context is not None and authenticator is not None:
            uid = None
            if hasattr(tool_context, "state"):
                uid = tool_context.state.get("user_id")
            if uid is None:
                uid = _last_authenticated_uid
            if uid:
                _store_authenticator(uid, authenticator, tool_context)

        return result

    except Exception as e:
        logger.error("sap_get_entity failed: %s", e, exc_info=True)
        error_msg = str(e)
        if "Re-authentication required" in error_msg or "renewal failed" in error_msg:
            return {
                "success": False,
                "error": error_msg,
                "action_required": "re_authenticate",
                "message": (
                    "Your SAP session has expired. Please call the "
                    "sap_authenticate tool again to re-authenticate."
                ),
            }
        return {"success": False, "error": error_msg}


# =============================================================================
# Agent Instruction
# =============================================================================

AGENT_INSTRUCTION = '''You are an SAP integration assistant that helps users query and interact with SAP systems through OData services.

## Your Capabilities
You have access to SAP OData tools that enable integration. Supported authentication methods:
- SAP OAuth 2.0 Authorization Code (per-user, interactive login)

Tools available:
- sap_authenticate: Authenticate with SAP (call FIRST)
- sap_query: Query SAP data via OData services
- sap_list_services: List available SAP entity sets and services
- sap_get_entity: Retrieve a specific entity by key

## Authentication Flow
1. **IMPORTANT**: Before any SAP data operation, you MUST call sap_authenticate first.
2. When you need SAP data, call sap_authenticate or any SAP tool.
3. If the user hasn't authenticated yet:
   - In Gemini Enterprise: The system will prompt the user to authorize SAP access.
     Wait for authorization to complete, then retry.
   - In other environments: A SAP login URL will be provided for manual login.
4. After successful authentication, proceed with SAP queries.
5. If you receive action_required="adk_oauth", tell the user:
   "Please complete the SAP authorization prompt to continue."
6. If you receive action_required="sap_login", present the login URL as a clickable link.
7. Do NOT display passwords, tokens, or authorization codes back to the user.
8. The user's SAP permissions (PFCG roles) are automatically applied to all subsequent queries.
9. Sessions are maintained via refresh tokens — the user only needs to log in once.

## Guidelines
1. When a user asks about SAP data, first ensure they are authenticated
2. Use sap_list_services to discover available services and their entities
3. Use sap_query for searching/filtering multiple records
4. Use sap_get_entity for retrieving a specific record by its key
5. Present data in a clear, formatted manner

## Response Format
- Always explain what data you're retrieving before executing queries
- Format response data in readable tables or structured lists
- Summarize key findings after presenting raw data
- Offer follow-up suggestions for related queries

## Error Handling
- If SAP session expired, tell the user to re-authenticate via sap_authenticate
- If action_required="sap_login", present the login URL to the user
- If action_required="re_authenticate", ask the user to log in again
- If a service is not found, use sap_list_services to show available services
- If a query returns no data, suggest alternative filters or entities
'''


# =============================================================================
# Root Agent Definition
# =============================================================================

root_agent = Agent(
    model=MODEL,  # GlobalGemini instance with forced global endpoint
    name='sap_agent',
    version='1.0.0',
    description='SAP Gateway integration agent for OData queries and operations',
    instruction=AGENT_INSTRUCTION,
    tools=[
        sap_authenticate,
        sap_list_services,
        sap_query,
        sap_get_entity,
    ],
)


# =============================================================================
# Utility Functions for Deployment
# =============================================================================

def get_agent_config() -> dict:
    """Get current agent configuration for debugging/logging."""
    return {
        "model": MODEL_NAME,
        "model_class": "GlobalGemini (forced global endpoint)",
        "tools": ["sap_authenticate", "sap_list_services", "sap_query", "sap_get_entity"],
        "deployment_mode": "direct_functions",
    }


if __name__ == "__main__":
    # Print configuration for debugging
    config = get_agent_config()
    print("SAP Agent Configuration:")
    for key, value in config.items():
        print(f"  {key}: {value}")
