# Challenge 5 

This challenge 5 is about building a FastAPI service that processes insurance claims using AI agents and Azure services.

The service will expose a single endpoint:

```bash
POST /process-claim

Request body (application/json):
{
  "claimId": "string",
  "policyNumber": "string"
}

Response body (application/json):
{
  "decision": "string",
  "justification": "string"
}
```

## Quick start

1. Move to challenge-5-api directory, create and activate a Python 3.11 virtual environment:

   ```bash
   cd challenge-5-api
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the app:

   ```bash
   uvicorn main:app --reload --port 8000
   ```

4. Test with curl:

   ```bash
   CLAIM_ID="CL001"
   POLICY_NUMBER="LIAB-AUTO-001"
   curl -sS -X POST "http://127.0.0.1:8000/process-claim"   -H "Content-Type: application/json"   -d "{\"claimId\":\"$CLAIM_ID\",\"policyNumber\":\"$POLICY_NUMBER\"}" | jq
   ```

## Build and Run with Docker locally

1. Build the Docker image:

   ```bash
   docker build -t claim-manager:latest .
   ```

2. Run the Docker container:

   Create the Service Principal and assign role:

   ```bash
   SP_NAME="<your-sp-name>"
   SUBSCRIPTION_ID=$(az account show --query id -o tsv)
   RESOURCE_GROUP="<your-resource-group>"
   SP_OUTPUT=$(az ad sp create-for-rbac --name "$SP_NAME" --role "Cognitive Services User" --scopes "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" 2>/dev/null || echo "Service principal might already exist")
   SP_APP_ID=$(echo $SP_OUTPUT | jq -r .appId)
   SP_PASSWORD=$(echo $SP_OUTPUT | jq -r .password)
   SP_TENANT=$(echo $SP_OUTPUT | jq -r .tenant)
   ```

   Then run the container with the necessary environment variables:

   ```bash
   docker run -p 8000:8000 --name claim-manager \
      -e AZURE_CLIENT_ID=$SP_APP_ID \
      -e AZURE_TENANT_ID=$SP_TENANT \
      -e AZURE_CLIENT_SECRET=$SP_PASSWORD \
      -e COSMOS_ENDPOINT=<your-cosmos-endpoint> \
      -e COSMOS_KEY=<your-cosmos-key> \
      -e AI_FOUNDRY_PROJECT_ENDPOINT=<your-ai-foundry-endpoint> \
      -e AZURE_OPENAI_DEPLOYMENT_NAME=<your-azure-openai-deployment-name> \
      -e AZURE_OPENAI_KEY=<your-azure-openai-key> \
      -e AZURE_OPENAI_ENDPOINT=<your-azure-openai-endpoint> \
      -e CLAIM_REV_AGENT_ID=<your-claim-reviewer-agent-id> \
      -e RISK_ANALYZER_AGENT_ID=<your-risk-analyzer-agent-id> \
      -e POLICY_CHECKER_AGENT_ID=<your-policy-checker-agent-id> \
      claim-manager:latest
   ```

   3. Test with curl:

   ```bash
   CLAIM_ID="CL001"
   POLICY_NUMBER="LIAB-AUTO-001"
   curl -sS -X POST "http://127.0.0.1:8000/process-claim"   -H "Content-Type: application/json"   -d "{\"claimId\":\"$CLAIM_ID\",\"policyNumber\":\"$POLICY_NUMBER\"}" | jq
   ```

## Push to Azure Container Registry

1. Tag the Docker image:

   ```bash
   ACR_NAME="<your-acr-name>"
   docker tag claim-manager:latest $ACR_NAME.azurecr.io/claim-manager:latest
   ```

2. Log in to Azure Container Registry:

   ```bash
   ACR_USERNAME="<your-username>"
   ACR_PASSWORD="<your-password>"
   docker login $ACR_NAME.azurecr.io --username $ACR_USERNAME --password $ACR_PASSWORD
   ```

3. Push the Docker image:

   ```bash
   docker push $ACR_NAME.azurecr.io/claim-manager:latest
   ```

## Run in Azure Container Apps


Create environment and container app using the pushed image and set the same environment variables as above.

1. Create the Container App environment:

   ```bash
   RESOURCE_GROUP="<your-resource-group>"
   LOCATION="<your-location>"
   ENV_NAME="<your-env-name>"
   az containerapp env create --name $ENV_NAME --resource-group $RESOURCE_GROUP --location $LOCATION
   ```

2. Create the Container App:

   ```bash
   APP_NAME="<your-app-name>"
   az containerapp create --name $APP_NAME --resource-group $RESOURCE_GROUP \
   --environment $ENV_NAME --image $ACR_NAME.azurecr.io/claim-manager:latest \
   --cpu 0.5 --memory 1.0Gi --min-replicas 1 --max-replicas 1 \
   --ingress 'external' --target-port 8000 \
   --registry-server $ACR_NAME.azurecr.io \
   --registry-username $ACR_USERNAME --registry-password $ACR_PASSWORD \
   --env-vars COSMOS_ENDPOINT="<your-cosmos-endpoint>" COSMOS_KEY="<your-cosmos-key>" \
   AI_FOUNDRY_PROJECT_ENDPOINT="<your-ai-foundry-endpoint>" AZURE_OPENAI_DEPLOYMENT_NAME="<your-azure-openai-deployment-name>" \
   AZURE_OPENAI_KEY="<your-azure-openai-key>" AZURE_OPENAI_ENDPOINT="<your-azure-openai-endpoint>" \
   CLAIM_REV_AGENT_ID="<your-claim-reviewer-agent-id>" \
   RISK_ANALYZER_AGENT_ID="<your-risk-analyzer-agent-id>" \
   POLICY_CHECKER_AGENT_ID="<your-policy-checker-agent-id>" 
   ```

   Give permissions to the container app to access resources using a system assigned managed identity:

   ```bash
   az containerapp identity assign \
   --name $APP_NAME \
   --resource-group $RESOURCE_GROUP \
   --system-assigned

   PRINCIPAL_ID=$(az containerapp identity show \
   --name $APP_NAME \
   --resource-group $RESOURCE_GROUP \
   --query principalId --output tsv)

   az role assignment create \
   --assignee $PRINCIPAL_ID \
   --role "Cognitive Services User" \
   --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
   ```


3. Test with curl:

   ```bash
   CLAIM_ID="CL001"
   POLICY_NUMBER="LIAB-AUTO-001"
   CLAIM_MANAGER_URL=$(az containerapp show --name $APP_NAME --resource-group $RESOURCE_GROUP --query properties.configuration.ingress.fqdn -o tsv)
   curl -sS -X POST "https://$CLAIM_MANAGER_URL/process-claim"   -H "Content-Type: application/json"   -d "{\"claimId\":\"$CLAIM_ID\",\"policyNumber\":\"$POLICY_NUMBER\"}" | jq
   ```