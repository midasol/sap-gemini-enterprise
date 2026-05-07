# Cloud Run OAuth Callback Service

A standalone Flask microservice that receives SAP OAuth redirect callbacks and bridges them to the agent via GCP Secret Manager.

## Why This Exists

When a user authenticates with SAP via OAuth, SAP redirects back to a callback URL with an authorization code. The agent running on Vertex AI Agent Engine cannot directly receive HTTP callbacks, so this Cloud Run service acts as an intermediary:

1. SAP redirects the user's browser to this service with the auth code
2. The service stores the code in GCP Secret Manager
3. The agent polls Secret Manager and picks up the code automatically

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/callback` | GET | Receives SAP OAuth redirect with `code` and `state` query params |
| `/identify` | POST | Receives Google ID token from One Tap to link user identity |
| `/health` | GET | Health check, returns `{"status": "ok"}` |

### `/callback` Flow

1. Receives `code`, `state` (and optionally `error`) from SAP redirect
2. Creates a Secret Manager secret named `sap-oauth-pending-{state[:16]}`
3. Stores `{"code": "...", "state": "...", "timestamp": "..."}` as a secret version
4. Returns an HTML success page with Google One Tap integration

### `/identify` Flow

1. Receives POST with `{"credential": "<google_id_token>", "secret_id": "..."}`
2. Verifies the Google ID token against `https://oauth2.googleapis.com/tokeninfo`
3. Checks audience matches the configured `GOOGLE_CLIENT_ID`
4. Updates the pending secret to include `google_user_email`
5. The agent uses this email to match the OAuth code to the correct ADK user

## Configuration

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `GOOGLE_CLOUD_PROJECT` | Yes | GCP project ID for Secret Manager |
| `GOOGLE_CLIENT_ID` | No | Google OAuth Client ID for One Tap verification |
| `PORT` | No | Server port (default: `8080`) |

## Deployment

### Build and Deploy

```bash
cd cloud-run-oauth-callback

# Build container
gcloud builds submit --tag gcr.io/$PROJECT_ID/sap-oauth-callback

# Deploy to Cloud Run
gcloud run deploy sap-oauth-callback \
  --image gcr.io/$PROJECT_ID/sap-oauth-callback \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
```

### Required IAM Permissions

The Cloud Run service account needs:
- `roles/secretmanager.secretAccessor` — read existing secrets
- `roles/secretmanager.secretVersionAdder` — add secret versions
- `roles/secretmanager.admin` — create new secrets (for pending codes)

## Docker Configuration

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "main:app"]
```

Dependencies: `flask`, `gunicorn`, `google-cloud-secret-manager`, `requests`

## Security

- The `/callback` endpoint is unauthenticated (SAP redirects the user's browser)
- All user input is escaped with `markupsafe.escape` before rendering in HTML
- Google ID tokens are verified against Google's `tokeninfo` endpoint and audience-checked
- Authorization codes are stored in Secret Manager (encrypted at rest)
- Secret IDs are sanitized to prevent injection (`re.sub(r"[^a-zA-Z0-9_-]", "_", ...)`)
