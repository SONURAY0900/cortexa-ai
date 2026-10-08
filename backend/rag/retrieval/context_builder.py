import re

from database.connection import supabase


DEFAULT_NEIGHBOR_WINDOW = 1
DEFAULT_MAX_CONTEXT_CHUNKS = 8
DEFAULT_MAX_CONTEXT_CHARS = 6000

# Weight used when selecting neighbor chunks.
NEIGHBOR_DISTANCE_WEIGHT = 0.20
NEIGHBOR_QUERY_WEIGHT = 0.80


# ============================================================
# QUERY TOKENIZATION
# ============================================================

def _tokenize_query(text: str) -> set[str]:
    """
    Convert query text into normalized tokens.

    Used for lightweight query-aware context selection.
    """

    if not text:
        return set()

    tokens = re.findall(
        r"[A-Za-z0-9_+#.-]+",
        text.lower(),
    )

    # Remove extremely short tokens because they create
    # many false matches.
    return {
        token
        for token in tokens
        if len(token) >= 2
    }


def _calculate_query_overlap(
    query: str,
    content: str,
) -> float:
    """
    Calculate a lightweight lexical overlap score.

    Returns a value between 0.0 and 1.0.

    This is intentionally simple. The main semantic
    ranking is still handled by the retrieval/reranking
    pipeline.
    """

    query_tokens = _tokenize_query(query)

    if not query_tokens or not content:
        return 0.0

    content_tokens = _tokenize_query(content)

    if not content_tokens:
        return 0.0

    matched_tokens = (
        query_tokens
        & content_tokens
    )

    return (
        len(matched_tokens)
        / len(query_tokens)
    )


# ============================================================
# NEIGHBOR RETRIEVAL
# ============================================================

def _get_neighbor_chunks(
    document_id: str,
    chunk_index: int,
    window: int = DEFAULT_NEIGHBOR_WINDOW,
) -> list[dict]:
    """
    Retrieve neighboring chunks from the same document.

    Example:

        chunk_index = 10
        window = 1

    Retrieves:

        9, 10, 11
    """

    start_index = max(
        0,
        chunk_index - window,
    )

    end_index = (
        chunk_index + window
    )

    response = (
        supabase
        .table("document_chunks")
        .select(
            "id, document_id, chunk_index, content, page_number"
        )
        .eq(
            "document_id",
            document_id,
        )
        .gte(
            "chunk_index",
            start_index,
        )
        .lte(
            "chunk_index",
            end_index,
        )
        .order(
            "chunk_index"
        )
        .execute()
    )

    return response.data or []


# ============================================================
# NEIGHBOR SCORING
# ============================================================

def _score_neighbor(
    query: str,
    neighbor: dict,
    source_chunk_index: int,
) -> float:
    """
    Score a neighbor chunk for context selection.

    Factors:

    1. Query overlap
    2. Distance from retrieved chunk

    The neighbor itself does not replace the original
    retrieval score.
    """

    neighbor_index = neighbor.get(
        "chunk_index"
    )

    if neighbor_index is None:
        return 0.0

    distance = abs(
        int(neighbor_index)
        - int(source_chunk_index)
    )

    # Distance score:
    #
    # distance 0 -> 1.0
    # distance 1 -> 0.5
    # distance 2 -> 0.33
    #
    distance_score = 1.0 / (
        1.0 + distance
    )

    query_score = _calculate_query_overlap(
        query=query,
        content=neighbor.get(
            "content",
            "",
        ),
    )

    return (
        NEIGHBOR_QUERY_WEIGHT
        * query_score
        +
        NEIGHBOR_DISTANCE_WEIGHT
        * distance_score
    )


# ============================================================
# REDUNDANCY
# ============================================================

def _content_signature(
    content: str,
) -> set[str]:
    """
    Generate a lightweight token signature
    for redundancy detection.
    """

    return _tokenize_query(content)


def _is_redundant(
    candidate: dict,
    selected: list[dict],
    threshold: float = 0.90,
) -> bool:
    """
    Determine whether a candidate chunk is almost
    completely redundant with an already selected chunk.
    """

    candidate_tokens = _content_signature(
        candidate.get(
            "content",
            "",
        )
    )

    if not candidate_tokens:
        return False

    for existing in selected:

        existing_tokens = _content_signature(
            existing.get(
                "content",
                "",
            )
        )

        if not existing_tokens:
            continue

        intersection = (
            candidate_tokens
            & existing_tokens
        )

        union = (
            candidate_tokens
            | existing_tokens
        )

        if not union:
            continue

        similarity = (
            len(intersection)
            / len(union)
        )

        if similarity >= threshold:
            return True

    return False


# ============================================================
# EXPAND + SELECT CONTEXT
# ============================================================

def expand_neighbors(
    results: list[dict],
    query: str = "",
    window: int = DEFAULT_NEIGHBOR_WINDOW,
    max_context_chunks: int = DEFAULT_MAX_CONTEXT_CHUNKS,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> list[dict]:
    """
    Expand retrieved chunks with neighboring chunks.

    Phase 8 improvements:

    - Retrieved chunks are always prioritized.
    - Neighbor chunks are query-aware.
    - Neighbor chunks are distance-aware.
    - Redundant chunks are removed.
    - Context has both chunk and character limits.
    - Final output is restored to document order.
    """

    if not results:
        return []

    # --------------------------------------------------
    # 1. Normalize original retrieved chunks
    # --------------------------------------------------

    retrieved_chunks = []

    seen_ids = set()

    for rank, result in enumerate(results):

        chunk_id = result.get("id")

        if not chunk_id:
            continue

        if chunk_id in seen_ids:
            continue

        seen_ids.add(chunk_id)

        chunk = {
            **result,
            "is_retrieved": True,
            "is_neighbor": False,
            "retrieval_rank": rank + 1,
            "context_score": float(
                result.get(
                    "rerank_score",
                    result.get(
                        "rrf_score",
                        0.0,
                    ),
                )
            ),
        }

        retrieved_chunks.append(
            chunk
        )

    if not retrieved_chunks:
        return []

    # --------------------------------------------------
    # 2. Collect neighboring candidates
    # --------------------------------------------------

    neighbor_candidates = []

    for result in retrieved_chunks:

        document_id = result.get(
            "document_id"
        )

        chunk_index = result.get(
            "chunk_index"
        )

        if not document_id:
            continue

        if chunk_index is None:
            continue

        neighbors = _get_neighbor_chunks(
            document_id=document_id,
            chunk_index=int(chunk_index),
            window=window,
        )

        for neighbor in neighbors:

            neighbor_id = neighbor.get(
                "id"
            )

            if not neighbor_id:
                continue

            # Already a retrieved chunk.
            if neighbor_id in seen_ids:
                continue

            score = _score_neighbor(
                query=query,
                neighbor=neighbor,
                source_chunk_index=int(
                    chunk_index
                ),
            )

            neighbor_candidates.append(
                {
                    **neighbor,
                    "is_retrieved": False,
                    "is_neighbor": True,
                    "neighbor_of_chunk": int(
                        chunk_index
                    ),
                    "neighbor_score": score,
                    "query_overlap": _calculate_query_overlap(
                        query=query,
                        content=neighbor.get(
                            "content",
                            "",
                        ),
                    ),
                }
            )

    # --------------------------------------------------
    # 3. Deduplicate neighbor candidates
    #
    # A neighbor may be adjacent to more than one
    # retrieved chunk.
    # --------------------------------------------------

    unique_neighbors = {}

    for candidate in neighbor_candidates:

        candidate_id = candidate.get(
            "id"
        )

        if not candidate_id:
            continue

        existing = unique_neighbors.get(
            candidate_id
        )

        if existing is None:

            unique_neighbors[
                candidate_id
            ] = candidate

        else:

            # Keep strongest neighbor score.
            if (
                candidate.get(
                    "neighbor_score",
                    0.0,
                )
                >
                existing.get(
                    "neighbor_score",
                    0.0,
                )
            ):

                unique_neighbors[
                    candidate_id
                ] = candidate

    neighbor_candidates = list(
        unique_neighbors.values()
    )

    # --------------------------------------------------
    # 4. Select retrieved chunks first
    # --------------------------------------------------

    selected = []

    current_chars = 0

    for result in retrieved_chunks:

        content = result.get(
            "content",
            "",
        ).strip()

        if not content:
            continue

        content_length = len(content)

        # Always preserve high-ranked retrieved chunks
        # unless the configured chunk count is reached.
        if len(selected) >= max_context_chunks:
            break

        if (
            current_chars > 0
            and current_chars + content_length
            > max_context_chars
        ):
            break

        selected.append(
            result
        )

        current_chars += content_length

    # --------------------------------------------------
    # 5. Rank neighbor candidates
    # --------------------------------------------------

    neighbor_candidates.sort(
        key=lambda item: (
            float(
                item.get(
                    "neighbor_score",
                    0.0,
                )
            ),
            float(
                item.get(
                    "query_overlap",
                    0.0,
                )
            ),
        ),
        reverse=True,
    )

    # --------------------------------------------------
    # 6. Add useful neighbors
    # --------------------------------------------------

    for neighbor in neighbor_candidates:

        if len(selected) >= max_context_chunks:
            break

        content = neighbor.get(
            "content",
            "",
        ).strip()

        if not content:
            continue

        # Avoid highly redundant context.
        if _is_redundant(
            candidate=neighbor,
            selected=selected,
        ):
            continue

        content_length = len(content)

        if (
            current_chars > 0
            and current_chars + content_length
            > max_context_chars
        ):
            continue

        selected.append(
            neighbor
        )

        current_chars += content_length

    # --------------------------------------------------
    # 7. Restore document order
    #
    # Retrieval ranking is useful for selection.
    # Document order is better for LLM context.
    # --------------------------------------------------

    selected.sort(
        key=lambda item: (
            str(
                item.get(
                    "document_id",
                    "",
                )
            ),
            int(
                item.get(
                    "chunk_index",
                    0,
                )
            ),
        )
    )

    # --------------------------------------------------
    # 8. Print diagnostics
    # --------------------------------------------------

    print()
    print(
        "[CONTEXT EXPANSION]"
    )

    print(
        f"Original chunks: "
        f"{len(results)}"
    )

    print(
        f"Retrieved selected: "
        f"{sum(1 for item in selected if item.get('is_retrieved'))}"
    )

    print(
        f"Neighbors selected: "
        f"{sum(1 for item in selected if item.get('is_neighbor'))}"
    )

    print(
        f"Final context chunks: "
        f"{len(selected)}"
    )

    print(
        f"Final context characters: "
        f"{current_chars}"
    )

    for result in selected:

        chunk_type = (
            "RETRIEVED"
            if result.get(
                "is_retrieved"
            )
            else "NEIGHBOR"
        )

        print(
            f"  {chunk_type} | "
            f"Page {result.get('page_number')} | "
            f"Chunk {result.get('chunk_index')} | "
            f"Query overlap: "
            f"{result.get('query_overlap', 0.0):.3f}"
        )

    return selected


# ============================================================
# CONTEXT TEXT BUILDER
# ============================================================

def build_context(
    results: list[dict],
) -> str:
    """
    Build the final textual context passed to the LLM.

    Chunks are already ordered by document/chunk position.
    """

    if not results:
        return ""

    context_parts = []

    for index, result in enumerate(
        results,
        start=1,
    ):

        page_number = result.get(
            "page_number"
        )

        chunk_index = result.get(
            "chunk_index"
        )

        content = result.get(
            "content",
            "",
        ).strip()

        if not content:
            continue

        source_label = (
            f"Source {index} | "
            f"Page {page_number} | "
            f"Chunk {chunk_index}"
        )

        context_parts.append(
            f"[{source_label}]\n"
            f"{content}"
        )

    return "\n\n".join(
        context_parts
    )


# ============================================================
# COMPLETE RAG CONTEXT
# ============================================================

def build_rag_context(
    results: list[dict],
    query: str = "",
    neighbor_window: int = DEFAULT_NEIGHBOR_WINDOW,
    max_context_chunks: int = DEFAULT_MAX_CONTEXT_CHUNKS,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> tuple[list[dict], str]:
    """
    Expand retrieved results and build final LLM context.

    Returns:

        (
            context_chunks,
            context_text
        )
    """

    context_chunks = expand_neighbors(
        results=results,
        query=query,
        window=neighbor_window,
        max_context_chunks=max_context_chunks,
        max_context_chars=max_context_chars,
    )

    context_text = build_context(
        results=context_chunks,
    )

    return (
        context_chunks,
        context_text,
    )