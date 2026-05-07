# SAP Agent Quick Reference

## Setup Checklist

```bash
# 1. GCP resources (APIs, service accounts, IAM)
./scripts/setup_gcp_prerequisites.sh

# 2. Private network to SAP (if on-prem)
./scripts/setup_psc_infrastructure.sh

# 3. Store SAP credentials
gcloud secrets create sap-credentials --replication-policy="automatic"
echo '{ "auth_type": "sap_oauth", ... }' | gcloud secrets versions add sap-credentials --data-file=-

# 4. Deploy Cloud Run OAuth callback
cd cloud-run-oauth-callback && gcloud run deploy sap-oauth-callback --source . --allow-unauthenticated

# 5. Deploy agent
python scripts/deploy_agent_engine.py --project <PROJECT_ID>

# 5-alt. Update existing agent
python scripts/deploy_agent_engine.py --project <PROJECT_ID> --update <RESOURCE_NAME>

# 6. Verify
gcloud ai reasoning-engines list --region=us-central1
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SAP_HOST` | Yes | - | SAP Gateway hostname or IP |
| `SAP_PORT` | No | 44300 | SAP Gateway HTTPS port |
| `SAP_CLIENT` | No | 100 | SAP client number |
| `SAP_AUTH_TYPE` | Yes | sap_oauth | Auth type (only `sap_oauth`) |
| `SAP_OAUTH_CLIENT_ID` | Yes | - | OAuth client ID |
| `SAP_OAUTH_CLIENT_SECRET` | Yes | - | OAuth client secret |
| `SAP_OAUTH_TOKEN_URL` | Yes | - | Token endpoint |
| `SAP_OAUTH_AUTHORIZE_URL` | Yes | - | Authorization endpoint |
| `SAP_OAUTH_REDIRECT_URI` | Yes | - | Redirect URI (Cloud Run URL in prod) |
| `SAP_OAUTH_SCOPE` | No | - | OAuth scope |
| `SAP_AGENT_MODEL` | No | gemini-3.1-pro-preview | LLM model override |
| `SAP_VERIFY_SSL` | No | false | SSL verification |
| `SAP_TIMEOUT` | No | 30 | Request timeout (seconds) |
| `SAP_RETRY_ATTEMPTS` | No | 3 | Retry count |

## Agent Tools

### sap_authenticate

Authenticates with SAP via OAuth Authorization Code + PKCE.

```python
# Step 1: Get login URL (no args needed)
sap_authenticate()
# Returns: {"auth_url": "https://sap-host/authorize?...", "state": "..."}

# Step 2: Exchange code (usually auto-detected via Cloud Run)
sap_authenticate(authorization_code="<code>", oauth_state="<state>")
# Returns: {"status": "authenticated", "sap_user": "SAPUSER01"}
```

### sap_list_services

Lists configured SAP OData services from `services.yaml`.

```python
sap_list_services()
# Returns: List of services with IDs, names, entities
```

### sap_query

Queries OData entity sets with filtering and pagination.

```python
sap_query(
    service="Z_TRAVEL_RECO_SRV",       # required - service ID
    entity_set="AirlineSet",            # required - entity set name
    filter="Carrid eq 'LH'",           # optional - OData $filter
    select="Carrid,Carrname",           # optional - comma-separated fields
    top=10,                             # optional - max records
    skip=0,                             # optional - pagination offset
    format="json_compact"               # optional - "json" or "json_compact" (default)
)
```

### sap_get_entity

Retrieves a single entity by key.

```python
sap_get_entity(
    service="Z_SALES_ORDER_GENAI_SRV",  # required
    entity_set="zsd004Set",              # required
    entity_key="91000092",               # required - key value
    select="Vbeln,Netwr,Waerk"           # optional
)
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/setup_gcp_prerequisites.sh` | Enable APIs, create service accounts, assign IAM |
| `scripts/setup_psc_infrastructure.sh` | Set up Private Service Connect for SAP |
| `scripts/deploy_agent_engine.py` | Deploy/update agent on Vertex AI |
| `scripts/cleanup_agent_engines.py` | Remove deployed agents |
| `scripts/test_deployed_sap_agent.py` | Test deployed agent |
| `scripts/test_agent_engine.py` | Basic connectivity test |
| `scripts/test_agent_engine_airlines.py` | Airline query test |

## Local Development

```bash
# HTTPS server (required for SAP OAuth redirect)
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -keyout certs/key.pem -out certs/cert.pem \
  -days 365 -nodes -subj '/CN=localhost'
python run_https.py
# -> https://localhost:8000

# ADK Web UI (simpler, but OAuth redirect won't work)
adk web
# -> http://localhost:8501
```

## SAP Configuration (Transactions)

| Transaction | Action |
|------------|--------|
| `SICF` | Activate `/sap/bc/sec/oauth2/authorize` and `/token` |
| `SOAUTH2` | Create OAuth client, register redirect URI, assign scopes |
| `SU01` | Set OAuth client user password (= client secret) |
| `PFCG` | Assign OData authorizations to end users |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| SAP connection timeout | Use internal IP for PSC; check firewall on port 44300 |
| SSL errors | `SAP_VERIFY_SSL=false` for dev; proper certs in prod |
| OAuth invalid_grant | Authorization code expired; restart login flow |
| OAuth invalid_client | Check client ID and secret |
| Redirect URI mismatch | Must match exactly: SOAUTH2, Secret Manager, Cloud Run |
| serviceUsageConsumer error | Run `setup_gcp_prerequisites.sh` |
| Secret Manager denied | Check IAM for `agent-engine-sa` |
| redirect_uri not found | Ensure it's in Secret Manager `sap-credentials` |
| Services not listed | Check `services.yaml` configuration |
| Query returns empty | Verify entity set name and OData filter syntax |

### Debugging Commands

```bash
# Agent Engine logs
gcloud logging read "resource.type=aiplatform.googleapis.com/ReasoningEngine" \
  --limit=50 --format=json

# Agent details
gcloud ai reasoning-engines describe <ENGINE_ID> --region=us-central1

# Check credentials
gcloud secrets versions access latest --secret=sap-credentials
```

## Quick Testing

```python
from vertexai import agent_engines

agent = agent_engines.get(
    "projects/<number>/locations/us-central1/reasoningEngines/<engine-id>"
)
session = agent.create_session()

session.send_message("authenticate with SAP")   # Get login URL
session.send_message("list available services")  # After auth
session.send_message("show me all airlines")     # Query data
```

## Key Defaults

| Setting | Default |
|---------|---------|
| SAP Port | 44300 |
| SAP Client | 100 |
| Model | gemini-3.1-pro-preview |
| Region | us-central1 |
| Timeout | 30 seconds |
| Retries | 3 |
| Output Format | json_compact |
| Max Cached Users | 1000 |

---

- [Deployment Guide](DEPLOYMENT_GUIDE.md)
- [SAP OAuth Setup](AUTH_SAP_OAUTH.md)
- [Korean Documentation (한국어 문서)](KR/QUICK_REFERENCE.md)
