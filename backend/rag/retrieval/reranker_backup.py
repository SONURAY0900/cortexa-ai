import re


# ============================================================
# STOP WORDS
# ============================================================

# Only common linguistic stop words are removed.
#
# IMPORTANT:
# Do not add domain-specific words such as:
# "candidate", "document", "pdf", "project", "technology",
# "resume", etc.
#
# The reranker must work with ANY PDF.
# ============================================================

STOP_WORDS = {
    # Articles
    "a",
    "an",
    "the",

    # Conjunctions
    "and",
    "or",
    "but",
    "nor",
    "so",
    "yet",

    # Prepositions
    "about",
    "above",
    "across",
    "after",
    "against",
    "along",
    "among",
    "around",
    "at",
    "before",
    "behind",
    "below",
    "beneath",
    "beside",
    "between",
    "beyond",
    "by",
    "despite",
    "during",
    "for",
    "from",
    "in",
    "inside",
    "into",
    "near",
    "of",
    "off",
    "on",
    "onto",
    "over",
    "through",
    "to",
    "toward",
    "under",
    "until",
    "up",
    "upon",
    "with",
    "within",
    "without",

    # Pronouns
    "i",
    "me",
    "my",
    "mine",
    "myself",
    "we",
    "us",
    "our",
    "ours",
    "ourselves",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "they",
    "them",
    "their",
    "theirs",
    "themselves",
    "it",
    "its",
    "itself",

    # Auxiliary / modal verbs
    "am",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "do",
    "does",
    "did",
    "doing",
    "have",
    "has",
    "had",
    "having",
    "can",
    "could",
    "may",
    "might",
    "must",
    "shall",
    "should",
    "will",
    "would",

    # Common question words
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "when",
    "where",
    "why",
    "how",

    # Common conversational words
    "please",
    "tell",
    "give",
    "show",
    "provide",

    # Common logical/filler words
    "also",
    "just",
    "more",
    "most",
    "some",
    "any",
    "all",
    "each",
    "every",
    "other",
    "another",
    "such",
    "than",
    "then",
    "there",
    "here",
    "very",
    "only",
    "own",
}


# ============================================================
# TOKENIZATION
# ============================================================

def tokenize(text: str) -> list[str]:
    """
    Convert text into lowercase alphanumeric tokens.

    Examples:

        "FastAPI-based REST API"
        ->
        ["fastapi", "based", "rest", "api"]

        "LLaMA 3.1"
        ->
        ["llama", "3", "1"]
    """

    if not text:
        return []

    return re.findall(
        r"[a-zA-Z0-9]+",
        text.lower(),
    )


def meaningful_tokens(text: str) -> list[str]:
    """
    Remove only generic linguistic stop words
    and very short tokens.
    """

    tokens = tokenize(text)

    return [
        token
        for token in tokens
        if token not in STOP_WORDS
        and len(token) > 1
    ]


# ============================================================
# KEYWORD OVERLAP
# ============================================================

def calculate_keyword_overlap(
    query_tokens: list[str],
    content_tokens: list[str],
) -> float:
    """
    Calculate standard normalized lexical overlap.
    """

    if not query_tokens:
        return 0.0

    query_set = set(query_tokens)
    content_set = set(content_tokens)

    matches = query_set.intersection(
        content_set
    )

    return len(matches) / len(query_set)


# ============================================================
# PHRASE MATCH
# ============================================================

def calculate_phrase_match(
    query: str,
    content: str,
) -> float:
    """
    Detect meaningful adjacent two-word phrases
    from the original query.

    Example:

        Query:
            "machine learning model"

        Possible phrases:
            "machine learning"
            "learning model"
    """

    query_tokens = meaningful_tokens(query)

    if len(query_tokens) < 2:
        return 0.0

    content_tokens = meaningful_tokens(content)

    if len(content_tokens) < 2:
        return 0.0

    content_token_set = set(
        zip(
            content_tokens,
            content_tokens[1:],
        )
    )

    phrase_count = 0
    matched_phrases = 0

    for index in range(
        len(query_tokens) - 1
    ):
        phrase = (
            query_tokens[index],
            query_tokens[index + 1],
        )

        phrase_count += 1

        if phrase in content_token_set:
            matched_phrases += 1

    if phrase_count == 0:
        return 0.0

    return (
        matched_phrases
        / phrase_count
    )


# ============================================================
# EXACT QUERY MATCH
# ============================================================

def calculate_exact_query_match(
    query: str,
    content: str,
) -> float:
    """
    Detect whether the complete meaningful query
    appears in the content.

    This is intentionally a small binary signal.
    """

    query_tokens = meaningful_tokens(query)

    if not query_tokens:
        return 0.0

    normalized_query = " ".join(
        query_tokens
    )

    content_tokens = meaningful_tokens(
        content
    )

    normalized_content = " ".join(
        content_tokens
    )

    if normalized_query in normalized_content:
        return 1.0

    return 0.0


# ============================================================
# RRF NORMALIZATION
# ============================================================

def normalize_rrf_scores(
    results: list[dict],
) -> dict[str, float]:
    """
    Normalize RRF scores into [0, 1].

    Normalization is performed only inside
    the current candidate set.
    """

    if not results:
        return {}

    scores = [
        float(
            result.get(
                "rrf_score",
                0.0,
            )
        )
        for result in results
    ]

    minimum = min(scores)
    maximum = max(scores)

    if maximum == minimum:
        return {
            str(
                result.get("id")
            ): 1.0
            for result in results
        }

    normalized = {}

    for result in results:
        result_id = str(
            result.get("id")
        )

        rrf_score = float(
            result.get(
                "rrf_score",
                0.0,
            )
        )

        normalized[result_id] = (
            rrf_score - minimum
        ) / (
            maximum - minimum
        )

    return normalized


# ============================================================
# RERANK
# ============================================================

def rerank_results(
    query: str,
    results: list[dict],
    result_count: int = 5,
    expanded_queries: list[str] | None = None,
) -> list[dict]:
    """
    Generic document-agnostic reranking.

    Ranking signals:

        1. Normalized RRF
        2. Standard lexical overlap
        3. Phrase match
        4. Exact meaningful-query match

    Query expansion is used only for candidate discovery.

    The original query is used for reranking.

    No document-specific assumptions are made.
    """

    if not results:
        return []

    if expanded_queries is None:
        expanded_queries = [query]

    print()
    print("[RERANKING]")

    print(
        f"Query: {query}"
    )

    print(
        f"Expanded queries: "
        f"{len(expanded_queries)} "
        f"(candidate discovery only)"
    )

    print(
        f"Input candidates: "
        f"{len(results)}"
    )

    # --------------------------------------------------------
    # NORMALIZE RRF
    # --------------------------------------------------------

    normalized_rrf = normalize_rrf_scores(
        results
    )

    # --------------------------------------------------------
    # QUERY TOKENS
    # --------------------------------------------------------

    query_tokens = meaningful_tokens(
        query
    )

    # --------------------------------------------------------
    # PROCESS CANDIDATES
    # --------------------------------------------------------

    reranked = []

    for result in results:

        content = result.get(
            "content",
            "",
        )

        result_id = str(
            result.get("id")
        )

        raw_rrf_score = float(
            result.get(
                "rrf_score",
                0.0,
            )
        )

        normalized_rrf_score = (
            normalized_rrf.get(
                result_id,
                0.0,
            )
        )

        content_tokens = meaningful_tokens(
            content
        )

        # ----------------------------------------------------
        # STANDARD LEXICAL OVERLAP
        # ----------------------------------------------------

        original_overlap = (
            calculate_keyword_overlap(
                query_tokens,
                content_tokens,
            )
        )

        # ----------------------------------------------------
        # PHRASE MATCH
        # ----------------------------------------------------

        phrase_match = (
            calculate_phrase_match(
                query,
                content,
            )
        )

        # ----------------------------------------------------
        # EXACT QUERY MATCH
        # ----------------------------------------------------

        exact_query_match = (
            calculate_exact_query_match(
                query,
                content,
            )
        )

        # ----------------------------------------------------
        # FINAL SCORE
        # ----------------------------------------------------

        # RRF remains the strongest signal because it combines
        # dense and keyword retrieval.
        #
        # Standard lexical overlap provides a stable
        # lexical relevance signal.
        #
        # Phrase matching rewards local semantic structure.
        #
        # Exact match remains a small high-confidence signal.

        rerank_score = (
            0.60 * normalized_rrf_score
            + 0.25 * original_overlap
            + 0.10 * phrase_match
            + 0.05 * exact_query_match
        )

        enriched = dict(result)

        enriched[
            "raw_rrf_score"
        ] = raw_rrf_score

        enriched[
            "normalized_rrf_score"
        ] = normalized_rrf_score

        enriched[
            "original_keyword_overlap"
        ] = original_overlap

        # Keep this field for compatibility with
        # existing retrieval/debug code.
        enriched[
            "weighted_keyword_overlap"
        ] = 0.0

        enriched[
            "expanded_keyword_overlap"
        ] = 0.0

        enriched[
            "keyword_overlap"
        ] = original_overlap

        enriched[
            "phrase_match"
        ] = phrase_match

        # Keep this field for compatibility with
        # existing retrieval/debug code.
        enriched[
            "partial_term_match"
        ] = 0.0

        enriched[
            "exact_query_match"
        ] = exact_query_match

        enriched[
            "rerank_score"
        ] = rerank_score

        reranked.append(
            enriched
        )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    reranked.sort(
        key=lambda item: (
            float(
                item.get(
                    "rerank_score",
                    0.0,
                )
            ),
            float(
                item.get(
                    "raw_rrf_score",
                    0.0,
                )
            ),
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # TOP RESULTS
    # --------------------------------------------------------

    reranked = reranked[
        :result_count
    ]

    # --------------------------------------------------------
    # DEBUG OUTPUT
    # --------------------------------------------------------

    for rank, result in enumerate(
        reranked,
        start=1,
    ):
        print(
            f"  Rank {rank} | "
            f"Chunk "
            f"{result.get('chunk_index')} | "
            f"RRF="
            f"{result.get('raw_rrf_score', 0.0):.6f} | "
            f"NormRRF="
            f"{result.get('normalized_rrf_score', 0.0):.3f} | "
            f"Weighted="
            f"{result.get('weighted_keyword_overlap', 0.0):.3f} | "
            f"Overlap="
            f"{result.get('original_keyword_overlap', 0.0):.3f} | "
            f"Phrase="
            f"{result.get('phrase_match', 0.0):.3f} | "
            f"Partial="
            f"{result.get('partial_term_match', 0.0):.3f} | "
            f"Exact="
            f"{result.get('exact_query_match', 0.0):.3f} | "
            f"Final="
            f"{result.get('rerank_score', 0.0):.6f}"
        )

    print(
        f"Final results: "
        f"{len(reranked)}"
    )

    return reranked