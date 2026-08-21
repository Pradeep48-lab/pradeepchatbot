targetScope = 'resourceGroup'

@description('Azure Region for all resources')
param location string

@description('Storage Account name for raw documents')
param storageAccountName string

@description('Storage container name')
param storageContainerName string = 'documents'

@description('Azure OpenAI Account name')
param openAiName string

@description('Azure AI Search service name')
param searchServiceName string

@description('Cosmos DB account name')
param cosmosAccountName string

@description('App Service Plan name')
param appServicePlanName string

@description('App Service Web App name')
param appServiceName string

@description('Entra ID App Registration Client ID for Easy Auth')
param entraClientId string

@description('Entra ID Tenant ID for Easy Auth')
param entraTenantId string = tenant().tenantId

module storage 'modules/storage.bicep' = {
  name: 'storageDeploy'
  params: {
    location: location
    storageAccountName: storageAccountName
    containerName: storageContainerName
  }
}

module openAi 'modules/openai.bicep' = {
  name: 'openAiDeploy'
  params: {
    location: location
    openAiName: openAiName
  }
}

module search 'modules/search.bicep' = {
  name: 'searchDeploy'
  params: {
    location: location
    searchServiceName: searchServiceName
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmosDeploy'
  params: {
    location: location
    accountName: cosmosAccountName
  }
}

module appService 'modules/appservice.bicep' = {
  name: 'appServiceDeploy'
  params: {
    location: location
    appServicePlanName: appServicePlanName
    appServiceName: appServiceName
    openAiEndpoint: openAi.outputs.endpoint
    chatDeployment: openAi.outputs.chatDeploymentName
    embeddingDeployment: openAi.outputs.embeddingDeploymentName
    searchEndpoint: search.outputs.endpoint
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosDbName: cosmos.outputs.databaseName
    cosmosContainer: cosmos.outputs.containerName
    entraClientId: entraClientId
    entraTenantId: entraTenantId
  }
}

module roles 'modules/roles.bicep' = {
  name: 'rolesDeploy'
  params: {
    appPrincipalId: appService.outputs.principalId
    searchPrincipalId: search.outputs.principalId
    storageAccountId: storage.outputs.id
    openAiAccountId: openAi.outputs.id
    searchAccountId: search.outputs.id
    cosmosAccountId: cosmos.outputs.id
  }
}

output appUrl string = 'https://${appService.outputs.defaultHostName}'
