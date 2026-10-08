from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY
from prompts.rag_prompt import RAG_PROMPT
from rag.retrieval.retrieval_pipeline import retrieve_context


rag_llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.2,
    api_key=GROQ_API_KEY
)

# Characters of chunk text shown as a citation preview in the UI.
SNIPPET_CHARS = 220


def _pdf_sources(context_chunks: list[dict]) -> list[dict]:
    """
    One citation per page, in the order the pages appear in the context.
    """

    sources = []
    seen_pages = set()

    for chunk in context_chunks:

        page_number = chunk.get("page_number")

        if page_number in seen_pages:
            continue

        seen_pages.add(page_number)

        content = " ".join((chunk.get("content") or "").split())

        sources.append({
            "type": "pdf",
            "title": f"Page {page_number}" if page_number else "PDF excerpt",
            "page_number": page_number,
            "chunk_index": chunk.get("chunk_index"),
            "snippet": content[:SNIPPET_CHARS],
        })

    return sources


def rag_agent(
    query: str,
    document_id: str | None = None,
) -> dict:
    """
    Retrieve context with the evaluated production retrieval pipeline
    (query transformation, hybrid retrieval, reranking, diversity
    selection, neighbor-aware context building) and generate a
    grounded answer using Groq.
    """

    # --------------------------------------------------
    # 1. Retrieve context
    # --------------------------------------------------

    retrieval = retrieve_context(
        query=query,
        document_id=document_id,
    )

    context_chunks = retrieval.get("context_chunks") or []
    context = retrieval.get("context") or ""

    if not context_chunks or not context.strip():
        return {
            "answer": "I could not find this information in the uploaded PDF.",
            "sources": [],
            "chunks": []
        }

    # --------------------------------------------------
    # 2. Generate answer
    # --------------------------------------------------

    prompt = RAG_PROMPT.format(
        query=query,
        context=context
    )

    response = rag_llm.invoke(prompt)

    return {
        "answer": response.content,
        "sources": _pdf_sources(context_chunks),
        "chunks": context_chunks
    }
