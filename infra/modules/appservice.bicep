param location string
param appServicePlanName string
param appServiceName string
param openAiEndpoint string
param chatDeployment string
param embeddingDeployment string
param searchEndpoint string
param cosmosEndpoint string
param cosmosDbName string
param cosmosContainer string
param entraClientId string
param entraTenantId string

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  sku: { name: 'B1', tier: 'Basic' }
  kind: 'linux'
  properties: { reserved: true }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: appServiceName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
        { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: chatDeployment }
        { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: embeddingDeployment }
        { name: 'AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
        { name: 'AZURE_COSMOS_ENDPOINT', value: cosmosEndpoint }
        { name: 'AZURE_COSMOS_DATABASE', value: cosmosDbName }
        { name: 'AZURE_COSMOS_CONTAINER', value: cosmosContainer }
      ]
    }
  }
}

resource authSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: webApp
  name: 'authsettingsV2'
  properties: {
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          // FIXED: Removed the hardcoded login.microsoftonline.com URL
          openIdIssuer: '${environment().authentication.loginEndpoint}${entraTenantId}/v2.0'
        }
      }
    }
  }
}

output principalId string = webApp.identity.principalId
output defaultHostName string = webApp.properties.defaultHostName
