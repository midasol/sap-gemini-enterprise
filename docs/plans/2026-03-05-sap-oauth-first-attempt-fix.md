# SAP OAuth First-Attempt Auth Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the bug where SAP OAuth authentication always fails on the first attempt but succeeds on retry.

**Architecture:** Four defensive fixes — raw input parsing, Step 1 re-trigger prevention, clearer agent instruction, and debug logging — applied to `agent.py` and `auth.py`.

**Tech Stack:** Python 3.11+, pydantic-settings, aiohttp, pytest, Google ADK

---

### Task 1: Add `_parse_oauth_callback` helper and tests

**Files:**
- Modify: `sap_agent/agent.py` (add helper function near line 500)
- Test: `tests/test_sap_oauth.py` (add new test class)

**Step 1: Write the failing tests**

Add to the end of `tests/test_sap_oauth.py` (before the E2E test class):

```python
class TestParseOAuthCallback:
    """_parse_oauth_callback parses raw user input into (code, state)."""

    def test_query_string_format(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "code=abc123&state=xyz789"
        )
        assert code == "abc123"
        assert state == "xyz789"

    def test_full_url_format(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "https://example.com/callback?code=abc123&state=xyz789"
        )
        assert code == "abc123"
        assert state == "xyz789"

    def test_fragment_url_format(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "https://example.com/callback#code=abc123&state=xyz789"
        )
        assert code == "abc123"
        assert state == "xyz789"

    def test_plain_code_returns_as_is(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback("just-a-plain-code")
        assert code == "just-a-plain-code"
        assert state is None

    def test_url_encoded_values(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback(
            "code=t2lqsT9hH9GGiVjKIW58pWvI_Mrht6UMwqTwy_A4F5laNYHe"
            "&state=y1_z_OOWhZ9Zwhp2od_nPz0HIlpGhoam9oBRw5YfIQE"
        )
        assert code == "t2lqsT9hH9GGiVjKIW58pWvI_Mrht6UMwqTwy_A4F5laNYHe"
        assert state == "y1_z_OOWhZ9Zwhp2od_nPz0HIlpGhoam9oBRw5YfIQE"

    def test_empty_string(self):
        from sap_agent.agent import _parse_oauth_callback

        code, state = _parse_oauth_callback("")
        assert code == ""
        assert state is None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sap_oauth.py::TestParseOAuthCallback -v`
Expected: FAIL with `ImportError: cannot import name '_parse_oauth_callback'`

**Step 3: Write the implementation**

Add to `sap_agent/agent.py` after the `_cleanup_expired_authenticators` function (around line 502):

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sap_oauth.py::TestParseOAuthCallback -v`
Expected: All 6 tests PASS

**Step 5: Wire up the parser in `sap_authenticate`**

In `sap_agent/agent.py`, inside `sap_authenticate()`, right after the `user_id` determination block (after line 614), add:

```python
            # Parse raw OAuth callback input (e.g., "code=...&state=...")
            if authorization_code and not oauth_state:
                logger.info(
                    "Parsing raw OAuth callback: %.50s...",
                    authorization_code,
                )
                authorization_code, oauth_state = _parse_oauth_callback(
                    authorization_code
                )
                logger.info(
                    "Parsed result: has_code=%s, has_state=%s",
                    bool(authorization_code), bool(oauth_state),
                )
```

**Step 6: Commit**

```bash
git add sap_agent/agent.py tests/test_sap_oauth.py
git commit -m "feat: add raw OAuth callback input parsing in sap_authenticate"
```

---

### Task 2: Add `_last_auth_info` to `SAPAuthorizationCodeStrategy`

**Files:**
- Modify: `sap_agent/sap_gw_connector/core/auth.py:1198-1206` (`__init__`) and `auth.py:1231-1281` (`generate_auth_url`)
- Test: `tests/test_sap_oauth.py`

**Step 1: Write the failing test**

Add to `tests/test_sap_oauth.py` inside `TestSAPAuthorizationCodeStrategy`:

```python
    def test_generate_auth_url_caches_last_info(self, sap_oauth_config):
        """generate_auth_url stores result in _last_auth_info."""
        from sap_agent.sap_gw_connector.core.auth import SAPAuthorizationCodeStrategy

        strategy = SAPAuthorizationCodeStrategy(sap_oauth_config)
        assert strategy._last_auth_info is None

        result = strategy.generate_auth_url("user-123")

        assert strategy._last_auth_info is not None
        assert strategy._last_auth_info["auth_url"] == result["auth_url"]
        assert strategy._last_auth_info["state"] == result["state"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sap_oauth.py::TestSAPAuthorizationCodeStrategy::test_generate_auth_url_caches_last_info -v`
Expected: FAIL with `AttributeError: 'SAPAuthorizationCodeStrategy' object has no attribute '_last_auth_info'`

**Step 3: Write the implementation**

In `sap_agent/sap_gw_connector/core/auth.py`:

1. In `__init__` (line 1198-1206), add after `self._auth_lock`:

```python
        self._last_auth_info: Optional[Dict[str, str]] = None
```

2. In `generate_auth_url` (around line 1276, before the return), add:

```python
        self._last_auth_info = {"auth_url": auth_url, "state": state}
```

And change the return to:

```python
        return self._last_auth_info
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_sap_oauth.py::TestSAPAuthorizationCodeStrategy -v`
Expected: All tests PASS (including existing ones)

**Step 5: Commit**

```bash
git add sap_agent/sap_gw_connector/core/auth.py tests/test_sap_oauth.py
git commit -m "feat: cache last auth URL in SAPAuthorizationCodeStrategy"
```

---

### Task 3: Prevent Step 1 re-trigger in `sap_authenticate`

**Files:**
- Modify: `sap_agent/agent.py:675-708` (Step 1 else branch)
- Test: `tests/test_sap_oauth.py`

**Step 1: Write the failing test**

Add new test class to `tests/test_sap_oauth.py`:

```python
class TestStep1ReuseExistingAuth:
    """sap_authenticate reuses pending auth URL instead of generating new."""

    def test_step1_reuse_returns_same_url(self, sap_oauth_env):
        """Calling sap_authenticate() twice without code returns same auth URL."""
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import sap_authenticate

        # First call: generates auth URL
        result1 = sap_authenticate()
        assert result1["action_required"] == "sap_login"
        url1 = result1["auth_url"]
        state1 = result1["oauth_state"]

        # Second call: should return the SAME URL (not generate new)
        result2 = sap_authenticate()
        assert result2["action_required"] == "sap_login"
        assert result2["auth_url"] == url1
        assert result2["oauth_state"] == state1

    def test_step1_reuse_then_exchange_succeeds(self, sap_oauth_env):
        """After reuse, code exchange with original state still works."""
        from sap_agent.sap_gw_connector.config import settings
        settings.config = None

        from sap_agent.agent import sap_authenticate

        # Step 1: generate auth URL
        result1 = sap_authenticate()
        state = result1["oauth_state"]

        # Step 1 again (re-triggered by LLM): should reuse
        result2 = sap_authenticate()
        assert result2["oauth_state"] == state

        # Step 2: exchange code with original state
        mock_resp = _mock_token_response(sap_user="REUSE_USER")
        mock_sess = _mock_session(mock_resp)

        with patch("aiohttp.ClientSession", return_value=mock_sess):
            result3 = sap_authenticate(
                authorization_code="test-code",
                oauth_state=state,
            )

        assert result3["success"] is True
        assert result3["sap_user"] == "REUSE_USER"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sap_oauth.py::TestStep1ReuseExistingAuth -v`
Expected: FAIL — `test_step1_reuse_returns_same_url` fails because second call generates a new URL

**Step 3: Write the implementation**

In `sap_agent/agent.py`, replace the `else` block at line 675-708 with:

```python
            else:
                # Step 1: Generate SAP login URL
                # If a cached authenticator already has a pending auth URL,
                # reuse it instead of generating a new one. This prevents
                # invalidating the previous login session when the LLM
                # re-triggers Step 1.
                if cached_auth is not None and cached_auth.uses_authorization_code:
                    strategy = cached_auth._strategy
                    if strategy._pending_auth and strategy._last_auth_info:
                        logger.info(
                            "Reusing existing pending auth URL (state=%s...)",
                            strategy._last_auth_info["state"][:16],
                        )
                        return {
                            "success": False,
                            "action_required": "sap_login",
                            "auth_url": strategy._last_auth_info["auth_url"],
                            "oauth_state": strategy._last_auth_info["state"],
                            "message": (
                                "SAP login required. Please open the following "
                                "URL in your browser to log in with your SAP "
                                "credentials. After login, copy the full URL "
                                "or code=...&state=... from the redirected URL "
                                "and paste it here.\n\n"
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

                return {
                    "success": False,
                    "action_required": "sap_login",
                    "auth_url": auth_info["auth_url"],
                    "oauth_state": auth_info["state"],
                    "message": (
                        "SAP login required. Please open the following URL "
                        "in your browser to log in with your SAP credentials. "
                        "After login, copy the full URL or "
                        "code=...&state=... from the redirected URL "
                        "and paste it here.\n\n"
                        f"Login URL: {auth_info['auth_url']}"
                    ),
                }
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sap_oauth.py::TestStep1ReuseExistingAuth -v`
Expected: Both tests PASS

**Step 5: Run all SAP OAuth tests to check for regressions**

Run: `pytest tests/test_sap_oauth.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add sap_agent/agent.py tests/test_sap_oauth.py
git commit -m "fix: prevent Step 1 re-trigger from invalidating pending OAuth state"
```

---

### Task 4: Update `AGENT_INSTRUCTION`

**Files:**
- Modify: `sap_agent/agent.py:1178-1191` (AGENT_INSTRUCTION string)

**Step 1: Replace the Authentication Flow section**

In `sap_agent/agent.py`, replace lines 1178-1191 of `AGENT_INSTRUCTION`:

```python
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
  the authorization code. Calling without arguments again will invalidate the
  previous login session.

- The user's SAP permissions (PFCG roles) are automatically applied to all subsequent queries.
- Sessions are maintained via refresh tokens — the user only needs to log in once.
```

**Step 2: Verify no syntax errors**

Run: `python -c "from sap_agent.agent import AGENT_INSTRUCTION; print('OK:', len(AGENT_INSTRUCTION))"`
Expected: `OK: <number>` (no import errors)

**Step 3: Commit**

```bash
git add sap_agent/agent.py
git commit -m "fix: clarify agent instruction to prevent Step 1 re-trigger"
```

---

### Task 5: Add debug logging to `sap_authenticate`

**Files:**
- Modify: `sap_agent/agent.py` (add `import logging` and logger calls inside `sap_authenticate`)

**Step 1: Add logger setup**

At the top of `sap_agent/agent.py`, add after the existing imports (around line 15):

```python
import logging

logger = logging.getLogger(__name__)
```

**Step 2: Add logging at 6 critical points in `sap_authenticate`**

1. **Function entry** (after line 585, start of `sap_oauth` branch):

```python
            logger.info(
                "sap_authenticate[sap_oauth]: has_code=%s, has_state=%s, "
                "has_tool_context=%s, user_id=%s",
                bool(authorization_code), bool(oauth_state),
                tool_context is not None, user_id,
            )
```

2. **Cached auth lookup** (after line 617):

```python
            logger.info(
                "sap_authenticate: cached_auth=%s, uses_auth_code=%s",
                cached_auth is not None,
                getattr(cached_auth, 'uses_authorization_code', False),
            )
```

3. **Step 1 reuse** — already added in Task 3.

4. **Step 2 exchange start** (after line 639, when authenticator is selected):

```python
                logger.info(
                    "sap_authenticate: exchanging code, state=%.16s..., "
                    "redirect_uri=%s, from_cache=%s",
                    oauth_state or "",
                    authenticator._strategy.config.oauth_redirect_uri or "(empty)",
                    authenticator is cached_auth,
                )
```

5. **Step 2 success** (after line 660):

```python
                logger.info(
                    "sap_authenticate: exchange successful, sap_user=%s",
                    token.sap_user,
                )
```

6. **Failure** (in the `except Exception` block at line 903):

```python
        logger.error("sap_authenticate failed: %s", str(e), exc_info=True)
```

**Step 3: Remove old `print()` debug statements**

Replace `print(f"Debug ensure_sap_config: ...")` at line 246-247 with `logger.debug(...)`.

**Step 4: Run all tests**

Run: `pytest tests/test_sap_oauth.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add sap_agent/agent.py
git commit -m "feat: add debug logging to sap_authenticate for Cloud Logging"
```

---

### Task 6: Final integration test and full regression check

**Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Verify import works**

Run: `python -c "from sap_agent.agent import sap_authenticate, _parse_oauth_callback, AGENT_INSTRUCTION; print('All imports OK')"`
Expected: `All imports OK`

**Step 3: Final commit (if any adjustments needed)**

```bash
git add -A
git commit -m "fix: SAP OAuth first-attempt authentication failure

- Add raw input parsing for code=...&state=... strings
- Prevent Step 1 re-trigger from invalidating pending auth
- Clarify agent instruction with CRITICAL warning
- Add debug logging at 6 points for Cloud Logging diagnostics"
```
