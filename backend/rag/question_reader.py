"""
Read one numbered question from a PDF page image.

Text extraction loses the layout of determinants, matrices and stacked
fractions: question 23 of a maths paper came out as "23 23 23 1 10 1 xx x
yy y zz z + Δ= ...", and the model then invented a different question. When
the user asks for a numbered question ("question no 23", "Q23"), the page
that holds it is rendered and the vision model copies that question out,
formulas in LaTeX. One vision call per such request; any failure falls back
to the extracted text.
"""

import os
import re
import tempfile

import pymupdf

from tools.vision_tool import analyze_image
from utils.logger import log_agent

ASKED_QUESTION = re.compile(r"\b(?:question|ques|que|qn|q)\s*(?:no\.?|number|#)?\s*\.?\s*(\d{1,3})\b", re.IGNORECASE)
RENDER_DPI = 130
# Small, so this call plus an image analysis (600) stays under the free
# tier's 1,000 output tokens per minute for the vision model.
MAX_TOKENS = 300
NOT_ON_PAGE = "NOT ON PAGE"


def asked_question(query: str) -> int | None:
    """The question number the user asks about, e.g. 23 for "solve que no 23"."""
    match = ASKED_QUESTION.search(query or "")
    return int(match.group(1)) if match else None


def page_of_question(chunks: list[dict], number: int) -> int | None:
    """Page whose text starts question `number` ("23. If x ..."), if retrieved."""
    marker = re.compile(rf"(?<![\d.]){number}\.\s")
    for chunk in chunks:
        if marker.search(chunk.get("content") or "") and chunk.get("page_number"):
            return chunk["page_number"]
    return None


def read_question(pdf_path: str, page_number: int, number: int) -> str | None:
    """Question `number` as printed on the page, or None if it can't be read."""
    handle, image_path = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    try:
        with pymupdf.open(pdf_path) as document:
            document[page_number - 1].get_pixmap(dpi=RENDER_DPI).save(image_path)
        text = analyze_image(
            image_path,
            f"Copy out question {number} from this exam page exactly as printed, with every part, "
            f"option and formula. Write all mathematics in LaTeX. Output only the question. "
            f"If question {number} is not on this page, output {NOT_ON_PAGE}.",
            max_tokens=MAX_TOKENS,
        )
    except Exception as error:
        log_agent("RAG", f"Reading question {number} from page {page_number} failed: {error!r}")
        return None
    finally:
        os.remove(image_path)

    text = (text or "").strip()
    return None if not text or NOT_ON_PAGE in text else text
