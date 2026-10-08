from graph.state import ChatState
from graph.router import route_query

from agents.llm_agent import generate_response
from agents.wikipedia_agent import wikipedia_agent
from agents.tavily_agent import tavily_agent
from agents.arxiv_agent import arxiv_agent

from agents.rag_agent import rag_agent

from agents.ocr_agent import ocr_agent
from agents.vision_agent import vision_agent


# ============================================================
# ROUTER NODE
# ============================================================

def router_node(state: ChatState):

    query = state["query"]

    # --------------------------------------------------------
    # PDF takes priority
    # --------------------------------------------------------

    if state.get("document_id"):

        return {
            "route": "PDF_RAG",
            "route_reason": "PDF document attached.",
            "error": ""
        }

    # --------------------------------------------------------
    # Image takes priority
    # --------------------------------------------------------

    if state.get("image_path"):

        return {
            "route": "IMAGE",
            "route_reason": "Image attached.",
            "error": ""
        }

    # --------------------------------------------------------
    # Otherwise use existing TEXT router
    # --------------------------------------------------------

    try:

        decision = route_query(query)

        return {
            "route": decision.route,
            "route_reason": decision.reason,
            "error": ""
        }

    except Exception as e:

        return {
            "route": "DIRECT",
            "route_reason": (
                "Router failed, using direct response."
            ),
            "error": f"Router error: {str(e)}"
        }


# ============================================================
# DIRECT NODE
# ============================================================

def direct_node(state: ChatState):

    return {
        "tool_result": "",
        "sources": [],
        "error": ""
    }


# ============================================================
# WIKIPEDIA NODE
# ============================================================

def wikipedia_node(state: ChatState):

    query = state["query"]

    try:

        result = wikipedia_agent(query)

        # ----------------------------------------------------
        # DEBUG
        # ----------------------------------------------------

        print(
            "\n================ WIKIPEDIA DEBUG ================"
        )

        print("WIKIPEDIA RESULT:")
        print(result)

        print("\nWIKIPEDIA SOURCES:")
        print(result.get("sources", []))

        print(
            "==================================================\n"
        )

        return {
            "tool_result": result.get(
                "content",
                ""
            ),

            "sources": result.get(
                "sources",
                []
            ),

            "error": result.get(
                "error",
                ""
            )
        }

    except Exception as e:

        return {
            "tool_result": "",
            "sources": [],
            "error": f"Wikipedia error: {str(e)}"
        }


# ============================================================
# TAVILY NODE
# ============================================================

def tavily_node(state: ChatState):

    query = state["query"]

    try:

        result = tavily_agent(query)

        return {
            "tool_result": result.get(
                "content",
                ""
            ),

            "sources": result.get(
                "sources",
                []
            ),

            "error": result.get(
                "error",
                ""
            )
        }

    except Exception as e:

        return {
            "tool_result": "",
            "sources": [],
            "error": f"Tavily error: {str(e)}"
        }


# ============================================================
# ARXIV NODE
# ============================================================

def arxiv_node(state: ChatState):

    query = state["query"]

    try:

        result = arxiv_agent(query)

        return {
            "tool_result": result.get(
                "content",
                ""
            ),

            "sources": result.get(
                "sources",
                []
            ),

            "error": result.get(
                "error",
                ""
            )
        }

    except Exception as e:

        return {
            "tool_result": "",
            "sources": [],
            "error": f"arXiv error: {str(e)}"
        }


# ============================================================
# PDF RAG NODE
# ============================================================

def rag_node(state: ChatState):

    query = state["query"]

    document_id = state.get(
        "document_id"
    )

    # --------------------------------------------------------
    # Make sure a PDF is actually attached
    # --------------------------------------------------------

    if not document_id:

        return {
            "tool_result": "",
            "sources": [],
            "error": (
                "No PDF document is attached to this request."
            )
        }

    try:

        result = rag_agent(
            query=query,
            document_id=document_id,
            match_count=5
        )

        return {
            "tool_result": result.get(
                "answer",
                ""
            ),

            "sources": result.get(
                "sources",
                []
            ),

            "error": ""
        }

    except Exception as e:

        return {
            "tool_result": "",
            "sources": [],
            "error": f"PDF RAG error: {str(e)}"
        }

# ============================================================
# IMAGE OCR + VISION NODE
# ============================================================

def image_node(state: ChatState):

    query = state["query"]

    image_path = state.get(
        "image_path"
    )

    # --------------------------------------------------------
    # Make sure an image is actually attached
    # --------------------------------------------------------

    if not image_path:

        return {
            "tool_result": "",
            "image_ocr_text": "",
            "image_vision_result": "",
            "sources": [],
            "error": (
                "No image is attached to this request."
            )
        }

    try:

        print(
            "\n================ IMAGE ANALYSIS ================"
        )

        print(
            f"Image path: {image_path}"
        )

        # ----------------------------------------------------
        # OCR
        # ----------------------------------------------------

        print("\n[IMAGE] Running OCR...")

        ocr_result = ocr_agent(
            image_path=image_path,
            query=query
        )

        ocr_text = ocr_result.get(
            "ocr_text",
            ""
        )

        ocr_error = ocr_result.get(
            "error",
            ""
        )

        print(
            f"[IMAGE] OCR completed. "
            f"Characters: {len(ocr_text)}"
        )

        # ----------------------------------------------------
        # Vision
        # ----------------------------------------------------

        print("\n[IMAGE] Running Vision...")

        vision_result = vision_agent(
            image_path=image_path,
            query=query
        )

        vision_answer = vision_result.get(
            "answer",
            ""
        )

        vision_error = vision_result.get(
            "error",
            ""
        )

        print(
            "[IMAGE] Vision completed."
        )

        print(
            "================================================\n"
        )

        # ----------------------------------------------------
        # Handle OCR + Vision errors
        # ----------------------------------------------------

        errors = []

        if ocr_error:
            errors.append(
                f"OCR error: {ocr_error}"
            )

        if vision_error:
            errors.append(
                f"Vision error: {vision_error}"
            )

        combined_error = "\n".join(errors)

        # ----------------------------------------------------
        # Combine OCR + Vision information
        # ----------------------------------------------------

        tool_result = f"""
        IMAGE ANALYSIS RESULT

        OCR TEXT:
        {ocr_text}

        VISION ANALYSIS:
        {vision_answer}
       """

        return {
            "tool_result": tool_result,
            "image_ocr_text": ocr_text,
            "image_vision_result": vision_answer,
            "sources": [],
            "error": combined_error
        }

    except Exception as e:

        return {
            "tool_result": "",
            "image_ocr_text": "",
            "image_vision_result": "",
            "sources": [],
            "error": f"Image analysis error: {str(e)}"
        }

# ============================================================
# FINAL LLM NODE
# ============================================================

def llm_node(state: ChatState):

    query = state["query"]

    tool_result = state.get(
        "tool_result",
        ""
    )

    error = state.get(
        "error",
        ""
    )

    chat_history = state.get(
        "chat_history",
        []
    )

    sources = state.get(
        "sources",
        []
    )

    # --------------------------------------------------------
    # Handle tool/RAG errors
    # --------------------------------------------------------

    if error:

        tool_result = f"""
The requested tool encountered an error.

Error:
{error}

Please provide the user with a helpful response explaining
that the requested external service is temporarily unavailable.

Do not expose internal implementation details.
"""

    try:

        response = generate_response(
            query=query,
            tool_result=tool_result,
            chat_history=chat_history
        )

        # ----------------------------------------------------
        # Update conversation history
        # ----------------------------------------------------

        updated_history = chat_history + [
            {
                "role": "user",
                "content": query
            },
            {
                "role": "assistant",
                "content": response
            }
        ]

        return {
            "final_response": response,
            "chat_history": updated_history,
            "sources": sources,
            "error": ""
        }

    except Exception as e:

        return {
            "final_response": (
                "Sorry, I was unable to generate "
                "a response right now."
            ),

            "chat_history": chat_history,

            "sources": sources,

            "error": f"LLM error: {str(e)}"
        }