# Azure MS Cloud RAG Chatbot

A Zero Trust, keyless Retrieval-Augmented Generation (RAG) chatbot hosted on Microsoft Azure with Microsoft Entra ID SSO, automated daily document vectorization, per-user persistent chat history, and a dynamic web UI.

## Architecture Overview

- **Zero Trust & Keyless Security:** Uses Azure System-Assigned Managed Identities (`DefaultAzureCredential`) for service-to-service access and GitHub Actions OIDC for deployments—no API keys, connection strings, or `.env` files.
- **Decoupled RAG Orchestrator:** FastAPI executes vector search directly via the Azure AI Search SDK (`VectorizedQuery`) and injects retrieved chunks into standard Azure OpenAI Chat Completions, avoiding the retired Azure OpenAI On Your Data `data_sources` parameter.
- **Integrated Vectorization:** `src/scripts/setup_search.py` configures Azure AI Search (`SplitSkill` + `AzureOpenAIEmbeddingSkill` using `text-embedding-3-large`) to automatically chunk and embed documents on a daily **12:00 AM IST** schedule.
- **Per-User SSO Isolation:** FastAPI reads App Service Easy Auth headers (`x-ms-client-principal-id` with a Base64 `x-ms-client-principal` claims fallback) so every signed-in user sees only their own grouped conversation history and citations.
- **Single-Host Deployment:** A single Linux App Service hosts both the FastAPI backend and the static Tailwind CSS UI (`index.html` + `logo.png`), eliminating CORS setup and inheriting Entra ID SSO natively.

### Core Azure Components

| Service | Configuration | Purpose |
| :--- | :--- | :--- |
| **Azure Storage Account** | Blob container: `documents` | Stores raw knowledge base files (`.txt`, `.pdf`, `.docx`). |
| **Azure OpenAI** | Deployments: `chat` (GPT-4), `embedding` (`text-embedding-3-large`, 3072 dims) | Generates query/document embeddings and grounded answers. |
| **Azure AI Search** | `documents-index`, `blob-datasource`, `document-skillset`, `document-indexer` | Automated chunking, vectorization, and vector search. |
| **Azure Cosmos DB (NoSQL)** | DB: `chatdb`, Container: `history`, Partition Key: `/sessionId` | Persists per-user chat turns (`userId`, `sessionId`, `context`). |
| **Azure App Service (Linux)** | Python 3.11, Basic B1+, Entra ID Easy Auth (`authsettingsV2`) | Runs FastAPI + static frontend with Oryx builds enabled. |

## Repository Structure

```text
.
├── .github/workflows/
│   ├── deploy.yml              # Workflow 1: Bicep infrastructure + AI Search pipeline
│   └── deploy_app.yml          # Workflow 2: FastAPI & static UI zip deployment
├── infra/
│   ├── main.bicep              # Root Bicep orchestrator
│   └── modules/
│       ├── appservice.bicep    # Linux Web App, Oryx settings, Easy Auth (authsettingsV2)
│       ├── cosmos.bicep        # Cosmos DB account, database, container, data-plane RBAC
│       ├── openai.bicep        # Azure OpenAI account + chat & embedding deployments
│       ├── roles.bicep         # Automated Managed Identity & SP RBAC assignments
│       ├── search.bicep        # Azure AI Search service with Managed Identity
│       └── storage.bicep       # Storage account + documents blob container
├── src/
│   ├── app/
│   │   ├── static/
│   │   │   ├── index.html      # Branded chat UI with grouped history & session switching
│   │   │   └── logo.png        # Company logo served at /static/logo.png
│   │   ├── main.py             # FastAPI RAG backend (/chat, /history, /history/{session_id})
│   │   └── requirements.txt    # Pinned stable Python dependencies
│   └── scripts/
│       └── setup_search.py     # Integrated Vectorization & daily 12:00 AM IST indexer setup
└── README.md
```

## Prerequisites (Manual Setup)

> **Only One Manual IAM Role Assignment:** Assigning **Contributor** and **Role Based Access Control Administrator** (or **User Access Administrator**) to your Entra ID App Registration's Service Principal at the Resource Group scope is the **only** manual IAM assignment required. All service-to-service Managed Identity roles are provisioned automatically by Bicep.

1. **Create an Azure Resource Group:**
   ```bash
   az group create --name <RESOURCE_GROUP_NAME> --location <AZURE_LOCATION>
   ```
2. **Create a Microsoft Entra ID App Registration** (single-tenant) and record:
   - Application (client) ID
   - Directory (tenant) ID
   - Enterprise Application **Object ID** (Service Principal Object ID)
3. **Add a GitHub OIDC Federated Credential** under **App Registration → Certificates & secrets → Federated credentials** for **GitHub Actions deploying Azure resources** targeting your org, repo, and `main` branch.
4. **Assign Resource Group RBAC Roles (ONLY manual IAM step):**
   ```bash
   az role assignment create \
     --assignee "<SERVICE_PRINCIPAL_OBJECT_ID>" \
     --role "Contributor" \
     --scope "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/<RESOURCE_GROUP_NAME>"

   az role assignment create \
     --assignee "<SERVICE_PRINCIPAL_OBJECT_ID>" \
     --role "Role Based Access Control Administrator" \
     --scope "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/<RESOURCE_GROUP_NAME>"
   ```
5. **Configure GitHub Actions Secrets & Variables** (**Settings → Secrets and variables → Actions**):
   - **Secrets:** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
   - **Variables:** `RESOURCE_GROUP_NAME`, `AZURE_LOCATION`, `STORAGE_ACCOUNT_NAME`, `STORAGE_CONTAINER_NAME` (`documents`), `OPENAI_ACCOUNT_NAME`, `SEARCH_SERVICE_NAME`, `COSMOS_ACCOUNT_NAME`, `APP_SERVICE_PLAN_NAME`, `APP_SERVICE_NAME`, `GITHUB_ACTION_PRINCIPAL_ID` (Enterprise Application Object ID)
6. **Configure Entra ID Authentication:** Under **App Registration → Authentication → Add a platform → Web**, add the Redirect URI:
   ```text
   https://<APP_SERVICE_NAME>.azurewebsites.net/.auth/login/aad/callback
   ```
   Under **Implicit grant and hybrid flows**, check **ID tokens (used for implicit and hybrid flows)** and save.

## Deployment Steps

1. **Deploy Infrastructure & Search Pipeline:** In GitHub **Actions**, manually run **Workflow 1 (`deploy.yml`)**. This deploys all Bicep modules (including Easy Auth and `SCM_DO_BUILD_DURING_DEPLOYMENT=true`), assigns Managed Identity RBAC roles, and runs `setup_search.py`.
2. **Deploy Application & UI:** Manually run **Workflow 2 (`deploy_app.yml`)**. Azure Oryx builds the Python 3.11 virtual environment (`antenv`) and starts FastAPI with Gunicorn/Uvicorn.
3. **Upload Knowledge Base & Run Initial Index:**
   - Upload `.txt`, `.pdf`, or `.docx` files into the Storage Account's `documents` blob container.
   - In the Azure Portal, open **Azure AI Search → Indexers → `document-indexer`** and click **Run** once manually to index the initial files immediately. Future uploads are indexed automatically every day at **12:00 AM IST**.
4. **Verify Live App:** Open `https://<APP_SERVICE_NAME>.azurewebsites.net/`, sign in via Entra ID SSO, confirm your logo and citations render, test switching chats in the sidebar, and verify per-user history isolation with a second Entra ID account.

## Chat Features

- **Microsoft Learn grounding:** Every question searches the internal knowledge base and Microsoft Learn in parallel. Learn results come from Microsoft's free, keyless Learn MCP server (`https://learn.microsoft.com/api/mcp`, tool `microsoft_docs_search`) via `src/app/learn_search.py`. The answer prefers internal sources for company-specific guidance and cites Learn article URLs. If Learn is unreachable, the bot answers from the internal knowledge base only.
  - App settings (optional): `ENABLE_LEARN_SEARCH` (`true`/`false`, default `true`), `LEARN_MAX_RESULTS` (default `3`), `LEARN_TIMEOUT_SECONDS` (default `10`).
  - The App Service needs outbound HTTPS to `learn.microsoft.com` (allowed by default; add it to any firewall/NAT allow list if you restrict egress).
- **"Search Microsoft Learn" under every answer:** Each answer shows the related Microsoft Learn articles that were found (clickable, opening in a new tab) and a **Search Microsoft Learn for this topic** button that opens `learn.microsoft.com/en-us/search/?terms=<the user's question>`. The button works even when `ENABLE_LEARN_SEARCH=false`, so you can keep answers grounded only in your own instructions while still offering Learn as an option.
- **Delete chats:** Hover a chat in the history sidebar and click the trash icon. `DELETE /history/{session_id}` removes only the signed-in user's messages in that session.
- **One question at a time:** The input box and send button are locked until the current answer arrives.

## Operational Guardrails

- **Build Settings:** Keep `SCM_DO_BUILD_DURING_DEPLOYMENT=true` in `infra/modules/appservice.bicep` and never set `WEBSITE_RUN_FROM_PACKAGE=1` (it blocks Oryx from building `antenv` and causes HTTP 503 crashes).
- **Static Assets:** Always commit `src/app/static/logo.png` and `src/app/static/index.html` before running `deploy_app.yml`.
- **App Service Renames:** Whenever `APP_SERVICE_NAME` changes, add the new `/.auth/login/aad/callback` Web Redirect URI in Entra ID.
- **Fresh Rebuilds:** Recreating a Resource Group requires re-applying Step 4 (Resource Group RBAC assignment) before running `deploy.yml`.
