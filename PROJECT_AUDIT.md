# Project Audit — Agentic AI Chatbot (LangGraph)

Audit date: 2026-10-08 · Scope: `backend/`, `frontend/`, Supabase schema (read-only), `requirements.txt`, `.env` (variable names only)
Status: **inspection only — no project file was changed, moved or deleted during this audit.**

---

## 1. Architecture as it actually runs

```
Browser (index.html + app.js, Supabase JS for auth only)
   │  Authorization: Bearer <Supabase access token>
   ▼
FastAPI  backend/main.py  (also serves /frontend static files)
   ├─ /chat/stream  ← the ONLY chat endpoint the frontend calls
   │     routing re-implemented inline in main.py:
   │       document_id → PDF_RAG · image_path → IMAGE · else LLM router (DIRECT/WIKIPEDIA/TAVILY/ARXIV)
   │     PDF_RAG → agents/rag_agent.py → rag/retriever.py  (OLD retriever, see F1)
   │     IMAGE   → OCR.space + Groq vision
   │     final answer → Groq (openai/gpt-oss-120b), streamed as SSE
   ├─ /chat         ← LangGraph graph (graph/workflow.py); not called by the frontend
   ├─ /upload/pdf   → rag/pipeline.py: PyMuPDF/pypdf → OCR fallback → clean → chunk → FastEmbed → Supabase
   ├─ /upload/image → saved to backend/uploads/images, returns absolute server path
   └─ /voice/transcribe → Groq Whisper
Supabase: Postgres + pgvector (VECTOR(384)), Supabase Auth. Backend uses an sb_secret key (bypasses RLS).
```

## 2. Findings, most severe first

| # | Severity | Finding | Where |
|---|---|---|---|
| S1 | **Critical** | `image_path` in the chat request is an arbitrary server file path supplied by the client. Any logged-in user can make the server open any readable file and send it to Groq vision / OCR.space (path traversal + data exfiltration to third parties), or use another user's uploaded image. | `main.py` ChatRequest, `tools/vision_tool.py`, `tools/ocr_tool.py` |
| S2 | **High** | `/upload/image` and `/voice/transcribe` have **no authentication**. Anyone on the internet can store files on the server and spend the Groq quota. | `main.py` |
| S3 | **High** | `/upload/image` returns the absolute server path (`D:\...\backend\uploads\images\...`) to the browser (internal path disclosure). | `main.py` |
| S4 | Medium | Raw exception text is returned to clients: `"Authentication failed: {e}"`, `"PDF processing failed: {e}"`, `/chat` returns `error: str(e)`, Tavily/Wikipedia/arXiv failures embed exception text in tool content. | `auth/supabase_auth.py`, `main.py`, `tools/*` |
| S5 | Medium | No PDF size limit server-side (image 10 MB and audio 25 MB are enforced; frontend says 25 MB for PDFs but the backend accepts any size → memory/CPU exhaustion). | `main.py /upload/pdf` |
| S6 | Medium | CORS `allow_origins=["*"]` with `allow_credentials=True`. Auth is a bearer header (not cookies), so CSRF risk is low, but the policy is wider than needed. | `main.py` |
| S7 | Low | Supabase JS loaded from jsDelivr as floating `@2` with no integrity pin. | `app.js` |
| S8 | Low | `/auth/test` is a debug route; harmless (auth required) but unnecessary. | `main.py` |
| S9 | Low | Source links from tool results are put in `href` without a protocol check (`javascript:` would be clickable if a tool ever returned one). | `app.js` |
| F1 | **High (RAG)** | **Live chat does not use the evaluated RAG pipeline.** `rag_agent` calls the old `rag/retriever.py` (dense + naive keyword, hard 0.30 similarity gate). All Phase 10–14 evaluation work (query transformation, RRF hybrid, reranker, diversity, context builder) measures `rag/retrieval/retrieval_pipeline.retrieve_context`, which only evaluation scripts call. Production users never get the evaluated quality, and the 0.30 gate can drop every chunk for non-English PDFs ("I could not find this information"). | `agents/rag_agent.py` |
| F2 | High (UX) | Guest mode is half-built: the frontend sends `X-Guest-Session-ID` and promises "fully usable without an account", but every chat endpoint requires login, so a guest's first message fails with an alert. `POST /auth/migrate-guest` does not exist (404 on every login). | `app.js`, `main.py` |
| F3 | Medium | PDF_RAG and IMAGE routes are missing from the frontend `ROUTES` list: the router animation highlights **DIRECT** for PDF/image questions and no route badge is shown. | `app.js` |
| F4 | Medium | PDF upload is not atomic: a document row is inserted before chunking/embedding; any failure afterwards leaves an orphan `documents` row with `user_id = NULL`. `processing_status` defaults to PROCESSING and only becomes COMPLETED on success. | `rag/pipeline.py`, `main.py` |
| F5 | Medium | Uploaded files are stored on local disk. On Render the filesystem is ephemeral (inference: standard Render behaviour without a persistent disk) — images and PDFs disappear on redeploy, while their DB rows remain. | `main.py` |
| F6 | Medium | Routing logic exists twice: inline in `/chat/stream` and in the LangGraph graph used by `/chat`. The README/About page describe LangGraph routing, but the streamed path bypasses the graph. | `main.py`, `graph/` |
| F7 | Low | Backend status pill polls `/openapi.json` (the whole API schema) every 15 s although `/health` exists. | `app.js` |
| F8 | Low | Hard-coded placeholder chats ("Research papers on RAG", "History of the internet") and a dead "Settings" link in the sidebar. | `index.html` |
| F9 | Low | Every error bubble says "Please make sure the FastAPI server is running." regardless of cause; errors use `alert()`. | `app.js` |
| F10 | Low | `createNewConversation` sends `{title}` as a JSON body, the endpoint reads a query parameter (title ignored; harmless). | `app.js` |
| E1 | Environment | On this Windows machine Smart App Control blocks `mmh3.pyd`, so `fastembed` cannot be imported and **the backend cannot start locally** (`rag/embeddings/embeddings.py` imports fastembed at startup). Not a code bug; Render (Linux) is unaffected. | — |
| E2 | Environment | The project is **not a git repository**; `.gitignore` and `README.md` are empty. There is no undo for edits or deletions. | root |

What is already done well: Supabase JWT verified server-side on all conversation/PDF routes; conversation and document ownership checks (`user_id` filters) prevent IDOR on chats and PDFs; uploaded file names are reduced with `Path(name).name` and prefixed with a UUID (no traversal on write); PDF magic-byte check; the public publishable key **cannot** read any table or call either RPC (verified: HTTP 401 on all 8 tables and both RPCs); markdown renderer escapes HTML before formatting (no XSS found in rendered answers); no secret is present in frontend code (the Supabase publishable key is designed to be public).

## 3. Endpoints

| Method | Path | Auth | Ownership check | Used by frontend | Notes |
|---|---|---|---|---|---|
| GET | `/` | none | — | yes | serves index.html |
| GET | `/health` | none | — | no | could replace the openapi poll (F7) |
| GET | `/auth/me` | user | — | yes (after login) | |
| GET | `/auth/test` | user | — | no | debug (S8) |
| POST | `/upload/pdf` | user | assigns owner | yes | no size limit (S5), raw error text (S4) |
| POST | `/upload/image` | **none** | **none** | yes | S1, S2, S3 |
| POST | `/voice/transcribe` | **none** | — | yes | S2 |
| GET | `/conversations` | user | user_id | yes | |
| POST | `/conversations` | user | user_id | yes | F10 |
| GET | `/conversations/{id}/messages` | user | yes | yes | |
| DELETE | `/conversations/{id}` | user | yes | **no UI** | soft-archive |
| POST | `/chat` | user | document yes, image **no** | no | LangGraph path; returns `str(e)` |
| POST | `/chat/stream` | user | document yes, image **no** | yes | S1 |
| POST | `/auth/migrate-guest` | — | — | yes | **does not exist** (F2) |
| — | `/admin/*` | — | — | — | no admin functionality exists today |

## 4. Database (Supabase, read via PostgREST OpenAPI with the backend key; RLS/index details are not visible through PostgREST)

| Table | Rows | PK | Owner column | Timestamps | Relationships | Used by backend |
|---|---:|---|---|---|---|---|
| conversations | 17 | id | user_id, guest_session_id | created/updated/last_message_at | — | yes |
| messages | 45 | id | user_id | created_at | → conversations | yes (every chat query + route + document_id in metadata) |
| documents | 27 | id | user_id (23 rows NULL = eval copies/orphans) | uploaded_at | → files, → conversations | yes |
| document_chunks | 1708 | id | via document | created_at | → documents | yes (VECTOR(384)) |
| files | 3 | id | user_id, guest_session_id | uploaded_at, deleted_at | → conversations | **no** |
| images | 0 | id | via file | created/updated_at | → files | **no** |
| audio_files | 0 | id | via file | created/updated_at | → files | **no** |
| profiles | ? | id | id = auth user | created/updated_at | — | **no**; has `role` (default `USER`); backend key gets HTTP 403 (no grant) |
| auth.users | 2 | — | — | last_sign_in_at | — | via Supabase Auth; admin account exists and is confirmed |

RPCs: `match_document_chunks`, `keyword_document_chunks`, `rls_auto_enable`. Public key: no access (good).

**Activity history that exists today:** chat/search queries (messages: user, time, query, route, document_id), PDF uploads (documents: owner, filename, time, status), account creation and last sign-in (auth.users). **Not recorded:** image uploads, voice transcriptions, failed requests/errors, admin actions. Those can only be collected from the moment logging is added.

## 5. requirements.txt

| Package | Class | Evidence |
|---|---|---|
| fastapi, uvicorn, pydantic, python-dotenv, python-multipart | REQUIRED | app, uploads |
| langgraph | REQUIRED | graph/workflow.py |
| langchain-groq (+ langchain-core transitively) | REQUIRED | all agents |
| langchain | UNUSED directly | no `import langchain` anywhere |
| langchain-community | UNUSED | no import |
| groq | REQUIRED | Whisper, vision |
| tavily-python | REQUIRED | tools/tavily_tool.py |
| arxiv | REQUIRED | tools/arxiv_tool.py |
| wikipedia | UNUSED | the tool calls the Wikipedia REST API with `requests` |
| supabase | REQUIRED | everywhere |
| psycopg[binary] | UNUSED | no import |
| pypdf, pymupdf | REQUIRED | pdf_loader, ocr_processor |
| fastembed | REQUIRED | embeddings (production model unchanged) |
| requests, Pillow | REQUIRED | OCR, Wikipedia |
| (all) | DEPLOYMENT RISK | no versions pinned; a fresh Render build can pull breaking releases. Installed versions are known (e.g. fastapi 0.141.1, langgraph 1.2.12, supabase 2.32.0, fastembed 0.8.1) and could be pinned. |

## 6. File inventory and cleanup classification

| File | Purpose | Used by | Class |
|---|---|---|---|
| backend/main.py | API + streaming chat | runtime | KEEP (production critical) |
| backend/auth/supabase_auth.py | JWT verification | runtime | KEEP (critical) |
| backend/config/settings.py | env loading | runtime | KEEP |
| backend/config/supabase_client.py, database/connection.py | Supabase client | runtime | DUPLICATE (identical clients; also a third copy in auth) — consolidate, keep one |
| backend/graph/* | LangGraph workflow | `/chat` only | KEEP (see F6) |
| backend/agents/*, tools/*, prompts/* | routes/tools | runtime | KEEP |
| backend/rag/pipeline.py, ingestion/*, chunking/*, embeddings/* | ingestion | runtime | KEEP (critical) |
| backend/rag/retriever.py | old retriever | live chat (F1), test_retrieval.py | KEEP until F1 is decided; DEPRECATED afterwards |
| backend/rag/retrieval/* (pipeline, hybrid, dense, keyword, reranker, diversity, context_builder, text_tokens, vector_store) | evaluated retrieval stack | evaluation; vector_store by ingestion | KEEP (critical) |
| backend/rag/retrieval/reranker_backup.py | old copy of reranker | nothing imports it | SAFE TO DELETE (after backup) |
| backend/rag/generation/ | empty package | nothing | SAFE TO DELETE |
| backend/rag/evaluation/* + datasets | evaluation | offline | KEEP (do not delete datasets) |
| backend/test_*.py (5 files) | manual smoke scripts, hard-coded IDs/paths, not a test suite | nothing | POSSIBLY UNUSED — move to `backend/scripts/manual/` rather than delete |
| backend/utils/helpers.py | text helpers | nothing imports it | POSSIBLY UNUSED |
| backend/uploads/* | user files (2.4 MB PDFs, 2.3 MB images incl. a personal certificate) | runtime data | KEEP; must be git-ignored |
| backend/.env | secrets | runtime | KEEP; must be git-ignored (`.gitignore` is empty) |
| frontend/index.html, app.js, style.css, about.html, about.css, assets/logo/logo.svg | UI | runtime | KEEP |
| README.md, .gitignore | empty | — | KEEP and fill |

Debug output: 212 `print()` calls outside evaluation code (e.g. `WIKIPEDIA DEBUG`, dense retrieval rank dumps) — replace with the existing `log_agent` logger.

## 7. Feature verification status

Static inspection only so far. Runtime verification is limited on this machine: the backend cannot start without a workaround for E1, and authenticated flows need a real login, which only the owner can perform (the assistant does not sign in with personal credentials).

| Feature | Status from inspection |
|---|---|
| Signup / login / logout / password reset / Google OAuth | Implemented in frontend via Supabase JS; backend verifies tokens. |
| Protected endpoints | Yes, except `/upload/image` and `/voice/transcribe` (S2). |
| Streaming chat + history | Implemented; history persisted in Supabase. |
| Routing (DIRECT/WIKIPEDIA/TAVILY/ARXIV/PDF_RAG/IMAGE) | Implemented; PDF_RAG/IMAGE mis-displayed in UI (F3). |
| PDF upload → RAG | Implemented; chat uses old retriever (F1); no size limit (S5). |
| Image OCR + vision | Implemented; insecure path handling (S1). |
| Voice | Implemented (MediaRecorder → Groq Whisper); no auth (S2); errors via `alert()`. |
| Conversation delete | Backend exists, no UI. |
| About page | Works; content describes only 4 routes. |
| Admin | None exists. |
| Guest use | Broken (F2). |

---

## 8. Resolution (2026-10-08)

Commits: `c91c7c6` baseline → `c675356` backend → `a5d3199` frontend/admin/cleanup.

| # | Status | What changed |
|---|---|---|
| S1 | **Fixed** | Chat requests carry an `image_id` (a `files` row owned by the user); the server resolves the path itself and only inside `backend/uploads/`. `image_path` from clients is ignored. Covered by tests. |
| S2 | **Fixed** | `/upload/image` and `/voice/transcribe` require login. |
| S3 | **Fixed** | Upload responses contain IDs, never server paths; files are stored under random names. |
| S4 | **Fixed** | Generic client messages; global handlers for validation and unhandled errors; details only in server logs. Tool failures are now returned as errors (retry works) instead of being fed to the LLM as content. |
| S5 | **Fixed** | Server-side limits (`MAX_PDF_MB` 25, `MAX_IMAGE_MB` 10, `MAX_AUDIO_MB` 25; 413 on excess); image bytes verified with Pillow. |
| S6 | **Fixed** | CORS only for `ALLOWED_ORIGINS` (none by default; frontend is same-origin). Security headers added; `/docs` off when `APP_ENV=production`. |
| S7 | **Fixed** | supabase-js pinned to 2.117.3 with SRI; Supabase URL/key served from `/config` (env), not hard-coded. |
| S8 | **Fixed** | `/auth/test` removed. |
| S9 | **Fixed** | Only http(s) links are rendered from sources and markdown. |
| F1 | **Fixed** (owner-approved) | Live PDF_RAG uses `retrieve_context`; old `rag/retriever.py` removed. Verified with a live question. |
| F2 | **Fixed** (owner-approved) | Login required; dead guest code and the call to the missing endpoint removed. |
| F3 | **Fixed** | All six routes shown in the UI. |
| F4 | **Fixed** | Ownership set at insert; failed ingestion deletes the partial document. |
| F5 | Documented | README explains attaching a Render disk for `backend/uploads`. |
| F6 | Partly | Image analysis shared by both paths (`services/image_analysis.py`); `/chat/stream` still routes inline (works; a full merge into LangGraph streaming is a larger refactor). |
| F7 | **Fixed** | Status pill polls `/health` every 30 s, paused when the tab is hidden. |
| F8–F10 | **Fixed** | Placeholder chats/links removed; contextual error states with retry; conversation create sends a JSON body. |
| E1 | Open (environment) | Local runs need Smart App Control to allow `mmh3.pyd`; Render is unaffected. |
| E2 | **Fixed** | Git repository with `.gitignore` (secrets, uploads, venv excluded). |

New: admin API + panel, activity logging migration, admin role script, security test suite, README, `.env.example`.

## Final completion audit (2026-10-10)

| # | Status | What changed |
|---|---|---|
| A1 | **Fixed** | Settings → Back sometimes looked like a logout: pages rendered the logged-out UI while a backend `/auth/me` check ran, and a failed token refresh (network/5xx) was treated as an expired session. `Auth` in `common.js` is now a single state machine (`loading` / `signed-in` / `signed-out` / `error`) with no backend call before first render, single-flight refresh, 503 kept separate from 401, Back/Forward-cache resync and a Reconnect state. |
| A2 | **Fixed** | Backend maps Supabase 5xx/429/network errors during token verification to 503 (with `Retry-After`) instead of 401. |
| A3 | **Fixed** | Admin = confirmed `ADMIN_EMAIL` account **and** `profiles.role = 'ADMIN'`; `app_metadata.role` ignored; role-read outage → 503; `ADMIN_PASSWORD` removed. Migration `20261010_protect_profile_role.sql` blocks role changes from the browser. |
| A4 | **Fixed** | Groq clients had no explicit timeout (SDK default 10 min); now `GROQ_TIMEOUT_S` (60) / `GROQ_MAX_RETRIES` (2). |
| A5 | **Fixed** | RAG/OCR/vision/search prompts state that retrieved or extracted text is untrusted and its instructions must not be followed. |
| A6 | **Fixed** | HTML pages served with `Cache-Control: no-cache`; `/about` route; `/favicon.ico` served; asset version bumped to `1.3.3`. |
| A7 | **Added** | Test for page citations from OCR'd / mixed PDFs through to the LLM context. |
| A8 | Open | Supabase implicit OAuth flow kept (PKCE recommended). Server logs include user queries. |
