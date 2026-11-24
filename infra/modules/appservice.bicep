/*
  infra/modules/appservice.bicep

  REQUIRED RBAC ROLES:
  1. Deployment Principal (GitHub Actions Service Principal) — Resource Group Scope (Manual Prerequisite):
     - Contributor: Provisions and updates all Azure resources.
     - User Access Administrator (or Role Based Access Control Administrator):
       Grants Microsoft.Authorization/roleAssignments/write so infra/modules/roles.bicep
       can assign Managed Identity permissions without AuthorizationFailed errors.

  2. App Service System-Assigned Managed Identity — Assigned Automatically via roles.bicep / cosmos.bicep:
     - Cognitive Services OpenAI User (on Azure OpenAI)
     - Search Index Data Reader (on Azure AI Search)
     - Cosmos DB Built-in Data Contributor (on Azure Cosmos DB, Role ID: 00000000-0000-0000-0000-000000000002)
*/

param location string = resourceGroup().location
param appServicePlanName string
param appServiceName string
param openAiEndpoint string
param chatDeployment string = 'chat'
@description('Fast model deployment for follow-up rewriting and conversation summaries.')
param fastDeployment string = 'chat-mini'
param embeddingDeployment string = 'embedding'
param searchEndpoint string
param cosmosEndpoint string
param cosmosDbName string = 'chatdb'
param cosmosContainer string = 'history'
param entraClientId string
param entraTenantId string

@description('Azure AI Content Safety endpoint for Prompt Shields. Empty turns Prompt Shields off.')
param contentSafetyEndpoint string = ''

@description('Blob endpoint of the storage account that holds the source documents, e.g. https://<name>.blob.core.windows.net')
param storageAccountUrl string

@description('Container the indexer reads; references open files from here.')
param storageContainerName string = 'documents'

param rateLimitPerMinute int = 20
param rateLimitPerDay int = 500

resource appServicePlan 'Microsoft.Web/serverfarms@2022-09-01' = {
  name: appServicePlanName
  location: location
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource appServiceApp 'Microsoft.Web/sites@2022-09-01' = {
  name: appServiceName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      ftpsState: 'Disabled'
      alwaysOn: true
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      http20Enabled: true
      remoteDebuggingEnabled: false
      appSettings: [
        {
          name: 'AZURE_CONTENT_SAFETY_ENDPOINT'
          value: contentSafetyEndpoint
        }
        {
          name: 'AZURE_STORAGE_ACCOUNT_URL'
          value: storageAccountUrl
        }
        {
          name: 'AZURE_STORAGE_CONTAINER'
          value: storageContainerName
        }
        {
          name: 'RATE_LIMIT_PER_MINUTE'
          value: string(rateLimitPerMinute)
        }
        {
          name: 'RATE_LIMIT_PER_DAY'
          value: string(rateLimitPerDay)
        }
        // Ensures Azure Oryx runs `pip install -r requirements.txt` during zip deployments.
        // Do NOT set WEBSITE_RUN_FROM_PACKAGE = '1' alongside this setting.
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAiEndpoint
        }
        {
          name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
          value: chatDeployment
        }
        {
          name: 'AZURE_OPENAI_FAST_DEPLOYMENT'
          value: fastDeployment
        }
        {
          name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
          value: embeddingDeployment
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: searchEndpoint
        }
        {
          name: 'AZURE_SEARCH_INDEX'
          value: 'documents-index'
        }
        {
          name: 'AZURE_COSMOS_ENDPOINT'
          value: cosmosEndpoint
        }
        {
          name: 'AZURE_COSMOS_DB_NAME'
          value: cosmosDbName
        }
        {
          name: 'AZURE_COSMOS_CONTAINER_NAME'
          value: cosmosContainer
        }
      ]
    }
  }
}

// Turn off FTP and Kudu (SCM) username/password publishing credentials.
// GitHub Actions deploys with OIDC (azure/login) and is unaffected.
resource ftpBasicAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2022-09-01' = {
  parent: appServiceApp
  name: 'ftp'
  properties: {
    allow: false
  }
}

resource scmBasicAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2022-09-01' = {
  parent: appServiceApp
  name: 'scm'
  properties: {
    allow: false
  }
}

resource authSettings 'Microsoft.Web/sites/config@2022-09-01' = {
  name: 'authsettingsV2'
  parent: appServiceApp
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureActiveDirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          openIdIssuer: uri(environment().authentication.loginEndpoint, '${entraTenantId}/v2.0')
        }
      }
    }
  }
}

output appServiceId string = appServiceApp.id
output appServiceName string = appServiceApp.name
output appServiceUrl string = 'https://${appServiceApp.properties.defaultHostName}'
output principalId string = appServiceApp.identity.principalId
