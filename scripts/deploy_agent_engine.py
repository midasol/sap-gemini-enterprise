"""Deploy SAP Agent to Vertex AI Agent Engine.

This script deploys the SAP Agent with direct Python function tools
(not MCP subprocess) for Agent Engine compatibility.

Usage:
    # Create new Agent Engine
    python scripts/deploy_agent_engine.py --project <PROJECT_ID>

    # Update existing Agent Engine
    python scripts/deploy_agent_engine.py --project <PROJECT_ID> --update <RESOURCE_NAME>
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def parse_args():
    parser = argparse.ArgumentParser(
        description="Deploy SAP Agent to Vertex AI Agent Engine",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="GCP project ID",
    )
    parser.add_argument(
        "--update",
        metavar="RESOURCE_NAME",
        help=(
            "Update an existing Agent Engine resource instead of creating a new one. "
            "Pass the full resource name "
            "(e.g. projects/123/locations/us-central1/reasoningEngines/456)"
        ),
    )
    parser.add_argument(
        "--region",
        default="us-central1",
        help="GCP region (default: us-central1)",
    )
    parser.add_argument(
        "--staging-bucket",
        default=None,
        help="GCS staging bucket (default: gs://<PROJECT_ID>_cloudbuild)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    PROJECT_ID = args.project
    LOCATION = args.region
    STAGING_BUCKET = args.staging_bucket or f"gs://{PROJECT_ID}_cloudbuild"

    # Set env vars so downstream modules (Secret Manager, ADK) pick them up
    os.environ["PROJECT_ID"] = PROJECT_ID
    os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID

    # Load SAP credentials from .env (for local testing before deploy)
    env_path = Path("sap_agent/.env")
    if env_path.exists():
        print(f"Loading environment variables from {env_path}")
        load_dotenv(dotenv_path=env_path)
    else:
        print(f"Note: {env_path} not found. Using Secret Manager for credentials.")

    NETWORK_ATTACHMENT = (
        f"projects/{PROJECT_ID}/regions/{LOCATION}"
        f"/networkAttachments/agent-engine-attachment"
    )

    print(f"Initializing Vertex AI SDK...")
    print(f"  Project:            {PROJECT_ID}")
    print(f"  Location:           {LOCATION}")
    print(f"  Staging Bucket:     {STAGING_BUCKET}")
    print(f"  Network Attachment: {NETWORK_ATTACHMENT}")

    import vertexai
    from vertexai import agent_engines

    vertexai.init(
        project=PROJECT_ID,
        location=LOCATION,
        staging_bucket=STAGING_BUCKET,
    )

    # Import agent module (triggers config loading)
    import sap_agent.agent

    print("Preparing agent for deployment...")
    print(f"  Agent Model: {sap_agent.agent.MODEL_NAME}")
    print(f"  Agent Tools: {[t.__name__ for t in sap_agent.agent.root_agent.tools]}")

    # enable_tracing=True sends OpenTelemetry traces to Cloud Trace.
    # Requires 'telemetry.traces.write' permission on the service account.
    # Grant via: gcloud projects add-iam-policy-binding $PROJECT_ID \
    #   --member="serviceAccount:$SA" --role="roles/cloudtrace.agent"
    app = agent_engines.AdkApp(
        agent=sap_agent.agent.root_agent,
        enable_tracing=True,
    )

    # Service account with Secret Manager access
    SERVICE_ACCOUNT = f"agent-engine-sa@{PROJECT_ID}.iam.gserviceaccount.com"
    print(f"  Service Account: {SERVICE_ACCOUNT}")

    # ---------------------------------------------------------------
    # Load SAP credentials from Secret Manager
    # ---------------------------------------------------------------
    from google.cloud import secretmanager

    print("Loading SAP credentials from Secret Manager...")
    sm_client = secretmanager.SecretManagerServiceClient()
    secret_name = f"projects/{PROJECT_ID}/secrets/sap-credentials/versions/latest"
    response = sm_client.access_secret_version(request={"name": secret_name})
    sap_creds = json.loads(response.payload.data.decode("UTF-8"))
    print(f"  Loaded credentials: {list(sap_creds.keys())}")

    # Map Secret Manager keys → SAP_ env vars for deployment.
    # oauth_redirect_uri is excluded because the agent ID (part of the
    # redirect URI) is only assigned AFTER deployment; the agent reads
    # it from Secret Manager at runtime instead.
    RUNTIME_ONLY_KEYS = {"oauth_redirect_uri"}

    env_vars = {}
    for key, value in sap_creds.items():
        if key in RUNTIME_ONLY_KEYS:
            print(f"  Skipping {key} (read from Secret Manager at runtime)")
            continue
        env_vars[f"SAP_{key.upper()}"] = str(value)

    if "auth_server_url" in sap_creds:
        env_vars["AUTH_SERVER_URL"] = sap_creds["auth_server_url"]

    print(f"  Auth type: {env_vars.get('SAP_AUTH_TYPE', 'basic')}")
    print(f"  Env vars:  {list(env_vars.keys())}")

    # ---------------------------------------------------------------
    # Resource limits for Agent Engine containers
    # ---------------------------------------------------------------
    # Supported values:
    #   cpu    : "1", "2", "4", "6", "8"
    #   memory : "1Gi" ~ "32Gi"  (e.g. "1Gi", "2Gi", "4Gi", "8Gi", "16Gi", "32Gi")
    RESOURCE_LIMITS = {"cpu": "8", "memory": "16Gi"}
    print(f"  Resource Limits:   {RESOURCE_LIMITS}")

    # ---------------------------------------------------------------
    # Deploy / Update
    # ---------------------------------------------------------------
    REQUIREMENTS = [
        "google-cloud-aiplatform[adk,agent_engines]>=1.128.0",
        "google-adk>=1.27.0",
        "google-cloud-secret-manager>=2.16.0",
        "pydantic>=2.5.0",
        "pydantic-settings>=2.1.0",
        "aiohttp>=3.9.0",
        "asyncio-throttle>=1.0.2",
        "structlog>=23.2.0",
        "tenacity>=8.2.3",
        "authlib>=1.3.0",
        "cryptography>=41.0.7",
        "xmltodict>=0.13.0",
        "pyyaml>=6.0.1",
        "python-dotenv>=1.0.0",
        "nest-asyncio>=1.5.0",
    ]

    try:
        if args.update:
            print(f"\nUpdating existing Agent Engine: {args.update}")
            remote_app = agent_engines.update(
                resource_name=args.update,
                agent_engine=app,
                requirements=REQUIREMENTS,
                extra_packages=["./sap_agent"],
                display_name="SAP Agent",
                env_vars=env_vars,
                resource_limits=RESOURCE_LIMITS,
                psc_interface_config={
                    "network_attachment": NETWORK_ATTACHMENT,
                },
            )
            print("Update finished!")
        else:
            print("\nCreating new Agent Engine...")
            remote_app = agent_engines.create(
                agent_engine=app,
                requirements=REQUIREMENTS,
                extra_packages=["./sap_agent"],
                display_name="SAP Agent",
                service_account=SERVICE_ACCOUNT,
                env_vars=env_vars,
                resource_limits=RESOURCE_LIMITS,
                psc_interface_config={
                    "network_attachment": NETWORK_ATTACHMENT,
                },
            )
            print("Deployment finished!")

        print(f"Resource Name: {remote_app.resource_name}")

    except Exception as e:
        print(f"Deployment failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
