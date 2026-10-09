"""
RAG ingestion pipeline.

Responsible for orchestrating:

PDF
 ↓
Document processing
 ↓
Chunking
 ↓
Embeddings
 ↓
Supabase storage
"""

from pathlib import Path

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


def ingest_pdf(
    file_path: str,
    filename: str,
    user_id: str | None = None,
    progress=None,
    file_id: str | None = None
) -> str:
    """
    Ingest a PDF into the RAG system.

    progress: optional callable(stage, details) reporting real
    pipeline stages ("extracting", "chunking", "embedding",
    "indexing") to the caller, e.g. for an upload status display.

    Flow:

    PDF
      ↓
    Process document
      ↓
    Create chunks
      ↓
    Generate embeddings
      ↓
    Store in Supabase
      ↓
    Return document ID
    """

    pdf_path = Path(file_path)

    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF file not found: {file_path}"
        )

    report = progress or (lambda stage, details=None: None)

    # ========================================================
    # STEP 1: DOCUMENT PROCESSING
    # ========================================================

    report("extracting")

    pages = process_document(
        pdf_path
    )

    if not pages:
        raise ValueError(
            "No pages found in PDF."
        )

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
        _chunk_embed_and_store(document_id, pages, report)
    except Exception:
        # Never leave a half-ingested document behind.
        delete_document(document_id)
        raise

    supabase.table("documents").update({
        "processing_status": "COMPLETED"
    }).eq("id", document_id).execute()

    return document_id


def _chunk_embed_and_store(
    document_id: str,
    pages: list[dict],
    report
) -> None:

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
    # STEP 4: EMBEDDINGS
    # ========================================================

    report("embedding", {"chunks": len(all_chunks)})

    texts = [
        chunk["content"]
        for chunk in all_chunks
    ]

    embeddings = generate_embeddings(
        texts
    )

    if len(embeddings) != len(
        all_chunks
    ):
        raise RuntimeError(
            "Number of embeddings does not "
            "match number of chunks."
        )

    # ========================================================
    # STEP 5: ATTACH EMBEDDINGS
    # ========================================================

    for chunk, embedding in zip(
        all_chunks,
        embeddings
    ):

        chunk["embedding"] = embedding

    # ========================================================
    # STEP 6: STORE
    # ========================================================

    report("indexing", {"chunks": len(all_chunks)})

    store_chunks(
        document_id=document_id,
        chunks=all_chunks
    )