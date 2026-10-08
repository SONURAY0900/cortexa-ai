from database.connection import supabase
from rag.embeddings.embeddings import generate_embedding

# Minimum semantic similarity required for a vector result
MIN_VECTOR_SIMILARITY = 0.30


STOP_WORDS = {
    "what",
    "is",
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "to",
    "in",
    "on",
    "for",
    "with",
    "this",
    "that",
    "who",
    "where",
    "when",
    "how",
    "does",
    "did",
    "do",
    "are",
    "was",
    "were",
    "can",
    "could",
    "would",
    "should",
    "tell",
    "me",
    "about",
    "mentioned",
}


def extract_keywords(query: str) -> list[str]:

    words = query.lower().replace("?", "").split()

    keywords = []

    for word in words:

        word = word.strip(
            ".,!?;:'\"()[]{}"
        )

        if not word:
            continue

        if word in STOP_WORDS:
            continue

        if len(word) < 3:
            continue

        keywords.append(word)

    return list(dict.fromkeys(keywords))


def vector_search(
    query: str,
    match_count: int = 5,
    document_id: str | None = None
) -> list[dict]:

    query_embedding = generate_embedding(query)

    response = supabase.rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "match_count": match_count,
            "filter_document_id": document_id
        }
    ).execute()

    results = response.data or []

    print("\n[RETRIEVAL] Vector results:")

    for result in results:

        similarity = float(
            result.get("similarity", 0)
        )

        print(
            f"  Page {result.get('page_number')}: "
            f"similarity={similarity:.4f}"
        )

    return results


def keyword_search(
    keywords: list[str],
    match_count: int = 5,
    document_id: str | None = None
) -> list[dict]:

    if not keywords:
        return []

    results = []

    for keyword in keywords:

        response = supabase.rpc(
            "keyword_document_chunks",
            {
                "search_query": keyword,
                "match_count": match_count,
                "filter_document_id": document_id
            }
        ).execute()

        results.extend(
            response.data or []
        )

    return results


def retrieve_chunks(
    query: str,
    match_count: int = 5,
    document_id: str | None = None
) -> list[dict]:

    if not query or not query.strip():
        return []

    # --------------------------------
    # 1. Vector retrieval
    # --------------------------------

    vector_results = vector_search(
        query=query,
        match_count=match_count,
        document_id=document_id
    )

    # --------------------------------
    # 2. Apply semantic relevance gate
    # --------------------------------

    relevant_vector_results = []

    for result in vector_results:

        similarity = float(
            result.get("similarity", 0)
        )

        if similarity >= MIN_VECTOR_SIMILARITY:

            relevant_vector_results.append(
                result
            )

    print(
        "\n[RETRIEVAL] Relevant vector results:",
        len(relevant_vector_results)
    )

    # --------------------------------
    # 3. Extract important keywords
    # --------------------------------

    keywords = extract_keywords(query)

    print(
        "[RETRIEVAL] Keywords:",
        keywords
    )

    # --------------------------------
    # 4. Keyword retrieval
    # --------------------------------

    keyword_results = keyword_search(
        keywords=keywords,
        match_count=match_count,
        document_id=document_id
    )

    # --------------------------------
    # 5. Combine results
    # --------------------------------

    combined = {}

    # Add only relevant vector results

    for result in relevant_vector_results:

        chunk_id = result["id"]

        combined[chunk_id] = {
            **result,
            "vector_score": float(
                result.get("similarity", 0)
            ),
            "keyword_score": 0.0
        }

    # Add keyword results

    for result in keyword_results:

        chunk_id = result["id"]

        if chunk_id not in combined:

            combined[chunk_id] = {
                **result,
                "vector_score": 0.0,
                "keyword_score": 0.0
            }

        combined[chunk_id][
            "keyword_score"
        ] += 1.0

    results = list(
        combined.values()
    )

    if not results:
        print(
            "[RETRIEVAL] No relevant chunks found."
        )
        return []

    # --------------------------------
    # 6. Normalize vector score
    # --------------------------------

    vector_scores = [
        max(
            0.0,
            result["vector_score"]
        )
        for result in results
    ]

    max_vector = (
        max(vector_scores)
        if vector_scores
        else 0.0
    )

    # --------------------------------
    # 7. Normalize keyword score
    # --------------------------------

    max_keyword = max(
        result["keyword_score"]
        for result in results
    )

    # --------------------------------
    # 8. Calculate hybrid score
    # --------------------------------

    for result in results:

        if max_vector > 0:

            vector_normalized = (
                max(
                    0.0,
                    result["vector_score"]
                )
                / max_vector
            )

        else:

            vector_normalized = 0.0

        if max_keyword > 0:

            keyword_normalized = (
                result["keyword_score"]
                / max_keyword
            )

        else:

            keyword_normalized = 0.0

        result["hybrid_score"] = (
            0.5 * vector_normalized
            + 0.5 * keyword_normalized
        )

    # --------------------------------
    # 9. Sort by hybrid score
    # --------------------------------

    results.sort(
        key=lambda x: x["hybrid_score"],
        reverse=True
    )

    # --------------------------------
    # 10. Return top results
    # --------------------------------

    final_results = results[
        :match_count
    ]

    print(
        "\n[RETRIEVAL] Final chunks:"
    )

    for result in final_results:

        print(
            f"  Page {result.get('page_number')} | "
            f"vector={result.get('vector_score', 0):.4f} | "
            f"keyword={result.get('keyword_score', 0):.4f} | "
            f"hybrid={result.get('hybrid_score', 0):.4f}"
        )

    return final_results