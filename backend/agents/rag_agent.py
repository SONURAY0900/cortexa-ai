from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY
from prompts.rag_prompt import RAG_PROMPT
from rag.retriever import retrieve_chunks


rag_llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.2,
    api_key=GROQ_API_KEY
)


def rag_agent(
    query: str,
    document_id: str | None = None,
    match_count: int = 5
) -> dict:
    """
    Retrieve relevant PDF chunks and generate
    a grounded answer using Groq.
    """

    # --------------------------------------------------
    # 1. Retrieve relevant chunks
    # --------------------------------------------------

    chunks = retrieve_chunks(
        query=query,
        match_count=match_count,
        document_id=document_id
    )

    if not chunks:
        return {
            "answer": "I could not find this information in the uploaded PDF.",
            "sources": [],
            "chunks": []
        }

    # --------------------------------------------------
    # 2. Build PDF context
    # --------------------------------------------------

    context_parts = []

    sources = []

    for chunk in chunks:

        content = chunk.get("content", "")
        page_number = chunk.get("page_number")
        similarity = chunk.get("similarity", 0)

        context_parts.append(
            f"Page {page_number}:\n"
            f"{content}"
        )

        sources.append({
            "page_number": page_number,
            "similarity": similarity
        })

    context = "\n\n".join(context_parts)

    # --------------------------------------------------
    # 3. Build prompt
    # --------------------------------------------------

    prompt = RAG_PROMPT.format(
        query=query,
        context=context
    )

    # --------------------------------------------------
    # 4. Generate answer
    # --------------------------------------------------

    response = rag_llm.invoke(prompt)

    return {
        "answer": response.content,
        "sources": sources,
        "chunks": chunks
    }