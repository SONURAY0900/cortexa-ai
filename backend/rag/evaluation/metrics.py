def recall_at_k(
    retrieved_chunks: list[int],
    relevant_chunks: list[int],
    k: int,
) -> float:
    """
    Recall@K.

    Measures the percentage of relevant chunks
    retrieved within the first K results.
    """

    if not relevant_chunks:
        return 0.0

    retrieved_top_k = set(
        retrieved_chunks[:k]
    )

    relevant = set(relevant_chunks)

    hits = len(
        retrieved_top_k.intersection(relevant)
    )

    return hits / len(relevant)


def precision_at_k(
    retrieved_chunks: list[int],
    relevant_chunks: list[int],
    k: int,
) -> float:
    """
    Precision@K.

    Measures the percentage of top-K retrieved
    chunks that are relevant.
    """

    if k <= 0:
        return 0.0

    retrieved_top_k = retrieved_chunks[:k]

    if not retrieved_top_k:
        return 0.0

    relevant = set(relevant_chunks)

    hits = sum(
        1
        for chunk_index in retrieved_top_k
        if chunk_index in relevant
    )

    return hits / len(retrieved_top_k)


def reciprocal_rank(
    retrieved_chunks: list[int],
    relevant_chunks: list[int],
) -> float:
    """
    Reciprocal Rank.

    Returns the reciprocal position of the
    first relevant result.
    """

    relevant = set(relevant_chunks)

    for position, chunk_index in enumerate(
        retrieved_chunks,
        start=1,
    ):
        if chunk_index in relevant:
            return 1.0 / position

    return 0.0