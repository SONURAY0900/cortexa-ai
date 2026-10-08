import argparse

from rag.retrieval.hybrid_retriever import retrieve_hybrid
from rag.retrieval.reranker import rerank_results


def print_hybrid_diagnostics(
    results: list[dict],
    relevant_chunks: list[int],
) -> None:

    print()
    print("=" * 100)
    print("HYBRID CANDIDATE SIGNALS")
    print("=" * 100)

    for rank, result in enumerate(results, start=1):

        chunk_index = result.get("chunk_index")

        marker = (
            " <-- RELEVANT"
            if chunk_index in relevant_chunks
            else ""
        )

        print(
            f"{rank:2d}. "
            f"Chunk {chunk_index:3} | "
            f"DenseSim={float(result.get('similarity', 0.0)):.6f} | "
            f"KeywordScore={float(result.get('keyword_score', 0.0)):.6f} | "
            f"DenseRank={result.get('dense_rank')} | "
            f"KeywordRank={result.get('keyword_rank')} | "
            f"RRF={float(result.get('rrf_score', 0.0)):.6f}"
            f"{marker}"
        )


def print_reranker_diagnostics(
    results: list[dict],
    relevant_chunks: list[int],
) -> None:

    print()
    print("=" * 100)
    print("RERANKER SIGNAL ANALYSIS")
    print("=" * 100)

    for rank, result in enumerate(results, start=1):

        chunk_index = result.get("chunk_index")

        marker = (
            " <-- RELEVANT"
            if chunk_index in relevant_chunks
            else ""
        )

        print()
        print(
            f"Rank {rank:2d} | "
            f"Chunk {chunk_index:3}{marker}"
        )

        print(
            f"  Dense similarity : "
            f"{float(result.get('similarity', 0.0)):.6f}"
        )

        print(
            f"  Keyword score    : "
            f"{float(result.get('keyword_score', 0.0)):.6f}"
        )

        print(
            f"  Dense rank       : "
            f"{result.get('dense_rank')}"
        )

        print(
            f"  Keyword rank     : "
            f"{result.get('keyword_rank')}"
        )

        print(
            f"  RRF              : "
            f"{float(result.get('raw_rrf_score', 0.0)):.6f}"
        )

        print(
            f"  Normalized RRF   : "
            f"{float(result.get('normalized_rrf_score', 0.0)):.6f}"
        )

        print(
            f"  Lexical overlap  : "
            f"{float(result.get('original_keyword_overlap', 0.0)):.6f}"
        )

        print(
            f"  Phrase match     : "
            f"{float(result.get('phrase_match', 0.0)):.6f}"
        )

        print(
            f"  Exact match      : "
            f"{float(result.get('exact_query_match', 0.0)):.6f}"
        )

        print(
            f"  Current final    : "
            f"{float(result.get('rerank_score', 0.0)):.6f}"
        )

        content = result.get("content", "")

        print(
            f"  Content          : "
            f"{content[:250]}"
        )


def summarize_relevant_chunks(
    results: list[dict],
    relevant_chunks: list[int],
) -> None:

    print()
    print("=" * 100)
    print("RELEVANT CHUNK SUMMARY")
    print("=" * 100)

    result_by_chunk = {
        result.get("chunk_index"): result
        for result in results
    }

    for chunk_index in relevant_chunks:

        result = result_by_chunk.get(chunk_index)

        if result is None:

            print(
                f"Chunk {chunk_index}: "
                "NOT FOUND IN HYBRID CANDIDATES"
            )

            continue

        print(
            f"Chunk {chunk_index}: "
            f"DenseSim="
            f"{float(result.get('similarity', 0.0)):.6f} | "
            f"KeywordScore="
            f"{float(result.get('keyword_score', 0.0)):.6f} | "
            f"RRF="
            f"{float(result.get('rrf_score', 0.0)):.6f} | "
            f"Rerank="
            f"{float(result.get('rerank_score', 0.0)):.6f}"
        )


def diagnose(
    document_id: str,
    query: str,
    relevant_chunks: list[int],
) -> None:

    print()
    print("#" * 100)
    print("GENERIC RERANKER SIGNAL DIAGNOSIS")
    print("#" * 100)

    print(f"Document ID: {document_id}")
    print(f"Query: {query}")
    print(f"Relevant chunks: {relevant_chunks}")

    # ---------------------------------------------------------
    # HYBRID RETRIEVAL
    # ---------------------------------------------------------

    hybrid_results = retrieve_hybrid(
        query=query,
        document_id=document_id,
        candidate_count=20,
        result_count=20,
    )

    print_hybrid_diagnostics(
        results=hybrid_results,
        relevant_chunks=relevant_chunks,
    )

    # ---------------------------------------------------------
    # RERANKING
    # ---------------------------------------------------------

    reranked_results = rerank_results(
        query=query,
        results=hybrid_results,
        result_count=20,
        expanded_queries=[query],
    )

    print_reranker_diagnostics(
        results=reranked_results,
        relevant_chunks=relevant_chunks,
    )

    # ---------------------------------------------------------
    # RELEVANT CHUNK SUMMARY
    # ---------------------------------------------------------

    summarize_relevant_chunks(
        results=reranked_results,
        relevant_chunks=relevant_chunks,
    )

    print()
    print("=" * 100)
    print("DIAGNOSIS COMPLETE")
    print("=" * 100)


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Generic RAG reranker signal diagnosis. "
            "Works with any PDF."
        )
    )

    parser.add_argument(
        "--document-id",
        required=True,
        help="Supabase document UUID",
    )

    parser.add_argument(
        "--query",
        required=True,
        help="User query",
    )

    parser.add_argument(
        "--relevant-chunks",
        required=True,
        help=(
            "Comma-separated ground-truth "
            "chunk indexes"
        ),
    )

    args = parser.parse_args()

    relevant_chunks = [
        int(value.strip())
        for value in args.relevant_chunks.split(",")
        if value.strip()
    ]

    diagnose(
        document_id=args.document_id,
        query=args.query,
        relevant_chunks=relevant_chunks,
    )


if __name__ == "__main__":
    main()