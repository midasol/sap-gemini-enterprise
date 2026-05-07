# Troubleshooting

Common issues and solutions when developing, deploying, or running the SAP ADK Agent.

## Authentication Errors

### "SAP OAuth login required. No user has authenticated yet."

**Cause**: The agent has no cached token for the current user. The user must authenticate first.

**Solution**: Ask the agent to authenticate (it will call `sap_authenticate` and return a login URL).

### "Authorization code is invalid or expired. Please restart the SAP login flow."

**Cause**: The OAuth authorization code was already used or expired before exchange.

**Solution**: Start a new login flow. Auth codes are single-use and time-limited.

### "OAuth client credentials are invalid."

**Cause**: `SAP_OAUTH_CLIENT_ID` or `SAP_OAUTH_CLIENT_SECRET` don't match what's configured in SAP.

**Solution**: Verify credentials in SAP transaction `SOAUTH2` and update your `.env` or Secret Manager.

### "SAP session expired and refresh failed."

**Cause**: Both the access token and refresh token are expired or revoked.

**Solution**: The user must re-authenticate via the SAP login flow.

## Connection Errors

### "Connection error during SAP OAuth token request"

**Cause**: Agent cannot reach the SAP server.

**Solutions**:
- **Local dev**: Ensure SAP host is reachable from your network. Check VPN if required.
- **Agent Engine**: Verify PSC infrastructure is set up correctly. Check:
  - Network attachment exists: `gcloud compute network-attachments list`
  - Firewall rules allow traffic: `gcloud compute firewall-rules list --filter="name:allow-agent-engine"`
  - SAP host IP is correct in firewall destination ranges

### "Request timeout for GET/POST ..."

**Cause**: SAP server is slow or unreachable.

**Solution**: Increase `SAP_TIMEOUT` (default: 30 seconds). Check SAP system health.

### SSL Certificate Errors

**Cause**: SAP uses self-signed or internal CA certificates.

**Solution**: Set `SAP_VERIFY_SSL=False` for development. For production, install proper certificates or configure the CA bundle.

## Configuration Errors

### "SAP host cannot be empty"

**Cause**: `SAP_HOST` environment variable is not set.

**Solution**: Set `SAP_HOST` in `sap_agent/.env` or as an environment variable.

### "oauth_authorize_url is required for sap_oauth authentication"

**Cause**: Missing `SAP_OAUTH_AUTHORIZE_URL` in configuration.

**Solution**: Set all required OAuth environment variables. See [Configuration Reference](CONFIGURATION.md).

### "Only auth_type 'sap_oauth' is supported"

**Cause**: `SAP_AUTH_TYPE` is set to something other than `sap_oauth`.

**Solution**: Set `SAP_AUTH_TYPE=sap_oauth`. Basic auth and client credentials are no longer supported.

### "Service 'X' not found in configuration"

**Cause**: The service ID used in a query doesn't match any service in `services.yaml`.

**Solution**: Check `sap_agent/services.yaml` for the correct service ID. Use `sap_list_services` to see available services.

## Deployment Errors

### "Deployment failed: permission denied"

**Cause**: Missing IAM permissions.

**Solution**: Run `scripts/setup_gcp_prerequisites.sh` to configure service accounts and roles. Key roles needed:
- `roles/aiplatform.user` on `agent-engine-sa`
- `roles/secretmanager.secretAccessor` on `agent-engine-sa`
- `roles/compute.networkAdmin` on AI Platform service agent

### "Secret 'sap-credentials' not found"

**Cause**: SAP credentials secret hasn't been created in Secret Manager.

**Solution**: Create the secret:
```bash
echo '{"auth_type":"sap_oauth","host":"...","oauth_client_id":"..."}' | \
  gcloud secrets versions add sap-credentials --data-file=-
```

### Agent Engine cannot reach SAP

**Cause**: PSC infrastructure not configured.

**Solution**: Run `scripts/setup_psc_infrastructure.sh` to create:
- PSC subnet
- Network attachment
- Firewall rules allowing traffic to SAP IP

## Cloud Run OAuth Callback

### "GOOGLE_CLOUD_PROJECT environment variable is required"

**Cause**: The Cloud Run service is missing its project ID configuration.

**Solution**: Set `GOOGLE_CLOUD_PROJECT` when deploying:
```bash
gcloud run deploy ... --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
```

### Google One Tap not appearing

**Cause**: The Google Client ID doesn't match, or the user's browser blocks third-party cookies.

**Solution**:
- Verify `GOOGLE_CLIENT_ID` matches your OAuth consent screen
- The SAP login itself still works — One Tap is optional for user identification

## Test Failures

### "ModuleNotFoundError: No module named 'sap_agent'"

**Cause**: Package not installed in development mode.

**Solution**: `pip install -e .` or `uv sync --group dev`

### Tests fail with real SAP credentials in environment

**Cause**: The `clean_env` fixture removes `SAP_*` vars, but some may leak through.

**Solution**: Tests use `conftest.py` autouse fixtures to clean the environment. If issues persist, check for environment variables set outside the test process.
