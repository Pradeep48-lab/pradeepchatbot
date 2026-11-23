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

@description('Deploy Azure AI Content Safety and turn on Prompt Shields in the app.')
param enablePromptShields bool = true

@description('Name (and subdomain) of the Content Safety resource. Must be globally unique.')
param contentSafetyName string = '${openAiName}-safety'

@description('Semantic ranker plan for AI Search: disabled, free (1,000 queries/month) or standard.')
@allowed([
  'disabled'
  'free'
  'standard'
])
param semanticSearch string = 'standard'

module contentSafety 'modules/contentsafety.bicep' = if (enablePromptShields) {
  name: 'contentSafetyDeploy'
  params: {
    location: location
    contentSafetyName: contentSafetyName
  }
}

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
    semanticSearch: semanticSearch
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
    contentSafetyEndpoint: enablePromptShields ? contentSafety!.outputs.endpoint : ''
    // setup_search.py points the indexer at the 'documents' container
    storageAccountUrl: 'https://${storageAccountName}.blob.${environment().suffixes.storage}'
    storageContainerName: 'documents'
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
    contentSafetyAccountId: enablePromptShields ? contentSafety!.outputs.id : ''
  }
}
