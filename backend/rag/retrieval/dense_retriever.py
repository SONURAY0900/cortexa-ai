from database.connection import supabase
from rag.embeddings.embeddings import generate_embedding


DEFAULT_CANDIDATE_COUNT = 20


def retrieve_dense(
    query: str,
    document_id: str | None = None,
    match_count: int = DEFAULT_CANDIDATE_COUNT,
) -> list[dict]:
    """
    Retrieve candidate chunks using dense vector similarity.

    The database performs cosine-distance search through
    the match_document_chunks RPC.
    """

    if not query or not query.strip():
        return []

    query_embedding = generate_embedding(query)

    response = supabase.rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "match_count": match_count,
            "filter_document_id": document_id,
        },
    ).execute()

    results = response.data or []

    formatted_results = []

    for rank, result in enumerate(results, start=1):
        formatted_results.append(
            {
                **result,
                "dense_rank": rank,
                "dense_score": float(
                    result.get("similarity", 0.0)
                ),
            }
        )

    print("\n[DENSE RETRIEVAL]")
    print(f"Query: {query}")
    print(f"Candidates: {len(formatted_results)}")

    for result in formatted_results:
        print(
            f"  Rank {result['dense_rank']} | "
            f"Page {result.get('page_number')} | "
            f"Score {result['dense_score']:.4f}"
        )

    return formatted_results