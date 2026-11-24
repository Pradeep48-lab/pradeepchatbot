import os
from datetime import datetime, time, timedelta, timezone
from azure.core.exceptions import ResourceExistsError
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
    SearchIndexerDataSourceConnection,
    SearchIndexerDataContainer,
    SplitSkill,
    AzureOpenAIEmbeddingSkill,
    SearchIndexerSkillset,
    SearchIndexer,
    IndexingSchedule,
    InputFieldMappingEntry,
    IndexProjectionMode,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerIndexProjectionSelector,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
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
        SearchableField(
            name="chunk_id",
            type=SearchFieldDataType.String,
            key=True,
            analyzer_name="keyword",
            sortable=True,
            filterable=True,
            facetable=True
        ),
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
        profiles=[
            VectorSearchProfile(
                name="my-vector-profile",
                algorithm_configuration_name="my-hnsw",
                vectorizer_name="my-openai"
            )
        ],
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name="my-openai",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=openai_endpoint,
                    deployment_name=openai_embedding_deployment,
                    model_name="text-embedding-3-large"
                )
            )
        ]
    )

    # Semantic ranker configuration used by the app's hybrid search (query_type="semantic").
    # Adding it to an existing index doesn't require re-indexing.
    semantic_search = SemanticSearch(
        default_configuration_name="default",
        configurations=[
            SemanticConfiguration(
                name="default",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ],
    )

    index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search, semantic_search=semantic_search)
    index_client.create_or_update_index(index)

    print("3. Creating Skillset (Chunking & Embedding)...")
    split_skill = SplitSkill(
        name="Splitter",
        context="/document",
        text_split_mode="pages",
        maximum_page_length=2000,
        page_overlap_length=500,
        inputs=[{"name": "text", "source": "/document/content"}],
        outputs=[{"name": "textItems", "targetName": "pages"}]
    )

    embedding_skill = AzureOpenAIEmbeddingSkill(
        name="Embedder",
        context="/document/pages/*",
        resource_url=openai_endpoint,
        deployment_name=openai_embedding_deployment,
        model_name="text-embedding-3-large",
        dimensions=3072,
        inputs=[{"name": "text", "source": "/document/pages/*"}],
        outputs=[{"name": "embedding", "targetName": "vector"}]
    )

    skillset = SearchIndexerSkillset(
        name="document-skillset",
        description="Split and vectorize",
        skills=[split_skill, embedding_skill],
        index_projection=SearchIndexerIndexProjection(
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
            parameters=SearchIndexerIndexProjectionsParameters(
                projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
            )
        )
    )
    indexer_client.create_or_update_skillset(skillset)

    print("4. Creating and Scheduling Indexer (Daily at 12:00 AM IST)...")
    # Anchors the daily schedule to 12:00 AM IST (18:30 UTC).
    # Replace `ist` with `timezone.utc` if you want 12:00 AM UTC instead.
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    next_midnight_utc = datetime.combine(
        now_ist.date() + timedelta(days=1),
        time.min,
        tzinfo=ist
    ).astimezone(timezone.utc)

    indexer = SearchIndexer(
        name="document-indexer",
        data_source_name="blob-datasource",
        target_index_name=index_name,
        skillset_name="document-skillset",
        schedule=IndexingSchedule(
            interval=timedelta(days=1),
            start_time=next_midnight_utc
        )
    )
    indexer_client.create_or_update_indexer(indexer)

    # Trigger an immediate run once setup finishes. Azure already starts a run when the indexer is
    # first created (or while the scheduled run is going), and only one run can happen at a time,
    # so "already running" is fine: the new files are picked up by that run.
    try:
        indexer_client.run_indexer("document-indexer")
        print("   Indexer run started.")
    except ResourceExistsError:
        print("   Indexer is already running (started automatically); it will pick up the files. Nothing else to do.")
    print(f"Setup complete! Indexer scheduled daily at 12:00 AM IST (next run: {next_midnight_utc.isoformat()}).")


if __name__ == "__main__":
    setup_search_pipeline()
