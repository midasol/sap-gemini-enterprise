# SAP OAuth First-Attempt Authentication Failure Fix

## Problem

When using SAP OAuth Authorization Code flow (`SAP_AUTH_TYPE=sap_oauth`), authentication **always fails on the first attempt** but succeeds on the second. This is a consistent pattern observed across deployments, with varying behavior across different browsers.

## Root Cause Analysis

### Cause 1: Agent Instruction Ambiguity

The current `AGENT_INSTRUCTION` contains:

```
1. IMPORTANT: Before any SAP data operation, you MUST call sap_authenticate first.
2. Call sap_authenticate WITHOUT any credential arguments first.
```

The LLM (Gemini) interprets instruction #2 literally — calling `sap_authenticate()` without arguments **every time**, even after the user has already provided an authorization code. This re-triggers Step 1, which:

- Creates a new `SAPAuthenticator` with a new `SAPAuthorizationCodeStrategy`
- Generates a new auth URL with a new `state`
- Replaces the cached authenticator (and its `_pending_auth` dict) in `_user_authenticators`
- The previous authorization code becomes unusable if SAP invalidates old sessions

### Cause 2: ADK Session State Instability

`_get_authenticator_for_session()` depends on `tool_context.state["firebase_uid"]` to retrieve the cached authenticator. In Agent Engine, session state persistence varies by browser/client. When state is lost:

- `cached_auth` returns `None`
- A new authenticator is created in Step 2 (code exchange)
- The new authenticator has an empty `_pending_auth` dict
- Falls back to deterministic PKCE (which should work), but `redirect_uri` or other config differences may cause `invalid_grant`

### Cause 3: Repeated Config Reset

`settings.config = None` is called in:
- `sap_authenticate()` Step 1 (line 678)
- `sap_authenticate()` Step 2 fallback (line 643)
- `ensure_sap_config()` called by `sap_query`/`sap_get_entity`

Each reset forces a full config reload from env vars, which should be consistent but adds unnecessary risk.

## Design

### Fix 1: Raw Input Parsing (`agent.py`)

Add a helper function to parse various user input formats, and call it early in the `sap_oauth` branch of `sap_authenticate()`.

```python
def _parse_oauth_callback(raw_input: str) -> tuple:
    """Parse code and state from various user input formats.

    Handles:
    - "code=abc123&state=xyz789" (query string)
    - "https://redirect.url?code=abc123&state=xyz789" (full URL)
    - "https://redirect.url#code=abc123&state=xyz789" (fragment URL)
    """
    from urllib.parse import urlparse, parse_qs

    if "code=" in raw_input and "state=" in raw_input:
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
```

Usage in `sap_authenticate()`:

```python
if authorization_code and not oauth_state:
    authorization_code, oauth_state = _parse_oauth_callback(authorization_code)
```

### Fix 2: Prevent Step 1 Re-trigger (`agent.py` + `auth.py`)

**In `SAPAuthorizationCodeStrategy`** — cache the last generated auth info:

```python
def __init__(self, config):
    ...
    self._last_auth_info: Optional[Dict[str, str]] = None

def generate_auth_url(self, user_id):
    ...
    self._last_auth_info = {"auth_url": auth_url, "state": state}
    return self._last_auth_info
```

**In `sap_authenticate()`** — reuse existing pending auth instead of generating new:

```python
# In the else branch (Step 1: Generate URL)
if cached_auth is not None and cached_auth.uses_authorization_code:
    strategy = cached_auth._strategy
    if strategy._pending_auth and strategy._last_auth_info:
        # Reuse existing auth URL — don't invalidate previous session
        logger.info("Reusing existing pending auth URL")
        return {
            "success": False,
            "action_required": "sap_login",
            "auth_url": strategy._last_auth_info["auth_url"],
            "oauth_state": strategy._last_auth_info["state"],
            "message": "...",
        }

# Only create new authenticator if no pending auth exists
settings.config = None
config = get_config(require_sap=True)
authenticator = SAPAuthenticator(config.sap)
...
```

### Fix 3: Agent Instruction Improvement (`agent.py`)

Replace the ambiguous authentication flow section in `AGENT_INSTRUCTION`:

```
## Authentication Flow
1. **IMPORTANT**: Before any SAP data operation, you MUST call sap_authenticate first.

### SAP OAuth Authorization Code (SAP_AUTH_TYPE=sap_oauth)
This mode lets each user log in directly with their own SAP account:

- **Step 1 (one-time only)**: Call sap_authenticate with NO arguments.
  → Returns action_required="sap_login" with auth_url and oauth_state.
  → Present the URL as a clickable link: [SAP Login](AUTH_URL).
  → Tell the user to open it in a new tab and log in with SAP credentials.
  → After logging in, the browser redirects. Ask the user to copy the
    full URL or code=...&state=... from the redirected URL and paste it here.

- **Step 2**: When the user pastes the redirect URL or code=...&state=... string:
  → Parse the code and state values from the user's input.
  → Call sap_authenticate with authorization_code=<code> and oauth_state=<state>.
  → If the user pastes code=...&state=... as a single string, you may pass
    the entire string as authorization_code — the tool will parse it automatically.

- **CRITICAL**: Do NOT call sap_authenticate() without arguments again after
  Step 1 is complete. Once you have the auth_url, wait for the user to provide
  the authorization code. Calling without arguments will invalidate the
  previous login session.

- The user's SAP permissions (PFCG roles) are automatically applied to all subsequent queries.
- Sessions are maintained via refresh tokens — the user only needs to log in once.
```

### Fix 4: Debug Logging (`agent.py`)

Add `logging` calls at 6 critical points in `sap_authenticate()`:

1. **Function entry**: auth_type, has_code, has_state, has_tool_context, user_id
2. **Raw input parsing**: before/after parsing result
3. **Cached auth lookup**: found, uses_auth_code, has_pending
4. **Step 1 reuse**: when existing pending auth is reused
5. **Step 2 exchange**: state prefix, redirect_uri used
6. **Failure**: exception type and message

Use `logger.info()` / `logger.error()` instead of `print()` for Cloud Logging compatibility.

## Files to Modify

| File | Changes |
|------|---------|
| `sap_agent/agent.py` | Add `_parse_oauth_callback()`, modify `sap_authenticate()` Step 1 reuse logic, update `AGENT_INSTRUCTION`, add logging |
| `sap_agent/sap_gw_connector/core/auth.py` | Add `_last_auth_info` field to `SAPAuthorizationCodeStrategy` |

## Testing

1. **First-attempt authentication**: Deploy to Agent Engine, verify first code exchange succeeds
2. **Raw input formats**: Test with `code=...&state=...`, full URL, and properly parsed parameters
3. **Step 1 re-trigger**: Verify that calling `sap_authenticate()` without args after Step 1 returns the same auth URL
4. **Multi-browser**: Test across Chrome, Firefox, Safari to verify consistent behavior
5. **Session restart**: Verify deterministic PKCE still works when in-memory state is lost
