param location string
param searchServiceName string

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    // THIS STOPS BICEP FROM OVERWRITING YOUR SETTING
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

output id string = search.id
output principalId string = search.identity.principalId
output endpoint string = 'https://${search.name}.search.windows.net'
