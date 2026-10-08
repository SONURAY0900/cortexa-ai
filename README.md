# Agentic AI Chatbot (LangGraph)

A chatbot that routes every question to the right tool — the LLM itself, Wikipedia,
the live web (Tavily), arXiv, your uploaded PDFs (RAG) or images (OCR + vision) —
and streams a grounded answer with sources. Includes voice input, saved chat
history and an admin panel.

**Stack:** FastAPI · LangGraph / LangChain · Groq · Supabase (Postgres, pgvector, Auth) · FastEmbed · plain HTML/CSS/JS frontend.

## Project layout

```
backend/
  main.py              API: chat streaming, uploads, voice, conversations
  admin/routes.py      /admin/* API (admin-only)
  auth/                Supabase token verification + admin check
  services/            storage, activity logging, image analysis
  graph/ agents/ tools/ prompts/   routing and tool agents
  rag/                 ingestion, chunking, embeddings, retrieval, evaluation
  scripts/grant_admin.py   grant/revoke the admin role
  tests/               security test suite (unittest)
frontend/
  index.html app.js    chat
  admin.html admin.js  admin panel
  about.html           project page
  common.js style.css  shared runtime and design system
supabase/migrations/   SQL to run once in the Supabase SQL editor
```

## Setup

1. Python 3.13, then:

   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   ```

2. Copy `backend/.env.example` to `backend/.env` and fill in the keys.
   `SUPABASE_KEY` is the server-side secret key; `SUPABASE_PUBLISHABLE_KEY` is the
   public key the browser uses for sign-in.

3. Run `supabase/migrations/20261008_activity_logging.sql` once in the Supabase
   SQL editor (activity logging + admin audit trail). The app works without it;
   the admin panel then shows only data from the existing tables.

4. Start the app:

   ```bash
   cd backend
   uvicorn main:app --reload
   ```

   Open http://127.0.0.1:8000 (chat) or http://127.0.0.1:8000/admin (admin).

## Admin access

Admins sign in with their normal account. Access requires **both**:

- the email listed in `ADMIN_EMAIL` (comma-separated for several), and
- `app_metadata.role = "admin"` on the Supabase account, set server-side with
  `python -m scripts.grant_admin` (run from `backend/`; `--revoke` removes it).

No admin password is stored or checked by the application.

## Tests

```bash
cd backend
python -m unittest discover -s tests -v
```

## Deploying on Render (no Docker)

- Root directory: `backend`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Environment: everything from `.env.example`, plus `APP_ENV=production`
  (disables `/docs` and `/openapi.json`).
- Uploaded files are stored on local disk under `backend/uploads/`. Render's
  filesystem is not persistent unless you attach a disk, so attach one (mounted at
  `backend/uploads`) if uploaded files must survive redeploys. PDF chunks used for
  answers are stored in Supabase either way.
- The embedding model is downloaded on first start, so the first request after a
  deploy is slower.

## RAG evaluation

Offline evaluation tools live in `backend/rag/evaluation/` (datasets in
`datasets/`). Example:

```bash
python -m rag.evaluation.evaluator --document-id <id> --dataset my_pdf.json
```
