#!/bin/bash
# Local testing script for the insurance orchestrator
# This script helps you test the container locally before deploying to Azure

echo "🧪 Local Testing Script for Insurance Orchestrator"
echo "=================================================="

# Step 1: Check if logged into Azure
echo "1️⃣ Checking Azure login status..."
if ! az account show &> /dev/null; then
    echo "❌ Not logged into Azure. Please run: az login"
    exit 1
fi
echo "✅ Azure login confirmed"

# Step 2: Get current subscription and resource group info
SUBSCRIPTION_ID=$(az account show --query id --output tsv)
echo "📋 Current subscription: $SUBSCRIPTION_ID"

# You need to set these variables to match your environment
RESOURCE_GROUP=""  #Fill Replace with your resource group
SP_NAME="" #Fill Replace with a unique name for your service principal

echo ""
echo "2️⃣ Creating service principal (if needed)..."
echo "Resource Group: $RESOURCE_GROUP"
echo "Service Principal Name: $SP_NAME"

# Create service principal
SP_OUTPUT=$(az ad sp create-for-rbac --name "$SP_NAME" --role "Cognitive Services User" --scopes "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" 2>/dev/null || echo "Service principal might already exist")

if [[ $SP_OUTPUT == *"Service principal might already exist"* ]]; then
    echo "⚠️  Service principal might already exist. Getting existing credentials..."
    # Get existing service principal info
    SP_APP_ID=$(az ad sp list --display-name "$SP_NAME" --query "[0].appId" --output tsv)
    if [ -z "$SP_APP_ID" ]; then
        echo "❌ Could not find existing service principal. Please delete and recreate it."
        exit 1
    fi
    echo "✅ Found existing service principal: $SP_APP_ID"
    echo "⚠️  You'll need to use the existing password or reset it with:"
    echo "   az ad sp credential reset --id $SP_APP_ID"
else
    # Extract credentials from new service principal
    SP_APP_ID=$(echo $SP_OUTPUT | jq -r .appId)
    SP_PASSWORD=$(echo $SP_OUTPUT | jq -r .password)
    SP_TENANT=$(echo $SP_OUTPUT | jq -r .tenant)
    
    echo "✅ Service principal created successfully!"
    echo "📝 App ID: $SP_APP_ID"
    echo "🔑 Password: $SP_PASSWORD"
    echo "🏢 Tenant: $SP_TENANT"
    echo ""
    echo "⚠️  SAVE THESE CREDENTIALS SECURELY - You'll need them for deployment!"
fi

echo ""
echo "3️⃣ Building Docker image..."
if docker build -t insurance-orchestrator . ; then
    echo "✅ Docker image built successfully"
else
    echo "❌ Docker build failed"
    exit 1
fi

echo ""
echo "4️⃣ Ready to test locally!"
echo "To run the container locally, use the following command:"
echo ""
echo "docker run -p 8080:8000 \\"
echo "  -e AZURE_CLIENT_ID=\"\$SP_APP_ID\" \\"
echo "  -e AZURE_CLIENT_SECRET=\"\$SP_PASSWORD\" \\"
echo "  -e AZURE_TENANT_ID=\"\$SP_TENANT\" \\"
echo "  -e AI_FOUNDRY_PROJECT_ENDPOINT=\"YOUR_AI_FOUNDRY_ENDPOINT\" \\"
echo "  -e MODEL_DEPLOYMENT_NAME=\"YOUR_MODEL_NAME\" \\"
echo "  -e COSMOS_ENDPOINT=\"YOUR_COSMOS_ENDPOINT\" \\"
echo "  -e COSMOS_KEY=\"YOUR_COSMOS_KEY\" \\"
echo "  -e AZURE_AI_CONNECTION_ID=\"YOUR_AI_CONNECTION_ID\" \\"
echo "  -e AZURE_AI_SEARCH_INDEX_NAME=\"YOUR_SEARCH_INDEX\" \\"
echo "  -e SEARCH_SERVICE_NAME=\"YOUR_SEARCH_SERVICE\" \\"
echo "  -e SEARCH_SERVICE_ENDPOINT=\"YOUR_SEARCH_ENDPOINT\" \\"
echo "  -e SEARCH_ADMIN_KEY=\"YOUR_SEARCH_KEY\" \\"
echo "  -e CLAIM_ID=\"CL001\" \\"
echo "  -e POLICY_NUMBER=\"LIAB-AUTO-001\" \\"
echo "  insurance-orchestrator"
echo ""
echo "Replace all YOUR_* values with the actual values from your .env file"
echo ""
echo "🎯 After successful local testing, update container-apps.sh with your credentials and deploy to Azure!"
