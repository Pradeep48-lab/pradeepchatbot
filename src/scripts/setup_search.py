import os
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    SearchIndexerDataIdentity,
    SearchIndexerDataSourceConnection,
    SearchIndexerDataContainer,
    SplitSkill,
    AzureOpenAIEmbeddingSkill,
    SearchIndexerSkillset,
    SearchIndexer,
    InputFieldMappingEntry,
    IndexProjectionMode,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerIndexProjectionSelector
)

def setup_search_pipeline():
    search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    storage_resource_id = os.environ["AZURE_STORAGE_RESOURCE_ID"]
    openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    openai_embedding_deployment = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]
    
    index_name = "documents-index"
    
    credential = DefaultAzureCredential()
    index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)
    indexer_client = SearchIndexerClient(endpoint=search_endpoint, credential=credential)

    # Reusable Managed Identity object for the SDK
    managed_identity = SearchIndexerDataIdentity(odata_type="#Microsoft.Azure.Search.ManagedServiceIdentity")

    print("1. Creating Data Source...")
    ds_conn = SearchIndexerDataSourceConnection(
        name="blob-datasource",
        type="azureblob",
        connection_string=f"ResourceId={storage_resource_id};",
        container=SearchIndexerDataContainer(name="documents")
    )
    indexer_client.create_or_update_data_source_connection(ds_conn)

    print("2. Creating Index...")
    fields = [
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True, sortable=True, filterable=True, facetable=True),
        SimpleField(name="parent_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector", 
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True, 
            vector_search_dimensions=3072, 
            vector_search_profile_name="my-vector-profile"
        )
    ]
    
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="my-hnsw")],
        profiles=[VectorSearchProfile(name="my-vector-profile", algorithm_configuration_name="my-hnsw", vectorizer_name="my-openai")],
        vectorizers=[AzureOpenAIVectorizer(
            vectorizer_name="my-openai",
            parameters=AzureOpenAIVectorizerParameters(
                resource_url=openai_endpoint,                   # FIXED: Renamed from resource_uri
                deployment_name=openai_embedding_deployment,    # FIXED: Renamed from deployment_id
                auth_identity=managed_identity                  # FIXED: Ensures user queries use Managed Identity
            )
        )]
    )
    
    index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
    index_client.create_or_update_index(index)

    print("3. Creating Skillset (Chunking & Embedding)...")
    split_skill = SplitSkill(
        name="Splitter",
        text_split_mode="pages",
        maximum_page_length=2000,
        page_overlapping_length=500,
        inputs=[{"name": "text", "source": "/document/content"}],
        outputs=[{"name": "textItems", "targetName": "pages"}]
    )
    
    embedding_skill = AzureOpenAIEmbeddingSkill(
        name="Embedder",
        resource_url=openai_endpoint,                   # FIXED: Renamed from resource_uri
        deployment_name=openai_embedding_deployment,    # FIXED: Renamed from deployment_id
        dimensions=3072,
        inputs=[{"name": "text", "source": "/document/pages/*"}],
        outputs=[{"name": "embedding", "targetName": "vector"}],
        auth_identity=managed_identity                  # FIXED: Uses correct Managed Identity object
    )

    skillset = SearchIndexerSkillset(
        name="document-skillset",
        description="Split and vectorize",
        skills=[split_skill, embedding_skill],
        index_projections=SearchIndexerIndexProjection(
            selectors=[
                SearchIndexerIndexProjectionSelector(
                    target_index_name=index_name,
                    parent_key_field_name="parent_id",
                    source_context="/document/pages/*",
                    mappings=[
                        InputFieldMappingEntry(name="content", source="/document/pages/*"),
                        InputFieldMappingEntry(name="content_vector", source="/document/pages/*/vector"),
                        InputFieldMappingEntry(name="title", source="/document/metadata_storage_name")
                    ]
                )
            ],
            parameters=SearchIndexerIndexProjectionsParameters(projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS)
        )
    )
    indexer_client.create_or_update_skillset(skillset)

    print("4. Creating and Running Indexer...")
    indexer = SearchIndexer(
        name="document-indexer",
        data_source_name="blob-datasource",
        target_index_name=index_name,
        skillset_name="document-skillset"
    )
    indexer_client.create_or_update_indexer(indexer)
    
    indexer_client.run_indexer("document-indexer")
    print("Setup complete! Indexer is now processing documents.")

if __name__ == "__main__":
    setup_search_pipeline()