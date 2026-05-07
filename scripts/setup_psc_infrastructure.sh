#!/bin/bash
# ============================================================================
# PSC Infrastructure Setup Script for SAP Agent
# ============================================================================
# This script sets up Private Service Connect (PSC) infrastructure:
# - PSC Subnet
# - Network Attachment
# - Firewall Rules
# - Service Agent IAM for PSC
# ============================================================================
# Prerequisites: Run setup_gcp_prerequisites.sh first
# ============================================================================

set -e

# ============================================================================
# Configuration - Modify these values for your environment
# ============================================================================
PROJECT_ID="${PROJECT_ID:-[your-project-id]}"
REGION="${REGION:-us-central1}"
VPC_NAME="${VPC_NAME:-sap-cal-default-network}"
PSC_SUBNET_NAME="${PSC_SUBNET_NAME:-psc-attachment-subnet}"
PSC_SUBNET_RANGE="${PSC_SUBNET_RANGE:-192.168.10.0/28}"
ATTACHMENT_NAME="${ATTACHMENT_NAME:-agent-engine-attachment}"
SAP_IP="${SAP_IP:-10.142.0.5}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

echo ""
echo "============================================================================"
echo "PSC Infrastructure Setup"
echo "============================================================================"

log_info "Setting project to $PROJECT_ID..."
gcloud config set project $PROJECT_ID

# Get project number
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
log_info "Project Number: $PROJECT_NUMBER"

log_info "Enabling necessary APIs..."
gcloud services enable compute.googleapis.com aiplatform.googleapis.com dns.googleapis.com --quiet

# ============================================================================
# Step 1: Create PSC Subnet
# ============================================================================
echo ""
echo "Step 1: Creating PSC Subnet"
echo "-------------------------------------------"

if gcloud compute networks subnets describe $PSC_SUBNET_NAME --region=$REGION --project=$PROJECT_ID > /dev/null 2>&1; then
    log_warning "Subnet $PSC_SUBNET_NAME already exists."
else
    log_info "Creating PSC subnet $PSC_SUBNET_NAME..."
    gcloud compute networks subnets create $PSC_SUBNET_NAME \
        --project=$PROJECT_ID \
        --network=$VPC_NAME \
        --range=$PSC_SUBNET_RANGE \
        --region=$REGION
    log_success "PSC subnet created."
fi

# ============================================================================
# Step 2: Create Network Attachment
# ============================================================================
echo ""
echo "Step 2: Creating Network Attachment"
echo "-------------------------------------------"

if gcloud compute network-attachments describe $ATTACHMENT_NAME --region=$REGION --project=$PROJECT_ID > /dev/null 2>&1; then
    log_warning "Network attachment $ATTACHMENT_NAME already exists."
else
    log_info "Creating Network Attachment $ATTACHMENT_NAME..."
    gcloud compute network-attachments create $ATTACHMENT_NAME \
        --project=$PROJECT_ID \
        --region=$REGION \
        --connection-preference=ACCEPT_AUTOMATIC \
        --subnets=$PSC_SUBNET_NAME
    log_success "Network Attachment created."
fi

# ============================================================================
# Step 3: Create Firewall Rules
# ============================================================================
echo ""
echo "Step 3: Creating Firewall Rules"
echo "-------------------------------------------"

FW_RULE_NAME="allow-agent-engine-to-sap"
if gcloud compute firewall-rules describe $FW_RULE_NAME --project=$PROJECT_ID > /dev/null 2>&1; then
    log_warning "Firewall rule $FW_RULE_NAME already exists."
else
    log_info "Creating firewall rule to allow Agent Engine traffic..."
    gcloud compute firewall-rules create $FW_RULE_NAME \
        --project=$PROJECT_ID \
        --network=$VPC_NAME \
        --action=ALLOW \
        --direction=INGRESS \
        --rules=tcp:44300,tcp:8000,tcp:443,tcp:80 \
        --source-ranges="$PSC_SUBNET_RANGE" \
        --destination-ranges="$SAP_IP/32" \
        --description="Allow Agent Engine PSC traffic to SAP"
    log_success "Firewall rule created."
fi

# ============================================================================
# Step 4: Configure AI Platform Service Agent for PSC
# ============================================================================
echo ""
echo "Step 4: Configuring AI Platform Service Agent for PSC"
echo "-------------------------------------------"

AI_PLATFORM_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"

log_info "Granting Network Admin role to AI Platform service agent..."
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$AI_PLATFORM_SA" \
    --role="roles/compute.networkAdmin" \
    --quiet 2>/dev/null || log_warning "Could not grant networkAdmin (may already exist)"

log_info "Granting DNS Peer role for PSC DNS resolution..."
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$AI_PLATFORM_SA" \
    --role="roles/dns.peer" \
    --quiet 2>/dev/null || log_warning "Could not grant dns.peer (may already exist)"

log_success "AI Platform service agent configured for PSC."

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "============================================================================"
echo "PSC Infrastructure Setup Complete!"
echo "============================================================================"
echo ""
log_success "VPC Network: $VPC_NAME"
log_success "PSC Subnet: $PSC_SUBNET_NAME ($PSC_SUBNET_RANGE)"
log_success "Network Attachment: projects/$PROJECT_ID/regions/$REGION/networkAttachments/$ATTACHMENT_NAME"
log_success "Firewall Rule: $FW_RULE_NAME"
log_success "SAP Target IP: $SAP_IP"
echo ""
echo "The deployment script will use:"
echo "  NETWORK_ATTACHMENT=\"projects/$PROJECT_ID/regions/$REGION/networkAttachments/$ATTACHMENT_NAME\""
echo ""
