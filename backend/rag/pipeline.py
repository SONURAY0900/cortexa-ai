"""
RAG ingestion pipeline.

Responsible for orchestrating:

PDF
 ↓
Document processing (embedded text, OCR for pages without text)
 ↓
Chunking
 ↓
Embeddings
 ↓
Supabase storage

Outcome rules
-------------
* COMPLETED (documents.processing_status) means every page that has text
  was chunked, embedded and stored.
* If some pages could not be read (OCR failed after retries), the pages
  that were read are still indexed and the summary reports
  partial=True with the failed page numbers. The document is searchable
  for the indexed pages only.
* If no page yields usable text, or embedding/storage fails, nothing is
  kept and IngestionError is raised with a reason and the summary.
"""

import threading
from pathlib import Path

from pypdf import PdfReader

from config import settings

from rag.ingestion.document_processor import (
    process_document
)

from rag.chunking.chunker import (
    split_text
)

from rag.embeddings.embeddings import (
    generate_embeddings
)

from rag.retrieval.vector_store import (
    create_document,
    delete_document,
    store_chunks
)

from database.connection import supabase


# Chunks embedded and stored per batch: memory stays flat however many
# pages the PDF has, and no single insert request grows with the PDF.
BATCH_CHUNKS = 64

# PDFs indexed at the same time; the others wait for a free slot.
_ingest_slots = threading.BoundedSemaphore(max(1, settings.PDF_MAX_CONCURRENT))


class IngestionError(Exception):
    """
    Ingestion did not produce a searchable document.

    reason: "no_text" | "ocr_failed" | "indexing_failed" | "invalid_pdf"
            | "too_many_pages"
    summary: processing summary (see build_summary), when available.
    """

    def __init__(self, reason: str, message: str, summary: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.summary = summary or {}


def build_summary(pages: list[dict]) -> dict:
    """Per-page extraction outcome, safe to show to the user."""

    ocr_pages = [p for p in pages if p.get("source") == "ocr"]
    failed = [p["page_number"] for p in ocr_pages if p.get("ocr_status") == "failed"]
    empty = [p["page_number"] for p in ocr_pages if p.get("ocr_status") == "empty"]
    with_text = [p["page_number"] for p in pages if (p.get("text") or "").strip()]

    return {
        "pages_total": len(pages),
        "pages_with_embedded_text": sum(1 for p in pages if p.get("source") == "text"),
        "pages_needing_ocr": len(ocr_pages),
        "pages_ocr_succeeded": sum(1 for p in ocr_pages if p.get("ocr_status") == "success"),
        "failed_pages": failed,
        "pages_without_text": empty,
        "pages_over_ocr_limit": [p["page_number"] for p in ocr_pages if p.get("ocr_error") == "OCR_LIMIT"],
        "pages_with_text": with_text,
        "pages_indexed": [],
        "chunks": 0,
        "indexed": False,
        "partial": False,
    }


def ingest_pdf_with_summary(
    file_path: str,
    filename: str,
    user_id: str | None = None,
    progress=None,
    file_id: str | None = None
) -> tuple[str, dict]:
    """
    Ingest a PDF into the RAG system.

    Returns (document_id, summary). Raises IngestionError when the PDF
    cannot be made searchable; nothing is left in the database then.

    progress: optional callable(stage, details) reporting real pipeline
    stages ("queued", "extracting", "ocr", "chunking", "embedding",
    "indexing").
    """

    pdf_path = Path(file_path)

    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF file not found: {file_path}"
        )

    report = progress or (lambda stage, details=None: None)

    pages_total = _page_count(pdf_path)
    if pages_total and settings.MAX_PDF_PAGES and pages_total > settings.MAX_PDF_PAGES:
        raise IngestionError(
            "too_many_pages",
            f"{pages_total} pages; the limit is {settings.MAX_PDF_PAGES}.",
            {"pages_total": pages_total, "max_pages": settings.MAX_PDF_PAGES},
        )

    if not _ingest_slots.acquire(blocking=False):
        report("queued")
        _ingest_slots.acquire()
    try:
        return _ingest(pdf_path, filename, user_id, report, file_id)
    finally:
        _ingest_slots.release()


def _page_count(pdf_path: Path) -> int | None:
    """Page count without extracting text; None if pypdf can't tell."""
    try:
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return None     # process_document reports unreadable PDFs


def _ingest(pdf_path: Path, filename: str, user_id, report, file_id) -> tuple[str, dict]:
    # ========================================================
    # STEP 1: DOCUMENT PROCESSING
    # ========================================================

    report("extracting")

    try:
        pages = process_document(pdf_path, progress=report)
    except Exception as e:
        raise IngestionError("invalid_pdf", f"PDF could not be read: {e.__class__.__name__}") from e

    if not pages:
        raise IngestionError("invalid_pdf", "No pages found in PDF.")

    summary = build_summary(pages)

    if not summary["pages_with_text"]:
        reason = "ocr_failed" if summary["failed_pages"] else "no_text"
        raise IngestionError(reason, "No page produced usable text.", summary)

    # ========================================================
    # STEP 2: CREATE DOCUMENT RECORD
    # ========================================================

    document_id = create_document(
        filename=filename,
        file_path=str(pdf_path),
        file_type="application/pdf",
        file_size=pdf_path.stat().st_size,
        user_id=user_id,
        file_id=file_id
    )

    try:
        stored = _chunk_embed_and_store(document_id, pages, report)
    except Exception as e:
        # Never leave a half-ingested document (or partial chunks) behind.
        delete_document(document_id)
        if isinstance(e, ValueError) and "No text chunks" in str(e):
            raise IngestionError("no_text", str(e), summary) from e
        raise IngestionError("indexing_failed", f"Indexing failed: {e.__class__.__name__}", summary) from e

    summary["chunks"] = len(stored)
    summary["pages_indexed"] = sorted({c["page_number"] for c in stored if c.get("page_number") is not None})
    summary["indexed"] = True
    summary["partial"] = bool(summary["failed_pages"])

    supabase.table("documents").update({
        "processing_status": "COMPLETED"
    }).eq("id", document_id).execute()

    return document_id, summary


def ingest_pdf(
    file_path: str,
    filename: str,
    user_id: str | None = None,
    progress=None,
    file_id: str | None = None
) -> str:
    """Backward-compatible wrapper: returns only the document ID."""

    document_id, _ = ingest_pdf_with_summary(file_path, filename, user_id, progress, file_id)
    return document_id


def _chunk_embed_and_store(
    document_id: str,
    pages: list[dict],
    report
) -> list[dict]:

    report("chunking", {"pages": len(pages)})

    # ========================================================
    # STEP 3: CHUNKING
    # ========================================================

    all_chunks = []

    global_chunk_index = 0

    for page in pages:

        page_number = page.get(
            "page_number"
        )

        text = page.get(
            "text",
            ""
        )

        if not text.strip():
            continue

        chunks = split_text(
            text
        )

        for chunk in chunks:

            if not chunk.strip():
                continue

            all_chunks.append(
                {
                    "chunk_index": global_chunk_index,
                    "content": chunk,
                    "page_number": page_number,
                }
            )

            global_chunk_index += 1

    if not all_chunks:
        raise ValueError(
            "No text chunks could be generated from the PDF."
        )

    # ========================================================
    # STEP 4: EMBED + STORE, one batch at a time
    # ========================================================

    total = len(all_chunks)

    for start in range(0, total, BATCH_CHUNKS):

        batch = all_chunks[start:start + BATCH_CHUNKS]
        report("embedding", {"chunks": total, "done": start})

        embeddings = generate_embeddings(
            [chunk["content"] for chunk in batch]
        )

        if len(embeddings) != len(batch):
            raise RuntimeError(
                "Number of embeddings does not "
                "match number of chunks."
            )

        report("indexing", {"chunks": total, "done": start})

        store_chunks(
            document_id=document_id,
            chunks=[{**chunk, "embedding": embedding} for chunk, embedding in zip(batch, embeddings)]
        )

    # Text and page numbers only; the vectors were freed batch by batch.
    return all_chunks
