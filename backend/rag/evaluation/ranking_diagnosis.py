import argparse

from rag.retrieval.hybrid_retriever import retrieve_hybrid
from rag.retrieval.reranker import rerank_results


# ============================================================
# HELPERS
# ============================================================

def find_chunk_rank(
    results: list[dict],
    chunk_index: int,
) -> int | None:
    """
    Find the rank of a specific chunk.

    This function contains no document-specific logic.
    """

    for rank, result in enumerate(results, start=1):

        if result.get("chunk_index") == chunk_index:
            return rank

    return None


def print_stage_results(
    stage_name: str,
    results: list[dict],
    relevant_chunks: list[int],
) -> None:
    """
    Print retrieval/ranking results for one pipeline stage.
    """

    print()
    print("=" * 80)
    print(stage_name)
    print("=" * 80)

    if not results:
        print("No results.")
        return

    for rank, result in enumerate(results, start=1):

        chunk_index = result.get("chunk_index")

        is_relevant = (
            chunk_index in relevant_chunks
        )

        marker = (
            " <-- RELEVANT"
            if is_relevant
            else ""
        )

        print(
            f"Rank {rank:2d} | "
            f"Chunk {str(chunk_index):>3} | "
            f"RRF={result.get('rrf_score', 0.0):.6f} | "
            f"Dense={result.get('dense_rank')} | "
            f"Keyword={result.get('keyword_rank')}"
            f"{marker}"
        )


def print_rank_comparison(
    hybrid_results: list[dict],
    reranked_results: list[dict],
    relevant_chunks: list[int],
) -> None:
    """
    Compare relevant chunk positions before and after reranking.
    """

    print()
    print("=" * 80)
    print("RANK MOVEMENT")
    print("=" * 80)

    for chunk_index in relevant_chunks:

        hybrid_rank = find_chunk_rank(
            hybrid_results,
            chunk_index,
        )

        rerank_rank = find_chunk_rank(
            reranked_results,
            chunk_index,
        )

        if hybrid_rank is None:
            hybrid_text = "NOT FOUND"
        else:
            hybrid_text = str(hybrid_rank)

        if rerank_rank is None:
            rerank_text = "NOT FOUND"
        else:
            rerank_text = str(rerank_rank)

        print(
            f"Chunk {chunk_index}: "
            f"Hybrid={hybrid_text} "
            f"-> Reranker={rerank_text}"
        )


# ============================================================
# DIAGNOSIS
# ============================================================

def diagnose_query(
    query: str,
    document_id: str,
    relevant_chunks: list[int],
    candidate_count: int = 20,
) -> dict:
    """
    Diagnose retrieval and reranking for one query.

    This function is completely document-agnostic.

    The caller provides:
        - document_id
        - query
        - relevant chunk indexes

    No PDF-specific assumptions are made.
    """

    print()
    print("#" * 80)
    print(f"QUERY: {query}")
    print(f"DOCUMENT ID: {document_id}")
    print(
        f"EXPECTED RELEVANT CHUNKS: "
        f"{relevant_chunks}"
    )
    print("#" * 80)

    # ========================================================
    # STAGE 1 — HYBRID RETRIEVAL
    # ========================================================

    hybrid_results = retrieve_hybrid(
        query=query,
        document_id=document_id,
        candidate_count=candidate_count,
        result_count=candidate_count,
    )

    print_stage_results(
        stage_name="STAGE 1 — HYBRID / RRF",
        results=hybrid_results,
        relevant_chunks=relevant_chunks,
    )

    print()
    print("Hybrid relevant-chunk ranks:")

    hybrid_ranks = {}

    for chunk_index in relevant_chunks:

        rank = find_chunk_rank(
            hybrid_results,
            chunk_index,
        )

        hybrid_ranks[chunk_index] = rank

        print(
            f"  Chunk {chunk_index}: "
            f"{rank if rank is not None else 'NOT FOUND'}"
        )

    # ========================================================
    # STAGE 2 — RERANKING
    # ========================================================

    reranked_results = rerank_results(
        query=query,
        results=hybrid_results,
        result_count=candidate_count,
    )

    print_stage_results(
        stage_name="STAGE 2 — RERANKING",
        results=reranked_results,
        relevant_chunks=relevant_chunks,
    )

    print()
    print("Reranked relevant-chunk ranks:")

    rerank_ranks = {}

    for chunk_index in relevant_chunks:

        rank = find_chunk_rank(
            reranked_results,
            chunk_index,
        )

        rerank_ranks[chunk_index] = rank

        print(
            f"  Chunk {chunk_index}: "
            f"{rank if rank is not None else 'NOT FOUND'}"
        )

    # ========================================================
    # RANK COMPARISON
    # ========================================================

    print_rank_comparison(
        hybrid_results=hybrid_results,
        reranked_results=reranked_results,
        relevant_chunks=relevant_chunks,
    )

    return {
        "query": query,
        "document_id": document_id,
        "relevant_chunks": relevant_chunks,
        "hybrid_results": hybrid_results,
        "reranked_results": reranked_results,
        "hybrid_ranks": hybrid_ranks,
        "rerank_ranks": rerank_ranks,
    }


# ============================================================
# DATASET-BASED DIAGNOSIS
# ============================================================

def diagnose_dataset(
    document_id: str,
    dataset: list[dict],
    candidate_count: int = 20,
) -> list[dict]:
    """
    Diagnose every query from an evaluation dataset.

    The dataset supplies the ground truth.

    Therefore this works with any PDF.
    """

    results = []

    for index, item in enumerate(
        dataset,
        start=1,
    ):

        print()
        print()
        print(
            f"########## "
            f"QUERY {index}/{len(dataset)} "
            f"##########"
        )

        query = item["query"]

        relevant_chunks = item[
            "relevant_chunks"
        ]

        result = diagnose_query(
            query=query,
            document_id=document_id,
            relevant_chunks=relevant_chunks,
            candidate_count=candidate_count,
        )

        results.append(result)

    return results


# ============================================================
# DATASET LOADING
# ============================================================

def load_dataset_file(
    dataset_file: str,
) -> list[dict]:
    """
    Load an evaluation dataset using
    the existing generic dataset loader.
    """

    from rag.evaluation.dataset import (
        load_dataset,
    )

    return load_dataset(
        dataset_file
    )


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    results: list[dict],
) -> None:
    """
    Print a generic diagnosis summary.
    """

    print()
    print()
    print("#" * 80)
    print("RANKING DIAGNOSIS SUMMARY")
    print("#" * 80)

    hybrid_found = 0
    reranker_found = 0

    hybrid_top5 = 0
    reranker_top5 = 0

    total_relevant = 0

    for result in results:

        for chunk_index in result[
            "relevant_chunks"
        ]:

            total_relevant += 1

            hybrid_rank = result[
                "hybrid_ranks"
            ].get(chunk_index)

            rerank_rank = result[
                "rerank_ranks"
            ].get(chunk_index)

            if hybrid_rank is not None:
                hybrid_found += 1

                if hybrid_rank <= 5:
                    hybrid_top5 += 1

            if rerank_rank is not None:
                reranker_found += 1

                if rerank_rank <= 5:
                    reranker_top5 += 1

    print()
    print(
        f"Relevant chunks evaluated: "
        f"{total_relevant}"
    )

    if total_relevant > 0:

        print(
            f"Hybrid found: "
            f"{hybrid_found}/{total_relevant}"
        )

        print(
            f"Hybrid top-5: "
            f"{hybrid_top5}/{total_relevant}"
        )

        print(
            f"Reranker found: "
            f"{reranker_found}/{total_relevant}"
        )

        print(
            f"Reranker top-5: "
            f"{reranker_top5}/{total_relevant}"
        )

    print()
    print("#" * 80)


# ============================================================
# CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Generic RAG ranking diagnosis "
            "for any PDF/document."
        )
    )

    parser.add_argument(
        "--document-id",
        required=True,
        help=(
            "Supabase UUID of the document "
            "to diagnose."
        ),
    )

    parser.add_argument(
        "--dataset",
        required=True,
        help=(
            "Evaluation dataset filename "
            "inside rag/evaluation/datasets."
        ),
    )

    parser.add_argument(
        "--candidate-count",
        type=int,
        default=20,
        help=(
            "Number of hybrid candidates "
            "to inspect. Default: 20."
        ),
    )

    args = parser.parse_args()

    dataset = load_dataset_file(
        args.dataset
    )

    results = diagnose_dataset(
        document_id=args.document_id,
        dataset=dataset,
        candidate_count=args.candidate_count,
    )

    print_summary(
        results
    )


if __name__ == "__main__":
    main()