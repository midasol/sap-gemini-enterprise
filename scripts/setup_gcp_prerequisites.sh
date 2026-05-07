#!/bin/bash
# ============================================================================
# GCP Prerequisites Setup Script for SAP Agent
# ============================================================================
# This script sets up all required GCP resources before deploying SAP Agent:
# - Enable required APIs
# - Create service accounts
# - Configure IAM permissions
# ============================================================================

set -e

# ============================================================================
# Configuration - Modify these values for your environment
# ============================================================================
PROJECT_ID="${PROJECT_ID:-[your-project-id]}"
REGION="${REGION:-us-central1}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# Helper Functions
# ============================================================================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============================================================================
# Step 0: Set Project and Get Project Number
# ============================================================================
echo ""
echo "============================================================================"
echo "Step 0: Setting up GCP project"
echo "============================================================================"

log_info "Setting project to $PROJECT_ID..."
gcloud config set project $PROJECT_ID

# Get project number (needed for service agent accounts)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
log_success "Project Number: $PROJECT_NUMBER"

# ============================================================================
# Step 1: Enable Required APIs
# ============================================================================
echo ""
echo "============================================================================"
echo "Step 1: Enabling required APIs"
echo "============================================================================"

APIS=(
    "compute.googleapis.com"              # Compute Engine (networking)
    "aiplatform.googleapis.com"           # Vertex AI / Agent Engine
    "secretmanager.googleapis.com"        # Secret Manager
    "cloudbuild.googleapis.com"           # Cloud Build (for deployment)
    "storage.googleapis.com"              # Cloud Storage (staging bucket)
    "iam.googleapis.com"                  # IAM
    "iamcredentials.googleapis.com"       # IAM Credentials
    "dns.googleapis.com"                  # Cloud DNS (for PSC)
    "servicenetworking.googleapis.com"    # Service Networking
)

for api in "${APIS[@]}"; do
    log_info "Enabling $api..."
    gcloud services enable $api --quiet
done

log_success "All required APIs enabled."

# ============================================================================
# Step 2: Create Service Account for Agent Engine
# ============================================================================
echo ""
echo "============================================================================"
echo "Step 2: Creating Service Account for Agent Engine"
echo "============================================================================"

SA_NAME="agent-engine-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Check if service account exists
if gcloud iam service-accounts describe $SA_EMAIL --project=$PROJECT_ID > /dev/null 2>&1; then
    log_warning "Service account $SA_NAME already exists."
else
    log_info "Creating service account $SA_NAME..."
    gcloud iam service-accounts create $SA_NAME \
        --project=$PROJECT_ID \
        --display-name="SAP Agent Engine Service Account" \
        --description="Service account for SAP Agent deployed on Vertex AI Agent Engine"
    log_success "Service account created: $SA_EMAIL"
fi

# ============================================================================
# Step 3: Assign IAM Roles to Agent Engine Service Account
# ============================================================================
echo ""
echo "============================================================================"
echo "Step 3: Assigning IAM roles to Agent Engine Service Account"
echo "============================================================================"

# Roles for agent-engine-sa
SA_ROLES=(
    "roles/aiplatform.user"                    # Vertex AI User
    "roles/secretmanager.secretAccessor"       # Secret Manager access
    "roles/storage.objectViewer"               # Read staging bucket
    "roles/logging.logWriter"                  # Write logs
    "roles/monitoring.metricWriter"            # Write metrics
    "roles/serviceusage.serviceUsageConsumer"  # Use project services
)

for role in "${SA_ROLES[@]}"; do
    log_info "Granting $role to $SA_NAME..."
    gcloud projects add-iam-policy-binding $PROJECT_ID \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" \
        --condition=None \
        --quiet
done

log_success "All roles assigned to $SA_NAME."

# ============================================================================
# Step 4: Configure GCP-Managed Service Agents (AI Platform)
# ============================================================================
echo ""
echo "============================================================================"
echo "Step 4: Configuring GCP-managed Service Agents"
echo "============================================================================"

# These are GCP-managed service accounts that need specific permissions
SERVICE_AGENTS=(
    "service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"
    "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
    "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-cc.iam.gserviceaccount.com"
)

# Roles needed by AI Platform service agents
AGENT_ROLES=(
    "roles/serviceusage.serviceUsageConsumer"
    "roles/compute.networkAdmin"
)

for sa in "${SERVICE_AGENTS[@]}"; do
    for role in "${AGENT_ROLES[@]}"; do
        log_info "Granting $role to $sa..."
        gcloud projects add-iam-policy-binding $PROJECT_ID \
            --member="serviceAccount:$sa" \
            --role="$role" \
            --condition=None \
            --quiet 2>/dev/null || log_warning "Could not grant $role to $sa (may not exist yet)"
    done
done

# DNS Peer role for PSC
log_info "Granting DNS Peer role for PSC..."
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com" \
    --role="roles/dns.peer" \
    --condition=None \
    --quiet 2>/dev/null || log_warning "Could not grant dns.peer role"

log_success "Service agents configured."

# ============================================================================
# Step 5: Create Staging Bucket (if not exists)
# ============================================================================
echo ""
echo "============================================================================"
echo "Step 5: Setting up Staging Bucket"
echo "============================================================================"

STAGING_BUCKET="${PROJECT_ID}_cloudbuild"

if gsutil ls -b gs://$STAGING_BUCKET > /dev/null 2>&1; then
    log_warning "Staging bucket gs://$STAGING_BUCKET already exists."
else
    log_info "Creating staging bucket gs://$STAGING_BUCKET..."
    gsutil mb -l $REGION gs://$STAGING_BUCKET
    log_success "Staging bucket created."
fi

# ============================================================================
# Step 6: Create Secret for SAP Credentials
# ============================================================================
echo ""
echo "============================================================================"
echo "Step 6: Setting up Secret Manager for SAP Credentials"
echo "============================================================================"

SECRET_NAME="sap-credentials"

if gcloud secrets describe $SECRET_NAME --project=$PROJECT_ID > /dev/null 2>&1; then
    log_warning "Secret $SECRET_NAME already exists."
else
    log_info "Creating secret $SECRET_NAME..."
    gcloud secrets create $SECRET_NAME \
        --project=$PROJECT_ID \
        --replication-policy="automatic"
    log_success "Secret created."
fi

log_info "Add SAP credentials based on your auth type:"
echo ""
echo "# SAP OAuth Authorization Code (per-user auth with PKCE)"
echo "echo '{"
echo '  "auth_type": "sap_oauth",'
echo '  "host": "10.142.0.5",'
echo '  "port": 44300,'
echo '  "client": "100",'
echo '  "oauth_client_id": "YOUR_OAUTH_CLIENT_ID",'
echo '  "oauth_client_secret": "YOUR_OAUTH_CLIENT_SECRET",'
echo '  "oauth_token_url": "https://sap-server/sap/bc/sec/oauth2/token?sap-client=100",'
echo '  "oauth_authorize_url": "https://sap-server/sap/bc/sec/oauth2/authorize?sap-client=100",'
echo '  "oauth_redirect_uri": "https://localhost:8000/callback",'
echo '  "oauth_scope": "YOUR_OAUTH_SCOPE"'
echo "}' | gcloud secrets versions add $SECRET_NAME --data-file=-"
echo ""

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "============================================================================"
echo "Setup Complete!"
echo "============================================================================"
echo ""
log_success "Project: $PROJECT_ID"
log_success "Project Number: $PROJECT_NUMBER"
log_success "Region: $REGION"
log_success "Service Account: $SA_EMAIL"
log_success "Staging Bucket: gs://$STAGING_BUCKET"
log_success "Secret Name: $SECRET_NAME"
echo ""
echo "Next Steps:"
echo "  1. Add SAP credentials to Secret Manager (if not done)"
echo "  2. Run setup_psc_infrastructure.sh for network setup"
echo "  3. Run deploy_agent_engine.py to deploy the agent"
echo ""
