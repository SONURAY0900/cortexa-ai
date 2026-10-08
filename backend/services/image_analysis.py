"""
OCR + Vision analysis of one uploaded image, shared by the streaming
chat endpoint and the LangGraph image node.

`image_path` must be a server-resolved path (see
services.storage.resolve_inside_uploads); never a client value.
"""

from agents.ocr_agent import ocr_agent
from agents.vision_agent import vision_agent
from utils.logger import log_agent


def analyze_image(image_path: str, query: str) -> dict:

    log_agent("OCR", "Starting OCR agent")

    ocr_result = ocr_agent(
        image_path=image_path,
        query=query
    )

    log_agent("VISION", "Starting Vision agent")

    vision_result = vision_agent(
        image_path=image_path,
        query=query
    )

    ocr_text = ocr_result.get("ocr_text", "")
    vision_answer = vision_result.get("answer", "")

    errors = []

    if ocr_result.get("error"):
        errors.append(f"OCR error: {ocr_result['error']}")

    if vision_result.get("error"):
        errors.append(f"Vision error: {vision_result['error']}")

    tool_result = f"""
IMAGE ANALYSIS RESULT

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
