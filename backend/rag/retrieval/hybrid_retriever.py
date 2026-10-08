from rag.retrieval.dense_retriever import (
    retrieve_dense,
)

from rag.retrieval.keyword_retriever import (
    retrieve_keyword,
)


DEFAULT_RRF_K = 60


def retrieve_hybrid(
    query: str,
    document_id: str | None = None,
    candidate_count: int = 20,
    result_count: int = 20,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[dict]:
    """
    Hybrid retrieval using:

    Dense Vector Retrieval
    +
    Keyword Retrieval
    +
    Reciprocal Rank Fusion (RRF)

    Handles:
    - dense-only results
    - keyword-only results
    - results found by both retrievers
    """

    # =========================================================
    # DENSE RETRIEVAL
    # =========================================================

    dense_results = retrieve_dense(
        query=query,
        document_id=document_id,
        match_count=candidate_count,
    )

    # Add dense rank explicitly.
    for rank, result in enumerate(
        dense_results,
        start=1,
    ):
        result["dense_rank"] = rank

        # A dense result may not have keyword rank.
        result.setdefault(
            "keyword_rank",
            None,
        )

    # =========================================================
    # KEYWORD RETRIEVAL
    # =========================================================

    keyword_results = retrieve_keyword(
        query=query,
        document_id=document_id,
        match_count=candidate_count,
    )

    # Add keyword rank explicitly.
    for rank, result in enumerate(
        keyword_results,
        start=1,
    ):
        result["keyword_rank"] = rank

        # A keyword result may not have dense rank.
        result.setdefault(
            "dense_rank",
            None,
        )

    # =========================================================
    # MERGE RESULTS
    # =========================================================

    combined = {}

    # ---------------------------------------------------------
    # Add dense results
    # ---------------------------------------------------------

    for result in dense_results:

        chunk_id = result.get("id")

        if not chunk_id:
            continue

        combined[chunk_id] = dict(
            result
        )

    # ---------------------------------------------------------
    # Add keyword results
    # ---------------------------------------------------------

    for result in keyword_results:

        chunk_id = result.get("id")

        if not chunk_id:
            continue

        if chunk_id not in combined:

            combined[chunk_id] = dict(
                result
            )

        else:

            existing = combined[chunk_id]

            # Preserve dense information.
            if existing.get(
                "dense_rank"
            ) is None:

                existing["dense_rank"] = (
                    result.get(
                        "dense_rank"
                    )
                )

            if existing.get(
                "similarity"
            ) is None:

                existing["similarity"] = (
                    result.get(
                        "similarity",
                        0.0,
                    )
                )

            # Preserve keyword information.
            existing["keyword_rank"] = (
                result.get(
                    "keyword_rank"
                )
            )

            existing["keyword_score"] = (
                result.get(
                    "keyword_score",
                    0.0,
                )
            )

            existing[
                "keyword_match_count"
            ] = result.get(
                "keyword_match_count",
                0,
            )

    # =========================================================
    # RECIPROCAL RANK FUSION
    # =========================================================

    for result in combined.values():

        dense_rank = result.get(
            "dense_rank"
        )

        keyword_rank = result.get(
            "keyword_rank"
        )

        dense_rrf = 0.0
        keyword_rrf = 0.0

        # Dense contribution.
        if dense_rank is not None:

            dense_rrf = (
                1.0
                / (
                    rrf_k
                    + dense_rank
                )
            )

        # Keyword contribution.
        if keyword_rank is not None:

            keyword_rrf = (
                1.0
                / (
                    rrf_k
                    + keyword_rank
                )
            )

        result["dense_rrf"] = dense_rrf
        result["keyword_rrf"] = keyword_rrf

        result["rrf_score"] = (
            dense_rrf
            + keyword_rrf
        )

    # =========================================================
    # SORT BY RRF
    # =========================================================

    results = list(
        combined.values()
    )

    results.sort(
        key=lambda item: float(
            item.get(
                "rrf_score",
                0.0,
            )
        ),
        reverse=True,
    )

    results = results[
        :result_count
    ]

    # =========================================================
    # LOGGING
    # =========================================================

    print()
    print(
        "[HYBRID RETRIEVAL]"
    )

    print(
        f"Query: {query}"
    )

    print(
        f"Dense candidates: "
        f"{len(dense_results)}"
    )

    print(
        f"Keyword candidates: "
        f"{len(keyword_results)}"
    )

    print(
        f"Combined candidates: "
        f"{len(combined)}"
    )

    print(
        f"Final results: "
        f"{len(results)}"
    )

    for rank, result in enumerate(
        results,
        start=1,
    ):

        print(
            f"  Rank {rank} | "
            f"Page {result.get('page_number')} | "
            f"RRF={result.get('rrf_score', 0.0):.6f} | "
            f"Dense rank={result.get('dense_rank')} | "
            f"Keyword rank={result.get('keyword_rank')}"
        )

    return results