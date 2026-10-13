import re

from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_TIMEOUT_S
from agents.llm_agent import clip, history_lines
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

# Image analysis text added to a PDF question (keeps the prompt within
# Groq's per-request limit; the PDF context itself is capped by retrieval).
IMAGE_CONTEXT_CHARS = 3000

NOT_FOUND = "I could not find this information in the uploaded PDF."

# A follow-up such as "and what is its time complexity?" has no topic words
# of its own, so searching the PDF with it alone finds the wrong pages.
# Such questions are searched together with the previous user question.
FOLLOW_UP = re.compile(
    r"^\s*(?:and|also|then|so|what about|how about)\b"
    r"|\b(?:it|its|it's|this|that|these|those|they|them|their|above|same)\b",
    re.IGNORECASE,
)
FOLLOW_UP_MAX_WORDS = 12


def retrieval_query(query: str, chat_history: list | None = None) -> str:
    """The text used to search the PDF (the prompt keeps the real question)."""

    if not chat_history or len(query.split()) > FOLLOW_UP_MAX_WORDS or not FOLLOW_UP.search(query):
        return query

    previous = next(
        (m.get("content") or "" for m in reversed(chat_history) if m.get("role") == "user"),
        "",
    )
    previous = " ".join(previous.split())[:300]

    return f"{previous} {query}" if previous else query


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


def retrieve_pdf_context(
    query: str,
    documents: list[dict],
    chat_history: list | None = None,
) -> dict:
    """
    Retrieve context for `query` from each document with the evaluated
    production retrieval pipeline (query transformation, hybrid
    retrieval, reranking, diversity selection, neighbor-aware context
    building). No LLM call.

    documents: list of {"id", "filename"}. With several documents each is
    retrieved independently and its context is labelled by file name.
    chat_history: lets follow-up questions be searched with their topic
    (see retrieval_query).

    Returns {"context": str, "sources": [...], "chunks": [...]}; empty
    chunks mean nothing relevant was found.
    """

    contexts, sources, all_chunks = [], [], []
    labelled = len(documents) > 1
    search = retrieval_query(query, chat_history)

    for document in documents:
        retrieval = retrieve_context(
            query=search,
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

    return {
        "context": "\n\n".join(contexts),
        "sources": sources,
        "chunks": all_chunks,
    }


def build_pdf_prompt(
    query: str,
    context: str,
    chat_history: list | None = None,
    image_context: str = "",
) -> str:
    """
    The grounded PDF prompt. Recent chat history is included only so
    follow-up questions ("explain that again") can be understood; the
    prompt tells the model it is not a source of facts.
    """

    if image_context:
        context = f"{context}\n\nAttached image analysis:\n{clip(image_context, IMAGE_CONTEXT_CHARS)}"

    prompt = RAG_PROMPT.format(query=query, context=context)

    history = history_lines(chat_history)

    if history:
        prompt = (
            "Previous conversation (only to understand what the user is referring to; "
            "it is not a source of facts):\n"
            f"{history}\n{prompt}"
        )

    return prompt


def stream_pdf_answer(
    query: str,
    context: str,
    chat_history: list | None = None,
    image_context: str = "",
):
    """Stream a grounded answer from already retrieved PDF context."""

    prompt = build_pdf_prompt(query, context, chat_history, image_context)

    for chunk in rag_llm.stream(prompt):
        if chunk.content:
            yield chunk.content


def rag_agent(
    query: str,
    document_id: str | None = None,
    documents: list[dict] | None = None,
) -> dict:
    """
    Retrieve context and generate a grounded answer (non-streaming).

    documents: optional list of {"id", "filename"} when several PDFs are
    attached. With a single document the behaviour is identical to
    document_id.
    """

    if not documents:
        documents = [{"id": document_id, "filename": None}]

    retrieval = retrieve_pdf_context(query, documents)

    if not retrieval["chunks"]:
        return {
            "answer": NOT_FOUND,
            "sources": [],
            "chunks": []
        }

    response = rag_llm.invoke(build_pdf_prompt(query, retrieval["context"]))

    return {
        "answer": response.content,
        "sources": retrieval["sources"],
        "chunks": retrieval["chunks"]
    }
