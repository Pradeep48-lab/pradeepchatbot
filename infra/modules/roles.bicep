param appPrincipalId string
param searchPrincipalId string
param githubActionPrincipalId string
param storageAccountId string
param openAiAccountId string
param searchAccountId string
param cosmosAccountId string

var roleCognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var roleSearchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var roleStorageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var roleSearchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var roleSearchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(storageAccountId, '/'))
}
resource openAiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: last(split(openAiAccountId, '/'))
}
resource searchAccount 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: last(split(searchAccountId, '/'))
}
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: last(split(cosmosAccountId, '/'))
}

// 1. APP SERVICE ROLES
resource appOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appPrincipalId, openAiAccountId, roleCognitiveServicesOpenAIUser)
  scope: openAiAccount
  properties: {
    principalId: appPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAIUser)
    principalType: 'ServicePrincipal'
  }
}
resource appSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appPrincipalId, searchAccountId, roleSearchIndexDataReader)
  scope: searchAccount
  properties: {
    principalId: appPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataReader)
    principalType: 'ServicePrincipal'
  }
}
resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(appPrincipalId, cosmosAccountId, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccountId}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: appPrincipalId
    scope: cosmosAccountId
  }
}

// 2. AI SEARCH ROLES
resource searchStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchPrincipalId, storageAccountId, roleStorageBlobDataReader)
  scope: storageAccount
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
    principalType: 'ServicePrincipal'
  }
}
resource searchOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchPrincipalId, openAiAccountId, roleCognitiveServicesOpenAIUser)
  scope: openAiAccount
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAIUser)
    principalType: 'ServicePrincipal'
  }
}

// 3. GITHUB ACTIONS ROLES
resource githubSearchServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(githubActionPrincipalId, searchAccountId, roleSearchServiceContributor)
  scope: searchAccount
  properties: {
    principalId: githubActionPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchServiceContributor)
    principalType: 'ServicePrincipal'
  }
}
resource githubSearchDataRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(githubActionPrincipalId, searchAccountId, roleSearchIndexDataContributor)
  scope: searchAccount
  properties: {
    principalId: githubActionPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataContributor)
    principalType: 'ServicePrincipal'
  }
}
