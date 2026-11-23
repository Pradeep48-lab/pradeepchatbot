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
4. **Verify Live App:** Open `https://<APP_SERVICE_NAME>.azurewebsites.net/`, sign in via Entra ID SSO, confirm your logo and numbered references render, test switching chats in the sidebar, and verify per-user history isolation with a second Entra ID account.

## Security

### Application (`src/app/main.py`, `src/app/security.py`)

| Control | What it does |
| :--- | :--- |
| Server-side conversation history | The browser sends only the new question. Earlier turns are rebuilt from Cosmos DB, so users can't inject fake `system`/`assistant` messages. |
| Input validation | Questions are limited to `MAX_MESSAGE_CHARS` (4,000); chat session IDs must match `[A-Za-z0-9_-]{1,80}` and are random UUIDs. |
| Prompt-injection resistant prompt | Rules stay in the system message; retrieved documents go into the user message inside `<sources>` tags marked as untrusted data, with tag spoofing neutralised. |
| Prompt Shields (Azure AI Content Safety) | Blocks jailbreak attempts in the question, and drops retrieved documents that contain hidden instructions. `PROMPT_SHIELD_FAIL_OPEN` (default `true`) decides whether the bot still answers if Content Safety is unreachable. |
| Rate limiting | `RATE_LIMIT_PER_MINUTE` (20) and `RATE_LIMIT_PER_DAY` (500) per user. Kept in memory per instance: exact on one instance; use API Management or Redis if you scale out. |
| Generic errors | Users see "Something went wrong… (Reference: abc123)"; the real error is in the log stream under the same reference. |
| Security headers | Strict Content-Security-Policy (no third-party or inline scripts/styles), HSTS, `X-Frame-Options: DENY`, `nosniff`, Referrer-Policy, Permissions-Policy, and `Cache-Control: no-store` on API responses. |
| Self-hosted front-end assets | Tailwind (pre-built), Font Awesome, marked and DOMPurify are served from `static/vendor/`, not CDNs. `deploy_app.yml` rebuilds `tailwind.css` on every deployment. |
| Minimal API surface | `/docs`, `/redoc` and `/openapi.json` are off; `/history/{id}` returns only the fields the UI needs; `/source/...` only opens files that were retrieved for the user's own answer. |

### Infrastructure (Bicep)

| Resource | Hardening |
| :--- | :--- |
| Azure OpenAI | `disableLocalAuth: true` (no API keys) |
| Azure AI Search | `disableLocalAuth: true` (RBAC only, no admin/query keys) |
| Cosmos DB | `disableLocalAuth: true`, `disableKeyBasedMetadataWriteAccess: true`, TLS 1.2 |
| Storage | `allowSharedKeyAccess: false` (no account keys or SAS), Entra auth by default in the portal, 14-day blob and container soft delete, blob versioning |
| App Service | FTP and SCM basic-auth publishing disabled, TLS 1.2 minimum, HTTP/2, remote debugging off |
| Content Safety (new) | `<OPENAI_ACCOUNT_NAME>-safety`, S0, keys disabled; the app calls it with its managed identity (**Cognitive Services User**). Set `enablePromptShields=false` to skip it. |

### After deploying this version (one-time)

Keys are now disabled everywhere, so **people** need Entra roles to work with data in the portal. Run `deploy.yml` first, then `deploy_app.yml`, then grant these to the admins who manage content:

```bash
RG=<RESOURCE_GROUP_NAME>; ME=$(az ad signed-in-user show --query id -o tsv)

# Upload / manage documents in the Storage container (then pick "Switch to Microsoft Entra user account" in the portal)
az role assignment create --assignee $ME --role "Storage Blob Data Contributor" \
  --scope $(az storage account show -g $RG -n <STORAGE_ACCOUNT_NAME> --query id -o tsv)

# Run/reset the indexer and use Search explorer in the portal
SEARCH_ID=$(az search service show -g $RG -n <SEARCH_SERVICE_NAME> --query id -o tsv)
az role assignment create --assignee $ME --role "Search Service Contributor" --scope $SEARCH_ID
az role assignment create --assignee $ME --role "Search Index Data Reader" --scope $SEARCH_ID

# (Optional) Read chat history in Cosmos DB Data Explorer
az cosmosdb sql role assignment create -g $RG -a <COSMOS_ACCOUNT_NAME> --scope "/" --principal-id $ME \
  --role-definition-id 00000000-0000-0000-0000-000000000001
```


## References (citations)

Each answer cites the knowledge-base passages it used, like Copilot does:

- **Numbered markers in the answer** (¹ ²): the model is told to cite every sentence or step with the number of the source passage it came from. Numbers that don't match a retrieved passage are dropped.
- **"N references" grid under the answer**: one chip per retrieved passage, showing the source file and which part of it (`2310060040008914.txt - Part 3`). Retrieved-but-not-cited passages are shown dimmed. The list can be collapsed.
- **Click a marker or chip** to see the exact passage the answer was based on. **Open full file** loads the original file from the storage account with that passage highlighted; **Download** saves it. PDFs and other non-text files are always downloaded, never rendered in the page.
- **How the parts are numbered:** the indexer splits each file into ~2,000-character parts (`setup_search.py`, SplitSkill). Part numbers come from the chunk key (`..._pages_0` = Part 1).
- **Security:** the browser never sends a file path. `GET /source/{session}/{answer}/{n}` looks up reference *n* on the user's own saved answer and reads that blob with the App Service's managed identity, which has **Storage Blob Data Reader on the `documents` container only**. Files over `MAX_SOURCE_BYTES` (5 MB) aren't opened.
- **Settings:** `SEARCH_TOP` (8) = passages retrieved per question = maximum references. `AZURE_STORAGE_ACCOUNT_URL` / `AZURE_STORAGE_CONTAINER` are set by Bicep.
- Chats saved before this change still show their old sources under **View sources**.

## AI Features

| Feature | How it works | Settings |
| :--- | :--- | :--- |
| **Hybrid search + semantic ranker** | Every question runs keyword + vector search together (exact error codes and ticket numbers *and* meaning), then the semantic ranker re-orders the top results and scores them 0–4. Falls back to plain hybrid search if the ranker isn't enabled. | `semanticSearch` Bicep param (`standard` default; `free` = 1,000 queries/month). `SEARCH_TOP` (5). `setup_search.py` adds the `default` semantic configuration (no re-index needed). |
| **Follow-up understanding** | For follow-ups ("how do I verify that?") and attachments, the model first rewrites the message into a standalone search query using the last few turns. The UI shows "Searched for: …". | Runs only when there is history or an attachment. |
| **Streaming answers** | `POST /chat/stream` returns newline-delimited JSON events (`meta`, `delta`, `done`, `suggestions`, or `blocked`/`error`); the UI renders text as it arrives. `/chat` still returns one JSON response. | `MAX_ANSWER_TOKENS` (1500) |
| **Screenshots and log files** | Paperclip, paste (Ctrl+V a screenshot) or drag and drop. Images are resized in the browser, checked server-side (type, real file signature, size), read by gpt-4.1 and used to find the error code for search. Text/log files are added as delimited, untrusted data. Only file *names* are saved in history. | `MAX_ATTACHMENTS` (3), `MAX_IMAGE_BYTES` (4 MB), `MAX_TEXT_ATTACHMENT_CHARS` (20,000) |
| **Similar closed tickets** | Knowledge-base results whose file name looks like a ticket are shown as cards with the extracted **Resolution** line. Name ticket files like `ticket-1234-closed.md`, `INC0012345 - VPN drops.pdf` or `Case 5512.docx`, and include a `Resolution:` line. | `TICKET_TITLE_PATTERN` (regex) |
| **Confidence badge** | Based on the best semantic ranker score: *Strong match* (≥ 2.0), *Partial match* (≥ 1.4), *Weak match*, or *No matching sources*. The model is also told the level, so weak matches get a cautious answer. | `CONFIDENCE_HIGH_SCORE`, `CONFIDENCE_MEDIUM_SCORE` |
| **Suggested next questions** | Three follow-up questions under each answer; click one to ask it. | `ENABLE_SUGGESTIONS` (`true`) |
| **Draft customer reply** | Turns a saved answer into a customer-ready email (internal ticket numbers, file names, IPs and tool names removed), with optional customer name, editable text and a Copy button. Counts toward the rate limit. | — |
| **Feedback** | Thumbs up/down on every answer, with an optional comment on thumbs down. Saved on the chat record in Cosmos DB (`feedback.rating`, `feedback.comment`). | — |

**Finding knowledge gaps:** in Cosmos DB Data Explorer, query
`SELECT c.user_message, c.feedback.comment, c.confidence.level FROM c WHERE c.feedback.rating = "down" OR c.confidence.level IN ("low", "none")`
to see which questions need new or better documents in the storage container.

**Model API version:** the app now uses Azure OpenAI API version `2024-10-21` (GA; needed for screenshots). Override with the `AZURE_OPENAI_API_VERSION` app setting.

## Chat Features

- **Formatted answers:** The model is instructed to answer in Markdown (heading, short summary, numbered steps, bold only for UI labels), and the UI renders it with `marked` and sanitizes it with `DOMPurify` before display. If those libraries can't load, answers fall back to plain text.
- **Delete chats:** Hover a chat in the history sidebar and click the trash icon. `DELETE /history/{session_id}` removes only the signed-in user's messages in that session.
- **One question at a time:** The input box and send button are locked until the current answer arrives.

## Operational Guardrails

- **Build Settings:** Keep `SCM_DO_BUILD_DURING_DEPLOYMENT=true` in `infra/modules/appservice.bicep` and never set `WEBSITE_RUN_FROM_PACKAGE=1` (it blocks Oryx from building `antenv` and causes HTTP 503 crashes).
- **Static Assets:** Always commit `src/app/static/logo.png` and `src/app/static/index.html` before running `deploy_app.yml`.
- **App Service Renames:** Whenever `APP_SERVICE_NAME` changes, add the new `/.auth/login/aad/callback` Web Redirect URI in Entra ID.
- **Fresh Rebuilds:** Recreating a Resource Group requires re-applying Step 4 (Resource Group RBAC assignment) before running `deploy.yml`.
