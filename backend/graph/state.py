from typing import TypedDict, List


class ChatState(TypedDict):
    # ========================================================
    # COMMON CHAT STATE
    # ========================================================

    query: str

    route: str
    route_reason: str

    tool_result: str
    final_response: str

    error: str

    chat_history: List[dict]

    sources: list

    # ========================================================
    # PDF RAG
    # ========================================================

    document_id: str | None

    # ========================================================
    # IMAGE
    # ========================================================

    image_path: str | None

    image_ocr_text: str

    image_vision_result: str