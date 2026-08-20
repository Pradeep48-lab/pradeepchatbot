resource storageaccount 'Microsoft.Storage/storageAccounts@2026-04-01' = {
  name: 'pradeepstoragetest'
  kind: 'StorageV2'
  location:'EastUS'
  sku:{
    name: 'Standard_LRS'
  }
  
}
