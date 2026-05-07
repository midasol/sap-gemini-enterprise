# Local Development Guide

## Prerequisites

- Python 3.11+ (up to 3.13)
- GCP project with Vertex AI enabled
- SAP Gateway system accessible from your network
- `gcloud` CLI authenticated

## Setup

### 1. Clone and Install

```bash
git clone <repository-url>
cd sap-adk-agent

# Install with uv (recommended)
uv sync --group dev

# Or with pip
pip install -e ".[dev]"
```

### 2. Configure Environment

Copy and edit the environment template:

```bash
cp sap_agent/.env.example sap_agent/.env
# Edit sap_agent/.env with your SAP credentials
```

Required variables — see [Configuration Reference](CONFIGURATION.md) for the full list:

```env
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id

SAP_HOST=your-sap-host
SAP_OAUTH_CLIENT_ID=your-client-id
SAP_OAUTH_CLIENT_SECRET=your-client-secret
SAP_OAUTH_TOKEN_URL=https://your-sap-host:44300/sap/bc/sec/oauth2/token?sap-client=100
SAP_OAUTH_AUTHORIZE_URL=https://your-sap-host:44300/sap/bc/sec/oauth2/authorize?sap-client=100
```

### 3. Configure Services

Edit `sap_agent/services.yaml` to match your SAP OData services. See [Configuration Reference](CONFIGURATION.md#servicesyaml) for the schema.

## Running Locally

### ADK Dev UI

The simplest way to run locally — launches the ADK web interface:

```bash
cd sap_agent
adk web
```

This starts the ADK development server with a chat UI at `http://localhost:8000`.

### HTTPS Dev Server

For testing OAuth callbacks locally (which require HTTPS):

```bash
# Generate self-signed certificates
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem \
  -days 365 -nodes -subj '/CN=localhost'

# Run HTTPS server
python run_https.py
```

This starts the server at `https://localhost:8000` with the self-signed cert.

## Running Tests

```bash
# All tests
pytest

# With verbose output
pytest -v

# Specific test file
pytest tests/test_auth.py
```

See [Testing Guide](TESTING.md) for more details.

## Linting

```bash
# Ruff (linting + formatting)
ruff check .
ruff format .

# MyPy (type checking)
mypy sap_agent/

# Codespell (typo checking)
codespell
```

Install lint dependencies: `pip install -e ".[lint]"`

## Project Scripts

| Script | Purpose |
|--------|---------|
| `scripts/deploy_agent_engine.py` | Deploy agent to Vertex AI Agent Engine |
| `scripts/cleanup_agent_engines.py` | Delete all Agent Engine instances |
| `scripts/setup_gcp_prerequisites.sh` | Set up GCP APIs, service accounts, IAM |
| `scripts/setup_psc_infrastructure.sh` | Set up PSC networking for on-premise SAP |
| `scripts/test_agent_engine.py` | Test a deployed Agent Engine instance |
| `scripts/test_remote_agent_v2.py` | Test remote agent with streaming |

### Deploy to Agent Engine

```bash
python scripts/deploy_agent_engine.py --project your-project-id

# Update existing deployment
python scripts/deploy_agent_engine.py --project your-project-id \
  --update projects/123/locations/us-central1/reasoningEngines/456
```
