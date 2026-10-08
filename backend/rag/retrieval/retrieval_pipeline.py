from rag.query.query_transformer import (
    transform_query,
)

from rag.retrieval.hybrid_retriever import (
    retrieve_hybrid,
)

from rag.retrieval.reranker import (
    rerank_results,
)

from rag.retrieval.diversity_selector import (
    select_diverse_results,
    print_diversity_results,
)

from rag.retrieval.context_builder import (
    build_rag_context,
)


# ============================================================
# DEFAULT SETTINGS
# ============================================================

DEFAULT_CANDIDATE_COUNT = 20
DEFAULT_HYBRID_COUNT = 20
DEFAULT_RERANK_COUNT = 5

DEFAULT_LIST_RERANK_COUNT = 8
DEFAULT_BROAD_DOCUMENT_RERANK_COUNT = 10

DEFAULT_NEIGHBOR_WINDOW = 1
DEFAULT_MAX_CONTEXT_CHUNKS = 8
DEFAULT_BROAD_DOCUMENT_CONTEXT_CHUNKS = 10
DEFAULT_MAX_CONTEXT_CHARS = 6000


# ============================================================
# QUERY-AWARE RERANK COUNT
# ============================================================

def _get_rerank_count(
    query_type: str,
    requested_count: int | None,
) -> int:
    """
    Determine how many results should be selected
    after reranking.

    Explicit caller values always override the
    automatic query-aware policy.

    Query types:

        specific
            -> 5

        list
            -> 8

        broad_document
            -> 10
    """

    if requested_count is not None:
        return requested_count

    if query_type == "broad_document":
        return DEFAULT_BROAD_DOCUMENT_RERANK_COUNT

    if query_type == "list":
        return DEFAULT_LIST_RERANK_COUNT

    return DEFAULT_RERANK_COUNT


# ============================================================
# QUERY-AWARE CONTEXT COUNT
# ============================================================

def _get_max_context_chunks(
    query_type: str,
    requested_count: int | None,
) -> int:
    """
    Determine maximum context chunks.

    Explicit caller values always override the
    automatic query-aware policy.
    """

    if requested_count is not None:
        return requested_count

    if query_type == "broad_document":
        return DEFAULT_BROAD_DOCUMENT_CONTEXT_CHUNKS

    if query_type == "list":
        return DEFAULT_MAX_CONTEXT_CHUNKS

    return DEFAULT_MAX_CONTEXT_CHUNKS


# ============================================================
# MERGE MULTI-QUERY RESULTS
# ============================================================

def _merge_retrieval_results(
    result_sets: list[list[dict]],
) -> list[dict]:
    """
    Merge results returned by multiple retrieval queries.

    Duplicate chunks are removed.

    If the same chunk appears through multiple retrieval
    queries, its strongest retrieval signals are preserved.
    """

    merged = {}

    for results in result_sets:

        for result in results:

            chunk_id = result.get(
                "id"
            )

            if not chunk_id:
                continue

            if chunk_id not in merged:

                merged[chunk_id] = dict(
                    result
                )

                merged[chunk_id][
                    "retrieval_query_count"
                ] = 1

            else:

                existing = merged[
                    chunk_id
                ]

                existing[
                    "retrieval_query_count"
                ] += 1

                # --------------------------------------------------
                # Strongest RRF score
                # --------------------------------------------------

                existing_rrf = float(
                    existing.get(
                        "rrf_score",
                        0.0,
                    )
                )

                current_rrf = float(
                    result.get(
                        "rrf_score",
                        0.0,
                    )
                )

                existing[
                    "rrf_score"
                ] = max(
                    existing_rrf,
                    current_rrf,
                )

                # --------------------------------------------------
                # Strongest dense similarity
                # --------------------------------------------------

                existing_similarity = float(
                    existing.get(
                        "similarity",
                        0.0,
                    )
                )

                current_similarity = float(
                    result.get(
                        "similarity",
                        0.0,
                    )
                )

                existing[
                    "similarity"
                ] = max(
                    existing_similarity,
                    current_similarity,
                )

                # --------------------------------------------------
                # Strongest keyword score
                # --------------------------------------------------

                existing_keyword = float(
                    existing.get(
                        "keyword_score",
                        0.0,
                    )
                )

                current_keyword = float(
                    result.get(
                        "keyword_score",
                        0.0,
                    )
                )

                existing[
                    "keyword_score"
                ] = max(
                    existing_keyword,
                    current_keyword,
                )

    merged_results = list(
        merged.values()
    )

    # --------------------------------------------------
    # Multi-query consistency bonus
    # --------------------------------------------------

    for result in merged_results:

        result[
            "multi_query_bonus"
        ] = (
            0.01
            * result.get(
                "retrieval_query_count",
                1,
            )
        )

        result[
            "rrf_score"
        ] = (
            float(
                result.get(
                    "rrf_score",
                    0.0,
                )
            )
            + result[
                "multi_query_bonus"
            ]
        )

    merged_results.sort(
        key=lambda item: float(
            item.get(
                "rrf_score",
                0.0,
            )
        ),
        reverse=True,
    )

    return merged_results


# ============================================================
# COMPLETE RETRIEVAL PIPELINE
# ============================================================

def retrieve_context(
    query: str,
    document_id: str | None = None,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    hybrid_count: int = DEFAULT_HYBRID_COUNT,
    rerank_count: int | None = None,
    neighbor_window: int = DEFAULT_NEIGHBOR_WINDOW,
    max_context_chunks: int | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict:
    """
    Complete RAG retrieval pipeline.

    Supports:

    - Query transformation
    - Multi-query retrieval
    - Dense retrieval
    - Keyword retrieval
    - Hybrid retrieval
    - Result merging
    - Reranking
    - Diversity-aware selection
    - Query-aware context building
    - Structure-aware neighbor expansion
    - Context size control

    Query-aware behavior:

        specific
            -> normal reranking

        list
            -> larger reranking pool
            -> diversity-aware selection

        broad_document
            -> larger reranking pool
            -> diversity-aware selection
    """

    # ==================================================
    # QUERY TRANSFORMATION
    # ==================================================

    query_info = transform_query(
        query
    )

    original_query = query_info[
        "original_query"
    ]

    retrieval_queries = query_info[
        "retrieval_queries"
    ]

    query_type = query_info[
        "query_type"
    ]

    # --------------------------------------------------
    # Determine automatic query-aware limits
    # --------------------------------------------------

    effective_rerank_count = (
        _get_rerank_count(
            query_type=query_type,
            requested_count=rerank_count,
        )
    )

    effective_max_context_chunks = (
        _get_max_context_chunks(
            query_type=query_type,
            requested_count=max_context_chunks,
        )
    )

    print()
    print(
        "[QUERY TRANSFORMATION]"
    )

    print(
        f"Original query: "
        f"{original_query}"
    )

    print(
        f"Query type: "
        f"{query_type}"
    )

    print(
        f"Effective rerank count: "
        f"{effective_rerank_count}"
    )

    print(
        f"Effective max context chunks: "
        f"{effective_max_context_chunks}"
    )

    print(
        "Retrieval queries:"
    )

    for index, retrieval_query in enumerate(
        retrieval_queries,
        start=1,
    ):

        print(
            f"  {index}. "
            f"{retrieval_query}"
        )

    # ==================================================
    # MULTI-QUERY HYBRID RETRIEVAL
    # ==================================================

    result_sets = []

    for retrieval_query in retrieval_queries:

        print()
        print(
            "[MULTI-QUERY RETRIEVAL]"
        )

        print(
            f"Query: "
            f"{retrieval_query}"
        )

        results = retrieve_hybrid(
            query=retrieval_query,
            document_id=document_id,
            candidate_count=candidate_count,
            result_count=hybrid_count,
        )

        result_sets.append(
            results
        )

    # ==================================================
    # MERGE RESULTS
    # ==================================================

    merged_results = (
        _merge_retrieval_results(
            result_sets
        )
    )

    print()
    print(
        "[MERGED RETRIEVAL]"
    )

    print(
        f"Query variants: "
        f"{len(retrieval_queries)}"
    )

    print(
        f"Unique candidates: "
        f"{len(merged_results)}"
    )

    # ==================================================
    # LIMIT CANDIDATES BEFORE RERANKING
    # ==================================================

    rerank_candidates = (
        merged_results[
            :candidate_count
        ]
    )

    # ==================================================
    # RERANK
    # ==================================================

    # Important:
    #
    # Retrieval uses transformed queries.
    #
    # Reranking uses the original user query.
    #
    # This prevents generic query expansion from
    # dominating the final relevance decision.

    # --------------------------------------------------
    # IMPORTANT DIVERSITY DESIGN
    # --------------------------------------------------
    #
    # Specific queries:
    #
    #     rerank only the normal top N.
    #
    # List and broad-document queries:
    #
    #     rerank the complete candidate pool first.
    #
    #     Diversity selection then chooses the final
    #     number of context candidates.
    #
    # This allows diversity selection to see more
    # candidates instead of only seeing the already
    # truncated top 8/10.
    # --------------------------------------------------

    if query_type in {
        "list",
        "broad_document",
    }:

        rerank_pool_count = min(
            candidate_count,
            len(rerank_candidates),
        )

    else:

        rerank_pool_count = min(
            effective_rerank_count,
            len(rerank_candidates),
        )

    reranked_results = rerank_results(
        query=original_query,
        results=rerank_candidates,
        result_count=rerank_pool_count,
        expanded_queries=retrieval_queries,
    )

    # ==================================================
    # DIVERSITY-AWARE SELECTION
    # ==================================================

    if query_type in {
        "list",
        "broad_document",
    }:

        print()
        print(
            "[DIVERSITY-AWARE RETRIEVAL]"
        )

        print(
            f"Candidate pool: "
            f"{len(reranked_results)}"
        )

        print(
            f"Target results: "
            f"{effective_rerank_count}"
        )

        reranked_results = (
            select_diverse_results(
                results=reranked_results,
                result_count=effective_rerank_count,
                redundancy_weight=0.30,
            )
        )

        print_diversity_results(
            reranked_results
        )

    # ==================================================
    # QUERY-AWARE CONTEXT BUILDING
    # ==================================================

    context_chunks, context = (
        build_rag_context(
            results=reranked_results,
            query=original_query,
            neighbor_window=neighbor_window,
            max_context_chunks=effective_max_context_chunks,
            max_context_chars=max_context_chars,
        )
    )

    # ==================================================
    # DIAGNOSTICS
    # ==================================================

    retrieved_count = sum(
        1
        for result in context_chunks
        if result.get(
            "is_retrieved"
        )
    )

    neighbor_count = sum(
        1
        for result in context_chunks
        if result.get(
            "is_neighbor"
        )
    )

    print()
    print(
        "[RETRIEVAL PIPELINE]"
    )

    print(
        f"Original query: "
        f"{original_query}"
    )

    print(
        f"Query type: "
        f"{query_type}"
    )

    print(
        f"Query variants: "
        f"{len(retrieval_queries)}"
    )

    print(
        f"Effective rerank count: "
        f"{effective_rerank_count}"
    )

    print(
        f"Effective max context chunks: "
        f"{effective_max_context_chunks}"
    )

    print(
        f"Rerank pool size: "
        f"{len(reranked_results)}"
    )

    print(
        f"Retrieved context chunks: "
        f"{retrieved_count}"
    )

    print(
        f"Neighbor context chunks: "
        f"{neighbor_count}"
    )

    print(
        f"Total context chunks: "
        f"{len(context_chunks)}"
    )

    print(
        f"Context characters: "
        f"{len(context)}"
    )

    return {
        "original_query": original_query,
        "retrieval_queries": retrieval_queries,
        "query_type": query_type,
        "results": reranked_results,
        "context_chunks": context_chunks,
        "context": context,
    }