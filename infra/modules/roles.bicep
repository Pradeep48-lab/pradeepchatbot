param appPrincipalId string
param searchPrincipalId string
param storageAccountId string
param openAiAccountId string
param searchAccountId string
param cosmosAccountId string

var roleCognitiveServicesOpenAIUser = '5e0bd9bd-7b91-4f2e-ab4a-3d97858e0dec'
var roleSearchIndexDataReader = '1407120a-a4aa-4278-889e-ab20585c4513'
var roleStorageBlobDataReader = '2a2b9908-6ea1-492a-b753-b2215406b747'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource appOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appPrincipalId, openAiAccountId, roleCognitiveServicesOpenAIUser)
  properties: {
    principalId: appPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAIUser)
    principalType: 'ServicePrincipal'
  }
}

resource appSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appPrincipalId, searchAccountId, roleSearchIndexDataReader)
  properties: {
    principalId: appPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataReader)
    principalType: 'ServicePrincipal'
  }
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: last(split(cosmosAccountId, '/'))
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

resource searchStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchPrincipalId, storageAccountId, roleStorageBlobDataReader)
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
    principalType: 'ServicePrincipal'
  }
}

resource searchOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchPrincipalId, openAiAccountId, roleCognitiveServicesOpenAIUser)
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAIUser)
    principalType: 'ServicePrincipal'
  }
}
