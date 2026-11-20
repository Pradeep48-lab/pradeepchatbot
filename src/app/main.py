import asyncio
import base64
import json
import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.cosmos import CosmosClient
from openai import AzureOpenAI

from learn_search import search_learn

# 1. Initialize the FastAPI App
app = FastAPI(title="Azure MS Cloud Chatbot API")

# 2. Automatically grab the Managed Identity credentials
credential = DefaultAzureCredential()

# 3. Load Environment Variables
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "chat")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "embedding")

AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "documents-index")

AZURE_COSMOS_ENDPOINT = os.environ.get("AZURE_COSMOS_ENDPOINT")
AZURE_COSMOS_DB_NAME = os.environ.get("AZURE_COSMOS_DB_NAME", "chatdb")
AZURE_COSMOS_CONTAINER_NAME = os.environ.get("AZURE_COSMOS_CONTAINER_NAME", "history")

# Microsoft Learn search (public Learn MCP server). Set to "false" to turn off.
ENABLE_LEARN_SEARCH = os.environ.get("ENABLE_LEARN_SEARCH", "true").lower() == "true"

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


# 5. Extract Authenticated User Identity from Azure Easy Auth Headers
def get_current_user(request: Request) -> tuple[str, str]:
    user_id = request.headers.get("x-ms-client-principal-id")
    user_name = request.headers.get("x-ms-client-principal-name", "")

    # Fallback for Linux App Service: decode Base64 X-MS-CLIENT-PRINCIPAL claims
    if not user_id:
        principal_header = request.headers.get("x-ms-client-principal")
        if principal_header:
            try:
                decoded = json.loads(base64.b64decode(principal_header).decode("utf-8"))
                claims = {c.get("typ"): c.get("val") for c in decoded.get("claims", [])}
                user_id = (
                    claims.get("http://schemas.microsoft.com/identity/claims/objectidentifier")
                    or claims.get("oid")
                    or claims.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier")
                    or claims.get("sub")
                )
                if not user_name:
                    user_name = (
                        claims.get("preferred_username")
                        or claims.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
                        or claims.get("name", "")
                    )
            except Exception as e:
                print(f"Failed to decode X-MS-CLIENT-PRINCIPAL: {e}")

    if not user_id:
        raise HTTPException(status_code=401, detail="Authenticated user identity not found.")

    return user_id, user_name


# 6. Serve the Static UI
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_ui():
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, headers={"Cache-Control": "no-store"})
    return {"status": "healthy", "service": "Azure MS Cloud Chatbot API"}


# Internal knowledge base search (runs in a worker thread; the SDK calls are blocking)
def search_internal_kb(question: str) -> list[dict]:
    embedding_response = openai_client.embeddings.create(
        input=question,
        model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    )
    vector_query = VectorizedQuery(
        vector=embedding_response.data[0].embedding,
        k_nearest_neighbors=3,
        fields="content_vector"
    )
    results = search_client.search(
        search_text=None,
        vector_queries=[vector_query],
        select=["title", "content"]
    )
    return [{"title": doc["title"], "content": doc["content"]} for doc in results]


# 7. Data Models
class ChatRequest(BaseModel):
    messages: list
    session_id: str = "default_session"


# 8. Chat Endpoint (RAG Logic + Per-User Persistence)
@app.post("/chat")
async def chat_endpoint(payload: ChatRequest, request: Request):
    user_id, user_name = get_current_user(request)
    if not payload.messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty.")

    user_message = payload.messages[-1].get("content")
    if not user_message:
        raise HTTPException(status_code=400, detail="Message content is required.")

    try:
        # STEPS 1-2: Search the internal knowledge base and Microsoft Learn in parallel
        learn_task = (
            search_learn(user_message) if ENABLE_LEARN_SEARCH else asyncio.sleep(0, result=[])
        )
        internal_docs, learn_docs = await asyncio.gather(
            asyncio.to_thread(search_internal_kb, user_message),
            learn_task,
        )

        internal_context = "\n\n".join(
            f"Source: {doc['title']}\n{doc['content']}" for doc in internal_docs
        )
        learn_context = "\n\n".join(
            f"Source: {doc['title']} ({doc['url']})\n{doc['content']}" for doc in learn_docs
        )

        # Clickable Learn articles shown under the answer
        learn_links = [
            {"title": doc["title"], "url": doc["url"]}
            for doc in learn_docs
            if doc.get("url", "").startswith("https://learn.microsoft.com/")
        ]

        context_sections = []
        if internal_context:
            context_sections.append(f"=== INTERNAL KNOWLEDGE BASE ===\n{internal_context}")
        if learn_context:
            context_sections.append(f"=== MICROSOFT LEARN ===\n{learn_context}")
        retrieved_context = "\n\n".join(context_sections)

        # STEP 3: Construct System Prompt
        system_prompt = f"""You are a helpful Azure customer support assistant.
        Use ONLY the following context to answer the user's question.
        The context has two kinds of sources:
        - INTERNAL KNOWLEDGE BASE: our own documents. Prefer these for company-specific
          processes, past tickets and internal guidance.
        - MICROSOFT LEARN: official Microsoft documentation. Use these for product behaviour,
          limits and how-to steps, and include the article URL when you use one.
        If the sources disagree, say so and explain both.
        If the answer is not in the context, politely say that you don't know and do not guess.

        Context:
        {retrieved_context or "(no matching sources found)"}
        """

        # STEP 4: Generate Completion
        chat_messages = [{"role": "system", "content": system_prompt}] + payload.messages

        chat_response = openai_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT,
            messages=chat_messages,
            temperature=0.3
        )

        ai_message = chat_response.choices[0].message.content

        # STEP 5: Persist to Cosmos DB tagged with the logged-in user's Entra ID
        chat_record = {
            "id": str(uuid.uuid4()),
            "sessionId": payload.session_id,
            "userId": user_id,
            "userName": user_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_message": user_message,
            "ai_message": ai_message,
            "context": retrieved_context,
            "learn_links": learn_links
        }
        history_container.create_item(body=chat_record)

        return {
            "message": {
                "role": "assistant",
                "content": ai_message
            },
            "context": retrieved_context,
            "learn_links": learn_links
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 9. History Endpoint (Unique Sessions Grouped per User)
@app.get("/history")
async def get_history(request: Request):
    user_id, _ = get_current_user(request)
    try:
        query = (
            "SELECT c.sessionId, c.user_message, c.timestamp, c._ts "
            "FROM c WHERE c.userId = @user_id"
        )
        parameters = [{"name": "@user_id", "value": user_id}]
        items = list(history_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        # Process oldest-to-newest so each chat keeps its first question as the title
        items.sort(key=lambda x: x.get("_ts", 0))

        sessions = {}
        for item in items:
            sid = item.get("sessionId")
            if not sid:
                continue
            if sid not in sessions:
                sessions[sid] = {
                    "sessionId": sid,
                    "user_message": item.get("user_message", "New Chat"),
                    "timestamp": item.get("timestamp", ""),
                    "_ts": item.get("_ts", 0)
                }
            else:
                sessions[sid]["timestamp"] = item.get("timestamp", sessions[sid]["timestamp"])
                sessions[sid]["_ts"] = max(sessions[sid]["_ts"], item.get("_ts", 0))

        # Return the 20 most recently active unique chats
        sorted_sessions = sorted(sessions.values(), key=lambda x: x["_ts"], reverse=True)[:20]
        return [
            {
                "sessionId": s["sessionId"],
                "user_message": s["user_message"],
                "timestamp": s["timestamp"]
            }
            for s in sorted_sessions
        ]
    except Exception as e:
        print(f"Error loading history: {e}")
        return []


# 10. Load Specific Session Endpoint (Restricted to the Logged-In User)
@app.get("/history/{session_id}")
async def get_session_history(session_id: str, request: Request):
    user_id, _ = get_current_user(request)
    try:
        query = (
            "SELECT * FROM c "
            "WHERE c.sessionId = @session_id AND c.userId = @user_id"
        )
        parameters = [
            {"name": "@session_id", "value": session_id},
            {"name": "@user_id", "value": user_id}
        ]
        items = list(history_container.query_items(
            query=query,
            parameters=parameters,
            partition_key=session_id
        ))
        items.sort(key=lambda x: x.get("_ts", 0))
        return items
    except Exception as e:
        print(f"Error loading session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 11. Delete a Chat Thread (only the logged-in user's own messages in that session)
@app.delete("/history/{session_id}")
async def delete_session_history(session_id: str, request: Request):
    user_id, _ = get_current_user(request)
    try:
        items = list(history_container.query_items(
            query="SELECT c.id FROM c WHERE c.sessionId = @session_id AND c.userId = @user_id",
            parameters=[
                {"name": "@session_id", "value": session_id},
                {"name": "@user_id", "value": user_id}
            ],
            partition_key=session_id
        ))
        if not items:
            raise HTTPException(status_code=404, detail="Chat not found.")

        for item in items:
            history_container.delete_item(item=item["id"], partition_key=session_id)

        return {"deleted": len(items), "sessionId": session_id}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
