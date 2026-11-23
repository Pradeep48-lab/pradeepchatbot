param location string
param searchServiceName string

@description('Semantic ranker plan. "free" = 1,000 semantic queries/month; "standard" = billed per 1,000 queries after the free monthly quota. Basic tier or higher is required.')
@allowed([
  'disabled'
  'free'
  'standard'
])
param semanticSearch string = 'standard'

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
    // RBAC only: admin and query API keys are disabled, so every call must use
    // Microsoft Entra ID (the app, the indexer and GitHub Actions already do).
    // authOptions must be omitted when local auth is disabled.
    disableLocalAuth: true
    // Semantic ranker re-orders hybrid (keyword + vector) results and scores their relevance
    semanticSearch: semanticSearch
  }
}

output id string = search.id
output principalId string = search.identity.principalId
output endpoint string = 'https://${search.name}.search.windows.net'
