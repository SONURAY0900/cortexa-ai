from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_TIMEOUT_S
from prompts.rag_prompt import RAG_PROMPT
from rag.retrieval.retrieval_pipeline import retrieve_context


rag_llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.2,
    api_key=GROQ_API_KEY,
    timeout=GROQ_TIMEOUT_S,
    max_retries=GROQ_MAX_RETRIES,
)

# Characters of chunk text shown as a citation preview in the UI.
SNIPPET_CHARS = 220


def _pdf_sources(context_chunks: list[dict], document_name: str | None = None) -> list[dict]:
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
            **({"document": document_name} if document_name else {}),
        })

    return sources


def rag_agent(
    query: str,
    document_id: str | None = None,
    documents: list[dict] | None = None,
) -> dict:
    """
    Retrieve context with the evaluated production retrieval pipeline
    (query transformation, hybrid retrieval, reranking, diversity
    selection, neighbor-aware context building) and generate a
    grounded answer using Groq.

    documents: optional list of {"id", "filename"} when several PDFs are
    attached. Each document is retrieved independently with the same
    pipeline and the contexts are labelled by file name. With a single
    document the behaviour is identical to document_id.
    """

    if not documents:
        documents = [{"id": document_id, "filename": None}]

    # --------------------------------------------------
    # 1. Retrieve context (per document)
    # --------------------------------------------------

    contexts, sources, all_chunks = [], [], []
    labelled = len(documents) > 1

    for document in documents:
        retrieval = retrieve_context(
            query=query,
            document_id=document["id"],
        )

        context_chunks = retrieval.get("context_chunks") or []
        context = retrieval.get("context") or ""

        if not context_chunks or not context.strip():
            continue

        name = document.get("filename") if labelled else None
        contexts.append(f"Document: {name}\n{context}" if name else context)
        sources.extend(_pdf_sources(context_chunks, name))
        all_chunks.extend(context_chunks)

    if not all_chunks:
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
        context="\n\n".join(contexts)
    )

    response = rag_llm.invoke(prompt)

    return {
        "answer": response.content,
        "sources": sources,
        "chunks": all_chunks
    }
