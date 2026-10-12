-- ============================================================
-- Indexes for PDF retrieval and per-user file lookups
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- document_chunks had only its primary key, but every PDF search
-- (match_document_chunks / keyword_document_chunks take
-- filter_document_id), the neighbour lookup in context_builder
-- (document_id + chunk_index) and document deletion filter by
-- document_id. Without an index each of these scans every chunk of
-- every user's PDFs.
--
-- files.user_id is filtered by the profile usage counts, the user's file
-- list and the admin "delete user" flow.
--
-- Adds indexes only: no table, column, data or policy changes.
-- A vector (HNSW) index is deliberately not added: searches are always
-- restricted to one document, which the btree index below already
-- narrows to that document's chunks, and exact search is then fast.
-- ============================================================

create index if not exists idx_document_chunks_document
    on public.document_chunks (document_id, chunk_index);

create index if not exists idx_files_user
    on public.files (user_id);
