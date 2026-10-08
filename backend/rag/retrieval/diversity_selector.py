import re
from typing import Iterable


# ============================================================
# TOKENIZATION
# ============================================================

def _tokenize(text: str) -> set[str]:
    """
    Convert text into a normalized set of tokens.
    """

    if not text:
        return set()

    return set(
        re.findall(
            r"[a-zA-Z0-9]+",
            text.lower(),
        )
    )


# ============================================================
# JACCARD SIMILARITY
# ============================================================

def _jaccard_similarity(
    first: set[str],
    second: set[str],
) -> float:
    """
    Calculate token-level Jaccard similarity.

        intersection
        ------------
         union

    Returns a value between 0 and 1.
    """

    if not first or not second:
        return 0.0

    intersection = len(
        first.intersection(second)
    )

    union = len(
        first.union(second)
    )

    if union == 0:
        return 0.0

    return intersection / union


# ============================================================
# DOCUMENT DISTANCE
# ============================================================

def _chunk_distance(
    first: dict,
    second: dict,
) -> int | None:
    """
    Return the distance between two chunks belonging
    to the same document.

    Returns None when chunk indexes are unavailable.
    """

    first_index = first.get(
        "chunk_index"
    )

    second_index = second.get(
        "chunk_index"
    )

    if (
        first_index is None
        or second_index is None
    ):
        return None

    try:
        return abs(
            int(first_index)
            - int(second_index)
        )
    except (
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# REDUNDANCY SCORE
# ============================================================

def _redundancy_score(
    candidate: dict,
    selected: Iterable[dict],
) -> float:
    """
    Estimate how redundant a candidate is compared
    with already selected chunks.

    Combines:

    1. Text similarity
    2. Chunk proximity

    Returns a value between 0 and 1.
    """

    candidate_tokens = _tokenize(
        candidate.get(
            "content",
            "",
        )
    )

    maximum_redundancy = 0.0

    for selected_chunk in selected:

        selected_tokens = _tokenize(
            selected_chunk.get(
                "content",
                "",
            )
        )

        text_similarity = (
            _jaccard_similarity(
                candidate_tokens,
                selected_tokens,
            )
        )

        distance = _chunk_distance(
            candidate,
            selected_chunk,
        )

        # Adjacent chunks naturally contain
        # some overlapping information because
        # of chunk overlap.
        #
        # We therefore treat proximity as a
        # weaker redundancy signal.

        if distance is None:
            proximity = 0.0

        elif distance == 0:
            proximity = 1.0

        elif distance == 1:
            proximity = 0.35

        elif distance == 2:
            proximity = 0.15

        else:
            proximity = 0.0

        redundancy = max(
            text_similarity,
            proximity,
        )

        maximum_redundancy = max(
            maximum_redundancy,
            redundancy,
        )

    return maximum_redundancy


# ============================================================
# DIVERSITY-AWARE SELECTION
# ============================================================

def select_diverse_results(
    results: list[dict],
    result_count: int = 5,
    redundancy_weight: float = 0.30,
) -> list[dict]:
    """
    Select high-quality retrieval results while
    reducing redundant chunks.

    The function assumes that `results` are already
    ranked by the upstream reranker.

    Selection score:

        relevance
        -
        redundancy penalty

    The original reranker score remains the primary
    relevance signal.

    This function is completely document-agnostic.
    """

    if not results:
        return []

    if result_count <= 0:
        return []

    if len(results) <= result_count:
        return list(results)

    selected: list[dict] = []

    remaining = list(results)

    # --------------------------------------------------------
    # ALWAYS KEEP THE BEST RANKED RESULT
    # --------------------------------------------------------

    first = remaining.pop(0)

    first_copy = dict(first)

    first_copy[
        "diversity_score"
    ] = float(
        first.get(
            "rerank_score",
            0.0,
        )
    )

    first_copy[
        "redundancy_penalty"
    ] = 0.0

    selected.append(
        first_copy
    )

    # --------------------------------------------------------
    # SELECT REMAINING RESULTS
    # --------------------------------------------------------

    while (
        remaining
        and len(selected) < result_count
    ):

        best_candidate = None
        best_score = float("-inf")
        best_penalty = 0.0

        for candidate in remaining:

            relevance = float(
                candidate.get(
                    "rerank_score",
                    0.0,
                )
            )

            redundancy = (
                _redundancy_score(
                    candidate,
                    selected,
                )
            )

            diversity_score = (
                relevance
                - (
                    redundancy_weight
                    * relevance
                    * redundancy
                )
            )

            if diversity_score > best_score:

                best_score = diversity_score
                best_candidate = candidate
                best_penalty = redundancy

        if best_candidate is None:
            break

        enriched = dict(
            best_candidate
        )

        enriched[
            "diversity_score"
        ] = best_score

        enriched[
            "redundancy_penalty"
        ] = best_penalty

        selected.append(
            enriched
        )

        remaining.remove(
            best_candidate
        )

    return selected


# ============================================================
# DEBUG OUTPUT
# ============================================================

def print_diversity_results(
    results: list[dict],
) -> None:
    """
    Print selected diversity-aware results.
    """

    print()
    print(
        "[DIVERSITY SELECTION]"
    )

    for rank, result in enumerate(
        results,
        start=1,
    ):

        print(
            f"  Rank {rank} | "
            f"Chunk "
            f"{result.get('chunk_index')} | "
            f"Rerank="
            f"{result.get('rerank_score', 0.0):.6f} | "
            f"Penalty="
            f"{result.get('redundancy_penalty', 0.0):.3f} | "
            f"Diversity="
            f"{result.get('diversity_score', 0.0):.6f}"
        )