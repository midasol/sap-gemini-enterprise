# Architecture

This document describes the module structure, data flow, and component relationships of the SAP ADK Agent.

## High-Level Overview

The SAP ADK Agent is an AI agent built with [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) that queries SAP Gateway OData services via natural language. It deploys on **Vertex AI Agent Engine** and connects to on-premise SAP systems through **Private Service Connect (PSC)**.

```
User (Chat UI)
    |
    v
Vertex AI Agent Engine (Gemini LLM)
    |
    v
sap_agent/agent.py  (ADK root_agent + tool functions)
    |
    v
sap_gw_connector/   (SAP Gateway HTTP client layer)
    |  (via PSC network attachment)
    v
SAP Gateway (OData v2/v4 services)
```

## Module Structure

```
sap_agent/
  agent.py                  # Main agent: root_agent definition, tool functions
  sap_auth_config.py        # Builds ADK AuthConfig for OAuth flows
  .env                      # Environment variable template
  services.yaml             # SAP OData service definitions
  sap_gw_connector/
    core/
      auth.py               # Auth strategies (SAPAuthorizationCodeStrategy)
      sap_client.py          # HTTP client for SAP OData requests
      exceptions.py          # Custom exception hierarchy
    config/
      settings.py            # Pydantic config models (SAPConnectionConfig, etc.)
      schemas.py             # Pydantic models for services.yaml
      loader.py              # YAML config loader
    tools/
      base.py                # SAPTool ABC + ToolRegistry
      auth_tool.py           # sap_authenticate tool
      query_tool.py          # sap_query tool
      entity_tool.py         # sap_get_entity tool
      service_tool.py        # sap_list_services tool
    utils/
      validators.py          # Input validation helpers
      logger.py              # Structured logging (structlog)
    protocol/
      schemas.py             # JSON-RPC protocol schemas

cloud-run-oauth-callback/   # Separate Cloud Run service for OAuth redirects
  main.py                   # Flask app: /callback, /identify, /health
  Dockerfile
  requirements.txt

scripts/
  deploy_agent_engine.py    # Deploy to Vertex AI Agent Engine
  cleanup_agent_engines.py  # Delete all Agent Engines
  setup_gcp_prerequisites.sh # GCP APIs, service accounts, IAM
  setup_psc_infrastructure.sh # PSC subnet, network attachment, firewall
  test_*.py                 # Various remote testing scripts

tests/                      # pytest test suite
```

## Key Components

### 1. Agent Layer (`sap_agent/agent.py`)

The main module defines:

- **`root_agent`**: An ADK `LlmAgent` powered by Gemini, with tool functions registered
- **Tool functions** exposed to the LLM:
  - `sap_authenticate` — Initiates OAuth login, exchanges codes, manages per-user tokens
  - `sap_query` — Queries OData entity sets with filters, pagination, field selection
  - `sap_get_entity` — Retrieves a single entity by key
  - `sap_list_services` — Lists configured SAP OData services from `services.yaml`
- **Per-user auth management**: Thread-safe cache of `SAPAuthenticator` instances keyed by session-based UID or real user identity. The generic `default-user-id` from Agent Engine is never used as a cache key — instead, session-based UIDs (`session-{session_id}`) provide unique per-session identity, upgraded to real email after OAuth.
- **Secret Manager integration**: Loads SAP credentials and polls for pending OAuth codes at runtime. Supports cross-session token recovery by scanning existing `sap-oauth-token-*` secrets.

### 2. Authentication (`core/auth.py`)

Uses the **Strategy Pattern** with a single concrete strategy:

- **`SAPAuthorizationCodeStrategy`**: OAuth 2.0 Authorization Code with PKCE
  - Generates authorization URLs with PKCE code challenges
  - Derives code verifier deterministically from state (survives container restarts)
  - Exchanges authorization codes for per-user SAP access tokens
  - Auto-refreshes expired tokens via refresh tokens
  - Per-user token cache with LRU eviction (max 1000 users)

- **`SAPAuthenticator`**: Facade wrapping the strategy for backward compatibility

### 3. SAP HTTP Client (`core/sap_client.py`)

`SAPClient` handles all HTTP communication with SAP Gateway:

- Async HTTP via `aiohttp` with connection pooling
- Automatic token refresh on 401 responses
- Retry with exponential backoff
- CSRF token support for write operations
- OData operations: query, get, create, update, delete

### 4. Configuration System

Three layers of configuration:

| Layer | Source | Purpose |
|-------|--------|---------|
| `SAPConnectionConfig` | Environment variables (`SAP_*`) | SAP server connection + OAuth credentials |
| `services.yaml` | YAML file | OData service/entity definitions |
| Secret Manager | GCP Secret Manager | Runtime credentials in production |

### 5. Cloud Run OAuth Callback

A separate Flask microservice (`cloud-run-oauth-callback/`) that:
1. Receives SAP OAuth redirect callbacks at `/callback`
2. Stores authorization codes in GCP Secret Manager
3. Uses Google One Tap (`/identify`) to link the user's Google account
4. The agent polls Secret Manager to detect completed logins

## Data Flow: Query Execution

```
1. User asks: "Show me sales orders from last month"
2. Gemini LLM decides to call sap_query tool
3. sap_query() checks for authenticated user:
   a. Gets SAPAuthenticator from per-user cache
   b. Calls get_valid_token() → returns cached or refreshed token
4. SAPClient.query_entity_set() sends OData GET request:
   - URL: https://{host}:{port}/sap/opu/odata/{service_path}/{entity_set}
   - Headers: Authorization: Bearer {access_token}
   - Params: $filter, $select, $top, $skip, $format=json
5. Response parsed and returned to LLM
6. LLM formats natural language response for user
```

## Data Flow: OAuth Authentication

```
1. User triggers sap_authenticate (or first query without auth)
2. Agent generates SAP OAuth URL with PKCE challenge
3. User opens URL → SAP login page → SAP redirects to Cloud Run callback
4. Cloud Run callback stores auth code in Secret Manager
5. Google One Tap identifies user's Google account (optional)
6. Agent polls Secret Manager, finds pending code
7. Agent exchanges code for access_token + refresh_token
8. Token cached per-user for subsequent requests
```

## Exception Hierarchy

```
SAPError (base)
  ├── SAPAuthenticationError
  │     └── SAPOAuthError
  ├── SAPConnectionError
  ├── SAPRequestError
  ├── SAPTimeoutError
  └── SAPValidationError
```

## Network Architecture (Production)

```
Vertex AI Agent Engine
    |  (PSC Network Attachment)
    v
VPC Network (sap-cal-default-network)
    |  (PSC Subnet: 192.168.10.0/28)
    |  (Firewall: allow tcp:44300,8000,443,80)
    v
SAP Gateway (on-premise, e.g., 10.142.0.5:44300)
```

Private Service Connect enables Agent Engine containers to reach on-premise SAP systems through a VPC without public internet exposure.
