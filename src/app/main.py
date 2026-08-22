import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.cosmos import CosmosClient
from openai import AzureOpenAI

# 1. Initialize the FastAPI App
app = FastAPI(title="Azure MS Cloud Chatbot API")

# 2. Automatically grab the Managed Identity credentials
credential = DefaultAzureCredential()

# 3. Load Environment Variables (Injected automatically by Azure App Service)
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "chat")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "embedding")

AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "documents-index")

AZURE_COSMOS_ENDPOINT = os.environ.get("AZURE_COSMOS_ENDPOINT")
AZURE_COSMOS_DB_NAME = os.environ.get("AZURE_COSMOS_DB_NAME", "chatdb")
AZURE_COSMOS_CONTAINER_NAME = os.environ.get("AZURE_COSMOS_CONTAINER_NAME", "history")

# 4. Initialize Azure Clients
try:
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    openai_client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version="2024-02-01",
        azure_ad_token_provider=token_provider
    )
    
    search_client = SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX,
        credential=credential
    )
    
    cosmos_client = CosmosClient(
        url=AZURE_COSMOS_ENDPOINT, 
        credential=credential
    )
    cosmos_db = cosmos_client.get_database_client(AZURE_COSMOS_DB_NAME)
    history_container = cosmos_db.get_container_client(AZURE_COSMOS_CONTAINER_NAME)
    
except Exception as e:
    print(f"Error initializing clients: {e}")

# 5. Define the Data Models for the API
class ChatRequest(BaseModel):
    messages: list
    session_id: str = "default_session"

# 6. Create the API Endpoint with the RAG Engine
@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    user_message = request.messages[-1].get("content")
    if not user_message:
        raise HTTPException(status_code=400, detail="Message content is required.")

    try:
        # STEP 1: Generate Vector Embedding for the user's prompt
        embedding_response = openai_client.embeddings.create(
            input=user_message,
            model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT
        )
        query_vector = embedding_response.data[0].embedding

        # STEP 2: Search the Knowledge Base
        vector_query = VectorizedQuery(
            vector=query_vector,
            k_nearest_neighbors=3,
            fields="content_vector"
        )
        
        search_results = search_client.search(
            search_text=None,
            vector_queries=[vector_query],
            select=["title", "content"]
        )
        
        retrieved_context = "\n\n".join([f"Source: {doc['title']}\n{doc['content']}" for doc in search_results])

        # STEP 3: Construct the System Prompt
        system_prompt = f"""You are a helpful Azure customer support assistant. 
        Use ONLY the following context to answer the user's question. 
        If the answer is not in the context, politely say that you don't know and do not guess.
        
        Context:
        {retrieved_context}
        """

        # STEP 4: Generate the AI Response
        chat_messages = [{"role": "system", "content": system_prompt}] + request.messages
        
        chat_response = openai_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT,
            messages=chat_messages,
            temperature=0.3
        )
        
        ai_message = chat_response.choices[0].message.content

        # STEP 5: Save the Conversation Turn to Cosmos DB
        chat_record = {
            "id": str(uuid.uuid4()),
            "sessionId": request.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_message": user_message,
            "ai_message": ai_message
        }
        history_container.create_item(body=chat_record)

        return {
            "message": {
                "role": "assistant",
                "content": ai_message
            },
            "context": retrieved_context 
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"status": "healthy", "service": "Azure MS Cloud Chatbot API"}