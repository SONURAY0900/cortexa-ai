from pathlib import Path

import pymupdf
from pypdf import PdfReader

from rag.ingestion.ocr_processor import extract_text_with_ocr
from rag.ingestion.text_quality import suspicious_ratio

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
            print(
                f"[PDF] Page {page_number}: pypdf text looks corrupted "
                f"({ratio:.3f}); using PyMuPDF text ({alternative_ratio:.3f})."
            )
            return alternative

        return pypdf_text

    alternative = fallback_text()

    if has_enough_text(alternative):
        print(
            f"[PDF] Page {page_number}: pypdf found no text; "
            f"using PyMuPDF text."
        )
        return alternative

    return pypdf_text


def extract_text_from_pdf(file_path: str) -> list[dict]:
    """
    Extract text from a PDF.

    Normal selectable-text pages are extracted using pypdf.
    Pages whose pypdf text is missing or looks corrupted are re-extracted
    with PyMuPDF. Pages still without enough text are sent to OCR.space.
    """

    pdf_path = Path(file_path)

    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF file not found: {file_path}"
        )

    reader = PdfReader(str(pdf_path))

    fallback_document = None

    def fallback_page_text(index: int) -> str:
        nonlocal fallback_document
        try:
            if fallback_document is None:
                fallback_document = pymupdf.open(str(pdf_path))
            return fallback_document[index].get_text() or ""
        except Exception as error:
            print(f"[PDF] PyMuPDF extraction failed: {error}")
            return ""

    pages = []

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        print(
            f"\n[PDF] Processing page {page_number}..."
        )

        # --------------------------------------------------
        # STEP 1: Normal PDF text extraction
        # (PyMuPDF fallback for missing / corrupted text)
        # --------------------------------------------------

        text = _choose_page_text(
            pypdf_text=page.extract_text() or "",
            fallback_text=lambda index=page_number - 1: fallback_page_text(index),
            page_number=page_number,
        )

        # --------------------------------------------------
        # STEP 2: Check whether selectable text exists
        # --------------------------------------------------

        if has_enough_text(text):

            print(
                f"[PDF] Page {page_number}: "
                f"Selectable text detected."
            )

            pages.append(
                {
                    "page_number": page_number,
                    "text": text
                }
            )

        # --------------------------------------------------
        # STEP 3: OCR fallback
        # --------------------------------------------------

        else:

            print(
                f"[PDF] Page {page_number}: "
                f"No sufficient text detected."
            )

            print(
                f"[OCR] Page {page_number}: "
                f"Sending to OCR.space..."
            )

            try:

                ocr_text = extract_text_with_ocr(
                    file_path=str(pdf_path),
                    page_number=page_number
                )

                if ocr_text.strip():

                    print(
                        f"[OCR] Page {page_number}: "
                        f"OCR successful."
                    )

                    pages.append(
                        {
                            "page_number": page_number,
                            "text": ocr_text
                        }
                    )

                else:

                    print(
                        f"[OCR] Page {page_number}: "
                        f"No text detected."
                    )

                    pages.append(
                        {
                            "page_number": page_number,
                            "text": ""
                        }
                    )

            except Exception as e:

                print(
                    f"[OCR] Page {page_number}: "
                    f"OCR failed: {str(e)}"
                )

                pages.append(
                    {
                        "page_number": page_number,
                        "text": ""
                    }
                )

    if fallback_document is not None:
        fallback_document.close()

    return pages