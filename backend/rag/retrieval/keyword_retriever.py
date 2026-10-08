from collections import defaultdict

from database.connection import supabase
from rag.retrieval.text_tokens import unicode_tokens


# Common English words that do not provide useful
# information for keyword retrieval.
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "give",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "please",
    "tell",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "what",
    "were",
    "which",
    "who",
    "will",
    "with",
    "would",
    "you",
    "your",
    "document",
    "pdf",
}


def extract_keywords(query: str) -> list[str]:
    """
    Extract meaningful keywords from a natural-language query.
    """

    if not query:
        return []

    # Unicode-aware tokens (any script); identical to the previous
    # [a-zA-Z0-9]+ tokens for ASCII text.
    words = unicode_tokens(query)

    keywords = []

    for word in words:

        if len(word) < 2:
            continue

        if word in STOP_WORDS:
            continue

        if word not in keywords:
            keywords.append(word)

    return keywords


def retrieve_keyword(
    query: str,
    document_id: str | None = None,
    match_count: int = 10,
) -> list[dict]:
    """
    Retrieve document chunks using multiple keyword searches.

    Each meaningful query term is searched independently.
    Results are then merged and ranked by keyword matches.
    """

    keywords = extract_keywords(query)

    print("\n[KEYWORD RETRIEVAL]")
    print(f"Query: {query}")
    print(f"Keywords: {keywords}")

    if not keywords:
        print("No meaningful keywords found.")
        return []

    # Store merged results.
    merged_results = {}

    # Number of keyword matches for each chunk.
    keyword_match_counts = defaultdict(int)

    # Individual keyword scores for each chunk.
    keyword_scores = defaultdict(float)

    for keyword in keywords:

        try:
            response = (
                supabase
                .rpc(
                    "keyword_document_chunks",
                    {
                        "search_query": keyword,
                        "match_count": match_count,
                        "filter_document_id": document_id,
                    },
                )
                .execute()
            )

        except Exception as error:

            print(
                f"Keyword search failed for "
                f"'{keyword}': {error}"
            )

            continue

        rows = response.data or []

        for row in rows:

            chunk_id = row["id"]

            if chunk_id not in merged_results:
                merged_results[chunk_id] = row

            keyword_match_counts[
                chunk_id
            ] += 1

            keyword_scores[
                chunk_id
            ] += float(
                row.get(
                    "keyword_score",
                    0.0,
                )
            )

    # Add our combined keyword score.
    results = []

    for chunk_id, row in merged_results.items():

        result = dict(row)

        result["keyword_match_count"] = (
            keyword_match_counts[chunk_id]
        )

        result["keyword_score"] = (
            keyword_scores[chunk_id]
        )

        results.append(result)

    # Rank by:
    #
    # 1. Number of matched keywords
    # 2. Total keyword score
    #
    results.sort(
        key=lambda item: (
            item["keyword_match_count"],
            item["keyword_score"],
        ),
        reverse=True,
    )

    results = results[:match_count]

    print(
        f"Candidates: {len(results)}"
    )

    for rank, result in enumerate(
        results,
        start=1,
    ):

        print(
            f"  Rank {rank} | "
            f"Page {result.get('page_number')} | "
            f"Keyword matches="
            f"{result['keyword_match_count']} | "
            f"Score="
            f"{result['keyword_score']:.3f}"
        )

    return results