param location string
param storageAccountName string
param storageContainerName string
param openAiName string
param searchServiceName string
param cosmosAccountName string
param appServicePlanName string
param appServiceName string
param entraClientId string
param entraTenantId string
param githubActionPrincipalId string

module storage 'modules/storage.bicep' = {
  name: 'storageDeploy'
  params: {
    location: location
    storageAccountName: storageAccountName
    storageContainerName: storageContainerName
  }
}

module openai 'modules/openai.bicep' = {
  name: 'openaiDeploy'
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
    cosmosAccountName: cosmosAccountName
  }
}

module appService 'modules/appservice.bicep' = {
  name: 'appServiceDeploy'
  params: {
    location: location
    appServicePlanName: appServicePlanName
    appServiceName: appServiceName
    entraClientId: entraClientId
    entraTenantId: entraTenantId
    openAiEndpoint: openai.outputs.endpoint
    chatDeployment: 'chat'
    embeddingDeployment: 'embedding'
    searchEndpoint: search.outputs.endpoint
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosDbName: 'chatdb'
    cosmosContainer: 'history'
  }
}

module roles 'modules/roles.bicep' = {
  name: 'rolesDeploy'
  params: {
    appPrincipalId: appService.outputs.principalId
    searchPrincipalId: search.outputs.principalId
    githubActionPrincipalId: githubActionPrincipalId
    storageAccountId: storage.outputs.id
    openAiAccountId: openai.outputs.id
    searchAccountId: search.outputs.id
    cosmosAccountId: cosmos.outputs.id
  }
}
