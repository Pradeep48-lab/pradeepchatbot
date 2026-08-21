param appPrincipalId string
param searchPrincipalId string
param storageAccountId string
param openAiAccountId string
param searchAccountId string
param cosmosAccountId string

// CORRECTED GUIDs
var roleCognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var roleSearchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var roleStorageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

// 1. Reference the existing resources to use as scopes
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

// 2. App Service -> OpenAI User (Scoped strictly to the OpenAI Account)
resource appOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appPrincipalId, openAiAccountId, roleCognitiveServicesOpenAIUser)
  scope: openAiAccount
  properties: {
    principalId: appPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAIUser)
    principalType: 'ServicePrincipal'
  }
}

// 3. App Service -> Search Index Reader (Scoped strictly to the Search Account)
resource appSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appPrincipalId, searchAccountId, roleSearchIndexDataReader)
  scope: searchAccount
  properties: {
    principalId: appPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataReader)
    principalType: 'ServicePrincipal'
  }
}

// 4. App Service -> Cosmos DB Data Plane (Scoped strictly to Cosmos DB)
resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(appPrincipalId, cosmosAccountId, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccountId}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: appPrincipalId
    scope: cosmosAccountId
  }
}

// 5. AI Search -> Storage Reader (Scoped strictly to the Storage Account)
resource searchStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchPrincipalId, storageAccountId, roleStorageBlobDataReader)
  scope: storageAccount
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
    principalType: 'ServicePrincipal'
  }
}

// 6. AI Search -> OpenAI User (Scoped strictly to the OpenAI Account)
resource searchOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchPrincipalId, openAiAccountId, roleCognitiveServicesOpenAIUser)
  scope: openAiAccount
  properties: {
    principalId: searchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAIUser)
    principalType: 'ServicePrincipal'
  }
}
