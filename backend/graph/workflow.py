from langgraph.graph import StateGraph, START, END

from graph.state import ChatState

from graph.nodes import (
    router_node,
    direct_node,
    wikipedia_node,
    tavily_node,
    arxiv_node,
    rag_node,
    image_node,
    llm_node
)


def route_decision(state: ChatState):

    return state["route"]


builder = StateGraph(ChatState)


# ============================================================
# NODES
# ============================================================

builder.add_node(
    "router",
    router_node
)

builder.add_node(
    "direct",
    direct_node
)

builder.add_node(
    "wikipedia",
    wikipedia_node
)

builder.add_node(
    "tavily",
    tavily_node
)

builder.add_node(
    "arxiv",
    arxiv_node
)

builder.add_node(
    "rag",
    rag_node
)

builder.add_node(
    "image",
    image_node
)

builder.add_node(
    "llm",
    llm_node
)


# ============================================================
# START
# ============================================================

builder.add_edge(
    START,
    "router"
)


# ============================================================
# ROUTING
# ============================================================

builder.add_conditional_edges(
    "router",
    route_decision,
    {
        "DIRECT": "direct",
        "WIKIPEDIA": "wikipedia",
        "TAVILY": "tavily",
        "ARXIV": "arxiv",
        "PDF_RAG": "rag",
        "IMAGE": "image"
    }
)


# ============================================================
# TOOL → FINAL LLM
# ============================================================

builder.add_edge(
    "direct",
    "llm"
)

builder.add_edge(
    "wikipedia",
    "llm"
)

builder.add_edge(
    "tavily",
    "llm"
)

builder.add_edge(
    "arxiv",
    "llm"
)

builder.add_edge(
    "rag",
    "llm"
)

builder.add_edge(
    "image",
    "llm"
)


# ============================================================
# FINAL
# ============================================================

builder.add_edge(
    "llm",
    END
)


chat_graph = builder.compile()