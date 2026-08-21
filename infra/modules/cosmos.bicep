param location string
param accountName string

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [ { locationName: location, failoverPriority: 0, isZoneRedundant: false } ]
    capabilities: [ { name: 'EnableServerless' } ]
    publicNetworkAccess: 'Enabled'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'ChatHistoryDB'
  properties: { resource: { id: 'ChatHistoryDB' } }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'Conversations'
  properties: {
    resource: {
      id: 'Conversations'
      partitionKey: { paths: [ '/sessionId' ], kind: 'Hash' }
    }
  }
}

output id string = cosmosAccount.id
output endpoint string = cosmosAccount.properties.documentEndpoint
output databaseName string = database.name
output containerName string = container.name
