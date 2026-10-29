"""
OCR + Vision analysis of one uploaded image, shared by the streaming
chat endpoint and the LangGraph image node.

`image_path` must be a server-resolved path (see
services.storage.resolve_inside_uploads); never a client value.

The two steps are exposed separately so callers can report progress
between them; analyze_image() runs both.
"""

from agents.ocr_agent import ocr_agent
from agents.vision_agent import vision_agent
from rag.ingestion.text_quality import script_share
from utils.logger import log_agent


def run_ocr(image_path: str, query: str) -> dict:
    log_agent("OCR", "Starting OCR agent")
    return ocr_agent(image_path=image_path, query=query)


def run_vision(image_path: str, query: str) -> dict:
    log_agent("VISION", "Starting Vision agent")
    return vision_agent(image_path=image_path, query=query)


def combine(ocr_result: dict, vision_result: dict, label: str = "") -> dict:
    ocr_text = ocr_result.get("ocr_text", "")
    vision_answer = vision_result.get("answer", "")

    errors = []
    if ocr_result.get("error"):
        errors.append(f"OCR error: {ocr_result['error']}")
    if vision_result.get("error"):
        errors.append(f"Vision error: {vision_result['error']}")

    title = f"IMAGE ANALYSIS RESULT{f' ({label})' if label else ''}"

    # OCR and the vision model read Latin script and Chinese well, but
    # misread others (Odia, Hindi, ...): the answer then says to check.
    caution = ""
    if ocr_text.strip() and script_share(ocr_text, {"LATIN", "CJK"}) < 0.5:
        caution = ("The text is in a script these readers often misread. Start the answer with one "
                   "short line asking the user to check the question and options against the image.\n")

    # The vision model misreads non-Latin scripts (it invented an Odia
    # question and options), so the exact wording comes from OCR.
    tool_result = f"""
{title}
Use OCR TEXT for the exact wording of questions and options; if it and
VISION ANALYSIS disagree, trust OCR TEXT. If the question cannot be read
clearly, say which part is unreadable instead of guessing.
{caution}
OCR TEXT:
{ocr_text}

VISION ANALYSIS:
{vision_answer}
"""

    return {
        "tool_result": tool_result,
        "ocr_text": ocr_text,
        "vision_answer": vision_answer,
        "ocr_ok": not ocr_result.get("error"),
        "vision_ok": not vision_result.get("error"),
        "error": "\n".join(errors),
        "unreliable_script": bool(caution),
    }


def analyze_image(image_path: str, query: str) -> dict:
    return combine(run_ocr(image_path, query), run_vision(image_path, query))
