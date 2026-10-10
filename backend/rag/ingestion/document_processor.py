"""
Document ingestion orchestration.

Responsible for:

PDF loading
    ↓
OCR fallback
    ↓
Text cleaning

This module does NOT:

- create chunks
- generate embeddings
- write to Supabase
- perform retrieval
"""

from pathlib import Path

from rag.ingestion.pdf_loader import extract_text_from_pdf
from rag.ingestion.text_cleaner import clean_text


def process_document(file_path: str | Path, progress=None) -> list[dict]:
    """
    Process a PDF into cleaned document pages.

    Returns one dict per page with the cleaned "text" plus the extraction
    details from pdf_loader (source, ocr_status, ocr_error, ...):

    [
        {
            "page_number": 1,
            "text": "...",
            "source": "text" | "ocr",
            ...
        },
        ...
    ]
    """

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"Document not found: {file_path}"
        )

    pages = extract_text_from_pdf(
        str(file_path),
        progress=progress
    )

    processed_pages = []

    for page in pages:

        page_number = page.get(
            "page_number"
        )

        raw_text = page.get(
            "text",
            ""
        )

        cleaned_text = clean_text(
            raw_text
        )

        processed_pages.append(
            {
                **page,
                "page_number": page_number,
                "text": cleaned_text,
            }
        )

    return processed_pages