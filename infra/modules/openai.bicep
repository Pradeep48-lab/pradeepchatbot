param location string
param openAiName string

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: openAiName
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: 'chat'
  sku: { 
    name: 'GlobalStandard' 
    capacity: 20 
  }
  properties: {
    model: { 
      format: 'OpenAI'
      name: 'gpt-4.1' 
      version: '2025-04-14' 
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: 'embedding'
  sku: { 
    name: 'GlobalStandard' 
    capacity: 20 
  }
  properties: {
    model: { 
      format: 'OpenAI'
      name: 'text-embedding-3-large' 
      version: '1' 
    }
  }
  dependsOn: [ chatDeployment ]
}

output id string = openAi.id
output endpoint string = openAi.properties.endpoint
output chatDeploymentName string = chatDeployment.name
output embeddingDeploymentName string = embeddingDeployment.name
