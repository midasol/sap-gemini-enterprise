# Testing Guide

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_auth.py

# Run a specific test class or method
pytest tests/test_sap_oauth.py::TestSAPOAuthConfig
pytest tests/test_auth.py::TestSAPAuthenticator::test_generate_auth_url
```

### Prerequisites

Install dev dependencies:

```bash
pip install -e ".[dev]"
# or with uv
uv sync --group dev
```

### Configuration

Test configuration is in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = "."
asyncio_mode = "auto"                        # Auto-detect async tests
asyncio_default_fixture_loop_scope = "function"
```

## Test Structure

```
tests/
  conftest.py              # Shared fixtures (clean_env, sap_oauth_env, sap_oauth_config)
  test_auth.py             # SAPUserToken, SAPAuthenticator, SAPAuthorizationCodeStrategy
  test_config.py           # SAPConnectionConfig validation, env var integration
  test_sap_oauth.py        # Full OAuth flow: unit, integration, E2E tests
  test_sap_auth_config.py  # ADK AuthConfig builder (build_sap_auth_config)
  test_adk_auth_flow.py    # ADK auth integration in agent tool functions
  test_integration.py      # Cross-module integration tests
```

## Test Levels

### Unit Tests (no network)

Test code logic in isolation with mocks. All tests in `test_auth.py`, `test_config.py`, and `test_sap_auth_config.py` are unit tests.

Examples:
- Token validity/expiration checks
- Config validation (valid/invalid inputs)
- Auth URL generation with PKCE
- Header construction

### Integration Tests (mocked SAP)

Test multi-component flows with mocked HTTP responses. Found in `test_sap_oauth.py` and `test_integration.py`.

Examples:
- Full OAuth code exchange with mock SAP token endpoint
- Auth flow through SAPClient with mocked HTTP
- Token refresh with mock responses
- Error handling (invalid_grant, invalid_client)

### E2E Tests (real SAP, skipped by default)

End-to-end tests against a real SAP system. Located in `test_sap_oauth.py`, skipped unless `SAP_E2E_TEST=1`:

```bash
SAP_E2E_TEST=1 pytest tests/test_sap_oauth.py -k "e2e" -v
```

## Key Fixtures (`conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `clean_env` | autouse | Removes all `SAP_*` env vars before each test |
| `sap_oauth_env` | function | Sets up env vars for SAP OAuth testing |
| `sap_oauth_config` | function | Creates a `SAPConnectionConfig` instance for testing |

## What the Tests Cover

| Area | Files | Coverage |
|------|-------|----------|
| Token lifecycle | `test_auth.py` | Valid/expired/empty tokens, cookies |
| Config validation | `test_config.py` | Required fields, defaults, env vars, invalid inputs |
| OAuth PKCE flow | `test_sap_oauth.py` | URL generation, code exchange, token refresh, error handling |
| ADK AuthConfig | `test_sap_auth_config.py` | Builder with/without env vars |
| ADK integration | `test_adk_auth_flow.py` | Tool functions with/without ADK credentials |
| Cross-module | `test_integration.py` | Config→Auth→Strategy chain, error hierarchy |

## Writing New Tests

1. Use the `sap_oauth_config` fixture for tests needing a config object
2. Use `monkeypatch.setenv()` for env var tests (auto-cleaned by `clean_env`)
3. Mark async tests with `@pytest.mark.asyncio` (auto-detected via `asyncio_mode = "auto"`)
4. Mock HTTP calls with `unittest.mock.AsyncMock` and `aiohttp` response mocks
5. Place fixtures in `conftest.py` if shared across files
