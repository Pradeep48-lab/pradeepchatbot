/*
  infra/modules/contentsafety.bicep

  Azure AI Content Safety, used by the app for Prompt Shields:
  - checks each user question for jailbreak attempts
  - checks retrieved documents for hidden instructions (indirect prompt injection)

  Keys are disabled; the App Service calls it with its managed identity
  (Cognitive Services User role, assigned in roles.bicep).
  S0 is billed per text record analysed; see https://aka.ms/content-safety-pricing
*/

param location string
param contentSafetyName string

@allowed([
  'F0'
  'S0'
])
param sku string = 'S0'

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: contentSafetyName
  location: location
  kind: 'ContentSafety'
  sku: {
    name: sku
  }
  properties: {
    customSubDomainName: contentSafetyName // required for Entra ID authentication
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

output id string = contentSafety.id
output name string = contentSafety.name
output endpoint string = contentSafety.properties.endpoint
