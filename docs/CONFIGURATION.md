# Configuration Reference

All configuration options for the SAP ADK Agent.

## Environment Variables

Environment variables are loaded from `sap_agent/.env` (local development) or set as container env vars (Agent Engine deployment). The `SAP_` prefix is required.

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `SAP_HOST` | SAP Gateway server hostname/IP | `10.142.0.5` |
| `SAP_OAUTH_CLIENT_ID` | OAuth 2.0 client ID from SAP | `OAUTH2` |
| `SAP_OAUTH_CLIENT_SECRET` | OAuth 2.0 client secret | `MySecret123` |
| `SAP_OAUTH_TOKEN_URL` | SAP OAuth token endpoint | `https://10.142.0.5:44300/sap/bc/sec/oauth2/token?sap-client=100` |
| `SAP_OAUTH_AUTHORIZE_URL` | SAP OAuth authorization endpoint | `https://10.142.0.5:44300/sap/bc/sec/oauth2/authorize?sap-client=100` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `SAP_PORT` | `44300` | SAP server port |
| `SAP_CLIENT` | `100` | SAP client number |
| `SAP_AUTH_TYPE` | `sap_oauth` | Authentication type (only `sap_oauth` is supported) |
| `SAP_OAUTH_REDIRECT_URI` | _(none)_ | OAuth redirect URI for callback |
| `SAP_OAUTH_SCOPE` | _(none)_ | OAuth scope (e.g., `Z_TRAVEL_RECO_SRV_0001`) |
| `SAP_OAUTH_CSRF_FOR_WRITES` | `False` | Fetch CSRF token for write operations |
| `SAP_VERIFY_SSL` | `False` | Verify SSL certificates (set `True` for production with valid certs) |
| `SAP_TIMEOUT` | `30` | Request timeout in seconds |
| `SAP_RETRY_ATTEMPTS` | `3` | Number of retry attempts on failure |

### GCP Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `GOOGLE_GENAI_USE_VERTEXAI` | Use Vertex AI for Gemini | `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | `my-project-id` |
| `GOOGLE_CLOUD_LOCATION` | GCP location | `global` |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID for One Tap | _(auto-configured)_ |

### Server Variables (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `SAP_GW_HOST` | `0.0.0.0` | Server bind address |
| `SAP_GW_PORT` | `8000` | Server port |
| `SAP_GW_LOG_LEVEL` | `INFO` | Logging level |
| `SAP_GW_DEBUG` | `False` | Enable debug mode |
| `SAP_SERVICES_CONFIG_PATH` | _(auto-detected)_ | Path to `services.yaml` |

## services.yaml

Defines SAP OData services, entity sets, and gateway configuration. Located at `sap_agent/services.yaml`.

### Schema

```yaml
# Gateway URL configuration
gateway:
  base_url_pattern: "https://{host}:{port}/sap/opu/odata"  # Must contain {host} and {port}
  metadata_suffix: "/$metadata"
  service_catalog_path: "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection"
  auth_endpoint:
    use_catalog_metadata: true           # Recommended: use generic catalog
    # service_id: Z_MY_SRV              # Alternative: use specific service
    # entity_name: MyEntitySet          # Entity for CSRF token
    # csrf_required_for_writes: false

# Service definitions
services:
  - id: Z_MY_SERVICE_SRV                # Unique ID (used in tool calls)
    name: "My Service"                   # Human-readable name
    path: "/SAP/Z_MY_SERVICE_SRV"       # Must start with /
    version: v2                          # v2 or v4
    description: "Service description"
    entities:
      - name: MyEntitySet               # Entity set name from $metadata
        key_field: MyKey                 # Primary key field
        description: "Entity description"
        navigations:                     # Navigation properties (informational)
          - ToRelated
        default_select:                  # Default fields for queries
          - MyKey
          - Name
          - Date
    custom_headers: {}                   # Service-specific HTTP headers
```

### How to Find Service Details

1. **Service names**: SAP transaction `SE80` or `/IWFND/MAINT_SERVICE`
2. **Entity sets and keys**: Browse `{base_url}{service_path}/$metadata`
3. **Service path format**: Usually `/SAP/<SERVICE_NAME>` or `/<NAMESPACE>/<SERVICE_NAME>`

## Secret Manager (Production)

In production, SAP credentials are stored in GCP Secret Manager under the secret name `sap-credentials`:

```json
{
  "auth_type": "sap_oauth",
  "host": "10.142.0.5",
  "port": 44300,
  "client": "100",
  "oauth_client_id": "YOUR_CLIENT_ID",
  "oauth_client_secret": "YOUR_CLIENT_SECRET",
  "oauth_token_url": "https://sap-server/sap/bc/sec/oauth2/token?sap-client=100",
  "oauth_authorize_url": "https://sap-server/sap/bc/sec/oauth2/authorize?sap-client=100",
  "oauth_redirect_uri": "https://your-callback-url/callback",
  "oauth_scope": "YOUR_SCOPE"
}
```

The agent reads this secret at startup via `_load_runtime_secrets()` in `agent.py`.

## Configuration Validation

All configuration is validated at load time using Pydantic:

- `SAPConnectionConfig`: Validates host, port (1-65535), auth_type (must be `sap_oauth`), and ensures OAuth credentials are complete
- `ServicesYAMLConfig`: Validates service paths start with `/`, OData version is `v2` or `v4`, entity names are non-empty
- `GatewayConfig`: Validates `base_url_pattern` contains `{host}` and `{port}` placeholders

Invalid configuration raises `ValueError` or `ValidationError` at startup.
