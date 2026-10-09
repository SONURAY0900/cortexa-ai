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

    tool_result = f"""
{title}

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
    }


def analyze_image(image_path: str, query: str) -> dict:
    return combine(run_ocr(image_path, query), run_vision(image_path, query))
