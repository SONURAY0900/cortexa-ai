from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from graph.workflow import chat_graph


BACKEND_DIR = Path(__file__).resolve().parents[2]

IMAGE_PATH = (
    BACKEND_DIR
    / "uploads"
    / "test_ocr.png"
)


query = "What information is written in this image?"


initial_state = {
    "query": query,
    "route": "",
    "route_reason": "",
    "tool_result": "",
    "final_response": "",
    "error": "",
    "chat_history": [],
    "sources": [],
    "document_id": None,

    # Image fields
    "image_path": str(IMAGE_PATH),
    "image_ocr_text": "",
    "image_vision_result": ""
}


try:

    print("\n==============================")
    print("LANGGRAPH IMAGE TEST")
    print("==============================")

    print(f"Image path: {IMAGE_PATH}")
    print(f"Image exists: {IMAGE_PATH.exists()}")

    if not IMAGE_PATH.exists():
        raise FileNotFoundError(
            f"Image not found:\n{IMAGE_PATH}"
        )

    print("\nRunning LangGraph...\n")

    result = chat_graph.invoke(
        initial_state
    )

    print("\n==============================")
    print("GRAPH RESULT")
    print("==============================")

    print("\nROUTE:")
    print("------------------------------")
    print(result.get("route"))

    print("\nROUTE REASON:")
    print("------------------------------")
    print(result.get("route_reason"))

    print("\nOCR TEXT:")
    print("------------------------------")
    print(result.get("image_ocr_text"))

    print("\nVISION RESULT:")
    print("------------------------------")
    print(result.get("image_vision_result"))

    print("\nFINAL RESPONSE:")
    print("------------------------------")
    print(result.get("final_response"))

    print("\nSOURCES:")
    print("------------------------------")
    print(result.get("sources"))

    print("\nERROR:")
    print("------------------------------")
    print(result.get("error"))

    print("\n==============================")

except Exception as e:

    print("\n==============================")
    print("LANGGRAPH IMAGE TEST FAILED")
    print("==============================")

    print(type(e).__name__)
    print(e)

    print("==============================")