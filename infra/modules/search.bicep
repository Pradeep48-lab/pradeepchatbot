param location string
param searchServiceName string

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    publicNetworkAccess: 'enabled'
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'free'
  }
}

output id string = search.id
output principalId string = search.identity.principalId
output endpoint string = 'https://${search.name}.search.windows.net'
