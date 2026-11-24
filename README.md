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


## Staying working through Azure changes

The legacy bot broke when Azure retired features it used. Version 2 avoids that in four ways:

- **No On Your Data.** It doesn't use the deprecated `data_sources` extension. Search runs in Azure AI Search, and the results are passed to the model as text.
- **Current token setting.** Chat requests use `max_completion_tokens`, which gpt-4.1 and newer reasoning models accept. `max_tokens` is not used anywhere.
- **Pinned versions.** The API version is pinned (`AZURE_OPENAI_API_VERSION`, default `2024-10-21` GA), model versions are pinned in `infra/modules/openai.bicep`, and Python libraries are pinned in `src/app/requirements.txt`. To upgrade, change a version, test in dev, then deploy.
- **Retirement watch.** Check the Azure OpenAI model retirement page every quarter, and upgrade the pinned model versions in dev before their retirement date.

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
│       ├── openai.bicep        # Azure OpenAI account + chat, chat-mini (fast) & embedding deployments
│       ├── roles.bicep         # Automated Managed Identity & SP RBAC assignments
│       ├── search.bicep        # Azure AI Search service with Managed Identity
│       └── storage.bicep       # Storage account + documents blob container
├── src/
│   ├── app/
│   │   ├── static/
│   │   │   ├── index.html      # Branded chat UI with grouped history & session switching
│   │   │   └── logo.png        # Company logo served at /static/logo.png
│   │   ├── main.py             # FastAPI RAG backend (/chat, /chat/stream, /history, /source)
│   │   ├── assistant.py        # Attachments, query rewrite, memory summaries, source details
│   │   ├── inventory.py        # Knowledge-base questions (recent / how many / indexed)
│   │   ├── security.py         # Rate limits, security headers, Prompt Shields
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

Each answer cites the original files it used:

- **Numbered markers in the answer** (¹ ²): one number per source *file*. The model cites every sentence or step; numbers that don't match a source are dropped.
- **"Sources (N files)" list under the answer**: only the files the answer actually cites, one row per original file name (e.g. `2310060040008914.txt`), numbered 1, 2, 3 in the order the answer first uses them. Files that were searched but not used are not shown. The list can be collapsed.
- **When the answer isn't in the documents**, the model starts its reply with an internal `NO_ANSWER_IN_SOURCES` signal (never shown to users). The app then removes any citation numbers and shows **no sources**.
- **Click a file (or a marker)** to open the whole original file from the storage account, with every section the answer used highlighted; **Download** saves it. PDFs and other non-text files are downloaded, never rendered in the page.
- **More variety per answer:** search takes the 20 best-ranked sections, then keeps at most `MAX_PARTS_PER_FILE` (3) from any one file, up to `SEARCH_TOP` (8) sections in total, so one very large file can't crowd out other tickets. (Files are split into ~2,000-character sections by the indexer; large combined exports are best split into one file per ticket.)
- **Security:** the browser never sends a file path. `GET /source/{session}/{answer}/{n}` looks up source *n* on the user's own saved answer and reads that blob with the App Service's managed identity, which has **Storage Blob Data Reader on the `documents` container only**. Files over `MAX_SOURCE_BYTES` (5 MB) aren't opened.
- **Only real matches become sources:** sections with a semantic ranker score below `MIN_RERANKER_SCORE` (1.5 of 4) are dropped before the model sees them, and the model is told to cite only the file(s) each statement was taken from.
- **Source details:** under each file name a grey line shows the type (**Ticket**, **Runbook** or **Document**, from the file name), and the product and date when the file has lines like `Product: VPN Gateway` and `Date closed: 14 Mar 2026` (also `Closed on`, `Resolved on`, `Last updated`). The model sees the same details and prefers the most recent source when two disagree.
- **Closed tickets first:** up to `MAX_TICKETS_IN_CONTEXT` (4) matching tickets are always included, even when documents rank higher, and each ticket up to `FULL_TICKET_CHARS` (15,000 characters) is sent to the model **whole** (symptoms, what was tried, root cause, resolution), not just the matched section. The Sources list groups files under **Tickets** and **Documents**.
- **Settings:** `SEARCH_TOP` (8), `SEARCH_CANDIDATES` (20), `MAX_PARTS_PER_FILE` (3), `MIN_RERANKER_SCORE` (1.5; 0 turns the filter off). `AZURE_STORAGE_ACCOUNT_URL` / `AZURE_STORAGE_CONTAINER` are set by Bicep.
- Chats saved before this change still open their sources normally.

## AI Features

| Feature | How it works | Settings |
| :--- | :--- | :--- |
| **Hybrid search + semantic ranker** | Every question runs keyword + vector search together (exact error codes and ticket numbers *and* meaning), then the semantic ranker re-orders the top results and scores them 0–4. Falls back to plain hybrid search if the ranker isn't enabled. | `semanticSearch` Bicep param (`standard` default; `free` = 1,000 queries/month). `SEARCH_TOP` (5). `setup_search.py` adds the `default` semantic configuration (no re-index needed). |
| **Follow-up understanding** | For follow-ups ("how do I verify that?") and attachments, the fast model (gpt-4.1-mini) first rewrites the message into a standalone search query using the last few turns. | Runs only when there is history or an attachment. |
| **Fast helper model** | Follow-up rewriting, memory summaries and the knowledge-base question check use the `chat-mini` deployment (gpt-4.1-mini, 50K TPM, added by Bicep). Answers still come from gpt-4.1. If `chat-mini` doesn't exist yet, the app uses `chat` instead and tries `chat-mini` again after 10 minutes. | `AZURE_OPENAI_FAST_DEPLOYMENT` (`chat-mini`) |
| **Detailed answers** | Answers are written for support engineers: typically 300–700 words for troubleshooting, with cause, how to confirm it, every fix step with exact settings and commands, and the matching past tickets. They use only what's in the sources. The Prompt Shields check runs *while* the answer starts generating. Nothing is shown until the check passes; on a jailbreak the turn is blocked, and if a document is flagged the answer is regenerated without it. Every answer logs `Timing ms: history=… rewrite=… search=… shields=… first_text=… total=…` to App Service logs. | `SEARCH_TOP` (8), `MAX_ANSWER_TOKENS` (2000), `MAX_TICKETS_IN_CONTEXT` (4), `FULL_TICKET_CHARS` (15000) |
| **General Azure guidance** | After the knowledge-base answer, the model adds a section titled **General Azure guidance (not from our knowledge base)**. It is set apart with a grey bar and carries no citations. It holds standard Microsoft troubleshooting the documents don't cover (for example Boot diagnostics, Serial console, Resource health). When the knowledge base has no answer at all, it still gives this general guidance (no sources, and the question is logged as a knowledge gap). When the problem is clear but details are missing, it answers anyway and ends with **To narrow this down:** plus one question. | `ALLOW_GENERAL_KNOWLEDGE` (`true`; `false` = answers only from our documents) |
| **Support answer layout** | The model works like a senior Microsoft Azure support engineer on the case: it checks our storage account files first, then plans the troubleshooting. Troubleshooting answers use **What our files show** (each matching ticket or runbook: symptom, root cause, resolution, and how it compares with this case) → **Likely cause** → **Troubleshooting steps** (ordered checks with portal paths or CLI/PowerShell commands, what each result means, then the fix) → **Verify** → **General Azure guidance** (labelled) → **Escalate if**. Every statement taken from our files is cited, so those files appear under Sources. How-to questions get a direct answer and the full procedure. | — |
| **Clarifying questions** | When a question is too vague and the documents give different fixes depending on missing details (e.g. which VPN type, the exact error), the assistant asks one short question instead of guessing. No sources are shown for it. It never asks twice in a row. | — |
| **Long conversations** | The last 4 turns go to the model word for word; older turns are folded into short running notes by the fast model after each answer (saved as `memory_summary` on the latest turn in Cosmos DB), so long chats keep their context without getting slower. | `MAX_HISTORY_TURNS` (4) |
| **Questions about the knowledge base** | Questions like *"what are the recent tickets uploaded?"*, *"how many VPN tickets were created in the last month?"*, *"give me the details of the tickets closed this month"* or *"is everything indexed?"* are answered from the real file list in the storage account and the search index, not from search passages. The answer is a count plus a table: file, date, product, and for tickets the **Issue** and **Resolution** lines (otherwise an Indexed column). Files not indexed yet are listed under the table. **Dates:** "closed" questions use each ticket's `Date closed:` / `Closed on:` / `Resolved on:` line, "created" questions use `Created:` / `Opened:` / `Reported:`, and upload questions use the upload date. A ticket without the needed line falls back to its upload date and is marked "(uploaded)". Only files uploaded since the start of the period are opened (a ticket is uploaded after it's closed), and only their first 16 KB is read, in parallel. "This month" means since the 1st. A file matches a topic when its name or indexed text contains the keyword. The file list is cached for 5 minutes. This uses the roles the app already has (Storage Blob Data Reader on `documents`, Search Index Data Reader), so there's nothing new to grant. | `INVENTORY_CACHE_SECONDS` (300), `INVENTORY_MAX_ROWS` (20), `INVENTORY_MAX_READS` (500), `INVENTORY_MAX_FILES` (20,000), `DATE_DAY_FIRST` (`true`: 03/04/2026 = 3 April) |
| **Streaming answers** | `POST /chat/stream` returns newline-delimited JSON events (`meta`, `delta`, `done`, `suggestions`, or `blocked`/`error`); the UI renders text as it arrives. `/chat` still returns one JSON response. | `MAX_ANSWER_TOKENS` (900) |
| **Screenshots and log files** | Paperclip, paste (Ctrl+V a screenshot) or drag and drop. Images are resized in the browser, checked server-side (type, real file signature, size), read by gpt-4.1 and used to find the error code for search. Text/log files are added as delimited, untrusted data. Only file *names* are saved in history. | `MAX_ATTACHMENTS` (3), `MAX_IMAGE_BYTES` (4 MB), `MAX_TEXT_ATTACHMENT_CHARS` (20,000) |
| **Similar closed tickets** | Knowledge-base results whose file name looks like a ticket are shown as cards with the extracted **Resolution** line. Name ticket files like `ticket-1234-closed.md`, `INC0012345 - VPN drops.pdf` or `Case 5512.docx`, and include a `Resolution:` line. | `TICKET_TITLE_PATTERN` (regex) |
| **Confidence (internal)** | The best semantic ranker score is still used to tell the model how well the documents match, so weak matches get a cautious answer. No badge is shown to users. | `CONFIDENCE_HIGH_SCORE`, `CONFIDENCE_MEDIUM_SCORE` |
| **Suggested next questions** | **Off by default.** When enabled, three follow-up questions appear under each answer. | `ENABLE_SUGGESTIONS` (`false`; set to `true` to turn on) |
| **Draft customer reply** | **Off by default** (no button, and `/draft-reply` returns 404). When enabled, turns a saved answer into a customer-ready email with internal details removed. | `ENABLE_DRAFT_REPLY` (`false`; set to `true` to turn on) |
| **Feedback** | Removed from the UI (no thumbs up/down). The `/feedback` API still exists for a future re-enable. | — |

**Finding knowledge gaps:** every question the documents couldn't answer is saved with `not_found: true` and logged as `Knowledge gap: question=… searched=…`. In Cosmos DB Data Explorer, query
`SELECT c.timestamp, c.user_message, c.search_query FROM c WHERE c.not_found = true ORDER BY c._ts DESC`
to see which topics need new documents in the storage container. `WHERE c.needs_clarification = true` shows questions that were too vague.

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
