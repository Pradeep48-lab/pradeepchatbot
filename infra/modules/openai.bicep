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
    name: 'GlobalStandard' // <-- Changed from Standard to GlobalStandard
    capacity: 20 
  }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-5.5', version: '2026-04-24' }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: 'embedding'
  sku: { 
    name: 'GlobalStandard' // <-- Changed from Standard to GlobalStandard
    capacity: 20 
  }
  properties: {
    model: { format: 'OpenAI', name: 'text-embedding-3-large', version: '1' }
  }
  dependsOn: [ chatDeployment ]
}

output id string = openAi.id
output endpoint string = openAi.properties.endpoint
output chatDeploymentName string = chatDeployment.name
output embeddingDeploymentName string = embeddingDeployment.name
