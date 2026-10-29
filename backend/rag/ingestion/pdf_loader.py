from pathlib import Path

import pymupdf
from pypdf import PdfReader

from config import settings
from rag.ingestion.ocr_processor import ocr_pdf_page
from rag.ingestion.text_quality import suspicious_ratio
from utils.logger import log_agent

MIN_TEXT_CHARS = 30

# Share of suspicious words/characters (see text_quality.py) above which
# pypdf output is treated as corrupted and PyMuPDF is tried instead.
# Clean pages in English, Hindi and Odia test PDFs score below 0.03;
# pages with broken font mappings score 0.06-0.20.
SUSPICIOUS_TEXT_RATIO = 0.05


def has_enough_text(text: str) -> bool:
    """
    Check whether normal PDF extraction
    produced enough usable text.
    """

    if not text:
        return False

    return len(text.strip()) >= MIN_TEXT_CHARS


def _choose_page_text(
    pypdf_text: str,
    fallback_text,
    page_number: int,
) -> str:
    """
    Keep the pypdf text unless it is missing or looks corrupted.

    In those cases the PyMuPDF text for the same page is used when it is
    better. pypdf output for clean pages is never replaced, so existing
    behaviour for well-formed PDFs is unchanged.
    """

    if has_enough_text(pypdf_text):

        ratio = suspicious_ratio(pypdf_text)

        if ratio <= SUSPICIOUS_TEXT_RATIO:
            return pypdf_text

        alternative = fallback_text()
        alternative_ratio = suspicious_ratio(alternative)

        if (
            has_enough_text(alternative)
            and alternative_ratio < ratio
        ):
            log_agent(
                "PDF",
                f"Page {page_number}: pypdf text looks corrupted "
                f"({ratio:.3f}); using PyMuPDF text ({alternative_ratio:.3f})."
            )
            return alternative

        return pypdf_text

    alternative = fallback_text()

    if has_enough_text(alternative):
        log_agent("PDF", f"Page {page_number}: pypdf found no text; using PyMuPDF text.")
        return alternative

    return pypdf_text


def extract_text_from_pdf(file_path: str, progress=None) -> list[dict]:
    """
    Extract text from a PDF, page by page.

    1. Embedded text: pypdf, with PyMuPDF for pages whose pypdf text is
       missing or looks corrupted.
    2. OCR (OCR.space) for pages that still have too little text. Each
       page is OCR'd independently; a failed page never stops the others.

    Returns one dict per page:

        page_number   1-based
        text          extracted text ("" if none)
        source        "text" (embedded) or "ocr"
        ocr_status    None for embedded text, else "success" | "empty" | "failed"
        ocr_error     short failure code (e.g. "E502") when ocr_status == "failed"
        ocr_attempts  number of OCR requests made for the page

    progress: optional callable(stage, details) receiving "ocr" events
    with the page being read and how many OCR pages there are.
    """

    report = progress or (lambda stage, details=None: None)
    pdf_path = Path(file_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    reader = PdfReader(str(pdf_path))
    pages: list[dict] = []
    fallback_document = None

    try:

        def fallback_page_text(index: int) -> str:
            nonlocal fallback_document
            try:
                if fallback_document is None:
                    fallback_document = pymupdf.open(str(pdf_path))
                return fallback_document[index].get_text() or ""
            except Exception as error:
                log_agent("PDF", f"PyMuPDF extraction failed: {error.__class__.__name__}")
                return ""

        # ---------------- pass 1: embedded text ----------------
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                pypdf_text = page.extract_text() or ""
            except Exception as error:
                log_agent("PDF", f"Page {page_number}: pypdf extraction failed: {error.__class__.__name__}")
                pypdf_text = ""

            text = _choose_page_text(
                pypdf_text=pypdf_text,
                fallback_text=lambda index=page_number - 1: fallback_page_text(index),
                page_number=page_number,
            )

            if has_enough_text(text):
                pages.append({"page_number": page_number, "text": text, "source": "text",
                              "ocr_status": None, "ocr_error": None, "ocr_attempts": 0})
            else:
                pages.append({"page_number": page_number, "text": "", "source": "ocr",
                              "ocr_status": "pending", "ocr_error": None, "ocr_attempts": 0})

    finally:
        if fallback_document is not None:
            fallback_document.close()

    # ---------------- pass 2: OCR for pages without text ----------------
    ocr_pages = [p for p in pages if p["source"] == "ocr"]

    log_agent("PDF", f"{len(pages)} page(s): {len(pages) - len(ocr_pages)} with embedded text, "
                     f"{len(ocr_pages)} need OCR.")

    # Each scanned page costs one OCR.space call (free quota, ~seconds each):
    # past the cap, pages are reported unreadable instead of being sent.
    limit = settings.MAX_OCR_PAGES
    if limit and len(ocr_pages) > limit:
        for page in ocr_pages[limit:]:
            page.update(ocr_status="failed", ocr_error="OCR_LIMIT")
        log_agent("OCR", f"{len(ocr_pages) - limit} scanned page(s) over the {limit}-page OCR limit are skipped.")
        ocr_pages = ocr_pages[:limit]

    for index, page in enumerate(ocr_pages, start=1):
        number = page["page_number"]
        report("ocr", {"page": number, "current": index, "total": len(ocr_pages)})
        log_agent("OCR", f"Page {number}: sending to OCR.space ({index}/{len(ocr_pages)})...")

        result = ocr_pdf_page(str(pdf_path), number)

        page["ocr_status"] = result.status
        page["ocr_attempts"] = result.attempts
        page["text"] = result.text if result.status == "success" else ""
        page["ocr_error"] = result.error if result.status == "failed" else None

        if result.status == "success":
            log_agent("OCR", f"Page {number}: OCR successful (engine {result.engine}, {result.attempts} attempt(s)).")
        elif result.status == "empty":
            log_agent("OCR", f"Page {number}: OCR found no text.")
        else:
            log_agent("OCR", f"Page {number}: OCR failed after {result.attempts} attempt(s): {', '.join(result.errors)}")

    return pages
