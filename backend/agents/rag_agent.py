import itertools
import re

from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_REASONING_EFFORT, GROQ_TIMEOUT_S
from agents.llm_agent import answer_style, clip, generate_response, history_lines
from prompts.rag_prompt import RAG_PROMPT
from rag.question_reader import asked_question, page_of_question, read_question
from rag.retrieval.retrieval_pipeline import retrieve_context
from rag.retrieval.vector_store import document_chunks_in_order


rag_llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.2,
    api_key=GROQ_API_KEY,
    timeout=GROQ_TIMEOUT_S,
    max_retries=GROQ_MAX_RETRIES,
    reasoning_effort=GROQ_REASONING_EFFORT,
)

# Characters of chunk text shown as a citation preview in the UI.
SNIPPET_CHARS = 220

# Image analysis text added to a PDF question (keeps the prompt within
# Groq's per-request limit; the PDF context itself is capped by retrieval).
IMAGE_CONTEXT_CHARS = 3000

NOT_FOUND = "I could not find this information in the uploaded PDF."

# What the PDF prompt answers when the PDF does not cover a *general*
# question; the chat then answers it from its usual sources instead.
NOT_IN_PDF = "NOT_IN_PDF"


def not_in_pdf(stream) -> tuple[bool, object]:
    """
    Look at the start of a streamed PDF answer. Returns (True, None) when
    the model replied NOT_IN_PDF, otherwise (False, chunks) with every chunk,
    including the ones read here, still to be streamed.
    """

    buffered, text = [], ""
    for chunk in stream:
        buffered.append(chunk)
        text += chunk
        head = text.lstrip(" \n*`")
        if len(head) >= len(NOT_IN_PDF) or not NOT_IN_PDF.startswith(head):
            break

    if text.lstrip(" \n*`").startswith(NOT_IN_PDF):
        return True, None
    return False, itertools.chain(buffered, stream)

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


# "Give all questions", "list every MCQ", "summarise the whole PDF": the
# answer needs the whole document, not the best-matching chunks.
WHOLE_DOCUMENT = re.compile(
    r"\b(all|every|each|whole|entire|full|complete)\b.{0,40}\b(questions?|mcqs?|problems?|exercises?|pages?|document|pdf|paper|text)\b"
    r"|\bsummar(?:y|i[sz]e)\b",
    re.IGNORECASE,
)
# Whole-document text sent to the model: with the prompt and a long answer
# this stays inside Groq's per-request limit.
WHOLE_DOCUMENT_CHARS = 12000


def whole_document_context(document_id: str) -> tuple[str, list[dict]]:
    """The document's text in page order, up to WHOLE_DOCUMENT_CHARS."""

    chunks = document_chunks_in_order(document_id)
    kept, used = [], 0
    for chunk in chunks:
        text = (chunk.get("content") or "").strip()
        if text and used + len(text) > WHOLE_DOCUMENT_CHARS and kept:
            break
        if text:
            kept.append(chunk)
            used += len(text)

    context = "\n\n".join(
        f"[Source {i} | Page {c.get('page_number')} | Chunk {c.get('chunk_index')}]\n{c['content'].strip()}"
        for i, c in enumerate(kept, start=1)
    )
    # A concrete checklist: told only "include every item", the model still
    # skipped questions (16 -> 21) when listing a 37-question paper.
    numbers = sorted({int(n) for n in re.findall(r"(?<![\d.])(\d{1,3})\.\s", context) if 0 < int(n) < 300})
    run = [n for i, n in enumerate(numbers, start=1) if n == i]
    if len(run) >= 5:
        context = (f"[This text contains questions numbered 1 to {run[-1]}. When asked for all questions, "
                   f"include every number from 1 to {run[-1]}.]\n\n{context}")
    if len(kept) < len(chunks):
        last, end = kept[-1].get("page_number"), chunks[-1].get("page_number")
        context += (f"\n\n[Only the text up to page {last} fits here; the PDF continues to page {end}. "
                    f"Tell the user to ask for the rest by page range, e.g. \"pages {last + 1 if isinstance(last, int) else ''}"
                    f"-{end}\".]")
    return context, kept


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

    documents: list of {"id", "filename", "path"?}. With several documents
    each is retrieved independently and its context is labelled by file
    name. With a stored file ("path") and a numbered question in the query
    ("question no 23"), that question is also read from the page image
    (see rag/question_reader.py), since extraction breaks formulas.
    chat_history: lets follow-up questions be searched with their topic
    (see retrieval_query).

    Returns {"context": str, "sources": [...], "chunks": [...]}; empty
    chunks mean nothing relevant was found.
    """

    contexts, sources, all_chunks = [], [], []
    labelled = len(documents) > 1
    search = retrieval_query(query, chat_history)
    number = asked_question(query)

    whole = bool(WHOLE_DOCUMENT.search(query or ""))

    for document in documents:
        if whole:
            context, context_chunks = whole_document_context(document["id"])
        else:
            retrieval = retrieve_context(
                query=search,
                document_id=document["id"],
            )
            context_chunks = retrieval.get("context_chunks") or []
            context = retrieval.get("context") or ""

        if not context_chunks or not context.strip():
            continue

        page = page_of_question(context_chunks, number) if number and document.get("path") else None
        printed = read_question(document["path"], page, number) if page else None
        if printed:
            context = (f"Question {number} as printed on page {page} (read from the page image; use this "
                       f"wording, the extracted text below may have broken formulas):\n{printed}\n\n{context}")

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

    prompt = RAG_PROMPT.format(query=query, context=context) + answer_style(query)

    history = history_lines(chat_history)

    if history:
        prompt = (
            "Previous conversation (only to understand what the user is referring to; "
            "it is not a source of facts):\n"
            f"{history}\n{prompt}"
        )

    return prompt


def output_budget(prompt: str) -> int:
    """
    Output tokens to request. Groq's free tier checks prompt + requested
    output against 8,000 tokens per request, and without a value it stops
    at 3,072, which cut "give all questions" off at question 35 of 37.
    Prompt tokens are estimated generously at 3 characters each.
    """
    return max(1024, min(4096, 7800 - len(prompt) // 3))


def stream_pdf_answer(
    query: str,
    context: str,
    chat_history: list | None = None,
    image_context: str = "",
):
    """Stream a grounded answer from already retrieved PDF context."""

    prompt = build_pdf_prompt(query, context, chat_history, image_context)

    for chunk in rag_llm.stream(prompt, max_tokens=output_budget(prompt)):
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

    if retrieval["chunks"]:
        prompt = build_pdf_prompt(query, retrieval["context"])
        answer = rag_llm.invoke(prompt, max_tokens=output_budget(prompt)).content
        if not answer.lstrip(" \n*`").startswith(NOT_IN_PDF):
            return {
                "answer": answer,
                "sources": retrieval["sources"],
                "chunks": retrieval["chunks"]
            }

    # A general question the PDF does not cover: answered from general
    # knowledge and said so (/chat/stream also routes it to web search etc.).
    return {
        "answer": "**This isn't covered in your PDF**, so here's an answer from general knowledge:\n\n"
                  + generate_response(query),
        "sources": [],
        "chunks": []
    }
