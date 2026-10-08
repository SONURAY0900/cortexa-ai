import argparse

from rag.evaluation.dataset import load_dataset

from rag.evaluation.metrics import (
    recall_at_k,
    precision_at_k,
    reciprocal_rank,
)

from rag.retrieval.retrieval_pipeline import (
    retrieve_context,
)


def evaluate_query(
    query: str,
    document_id: str,
    relevant_chunks: list[int],
) -> dict:
    """
    Evaluate one query against one document.

    document_id is supplied at runtime, so this
    evaluator works with any PDF.
    """

    result = retrieve_context(
        query=query,
        document_id=document_id,
    )

    retrieved_chunks = [
        item["chunk_index"]
        for item in result.get(
            "results",
            [],
        )
    ]

    metrics = {
        "recall@3": recall_at_k(
            retrieved_chunks,
            relevant_chunks,
            3,
        ),
        "recall@5": recall_at_k(
            retrieved_chunks,
            relevant_chunks,
            5,
        ),
        "recall@10": recall_at_k(
            retrieved_chunks,
            relevant_chunks,
            10,
        ),
        "precision@5": precision_at_k(
            retrieved_chunks,
            relevant_chunks,
            5,
        ),
        "mrr": reciprocal_rank(
            retrieved_chunks,
            relevant_chunks,
        ),
    }

    return {
        "query": query,
        "retrieved_chunks": retrieved_chunks,
        "relevant_chunks": relevant_chunks,
        "metrics": metrics,
    }


def run_evaluation(
    document_id: str,
    dataset_file: str,
) -> list[dict]:
    """
    Evaluate one PDF/document.

    The evaluator itself does not know anything
    about the type or contents of the PDF.
    """

    dataset = load_dataset(
        dataset_file
    )

    results = []

    for item in dataset:

        result = evaluate_query(
            query=item["query"],
            document_id=document_id,
            relevant_chunks=item[
                "relevant_chunks"
            ],
        )

        results.append(result)

    return results


def calculate_average_metrics(
    results: list[dict],
) -> dict:
    """
    Calculate average retrieval metrics
    across all evaluation queries.
    """

    if not results:
        return {}

    metric_names = [
        "recall@3",
        "recall@5",
        "recall@10",
        "precision@5",
        "mrr",
    ]

    averages = {}

    for metric_name in metric_names:

        total = sum(
            result["metrics"][metric_name]
            for result in results
        )

        averages[metric_name] = (
            total / len(results)
        )

    return averages


def print_results(
    results: list[dict],
) -> None:
    """
    Print detailed evaluation results.
    """

    print()
    print("=" * 80)
    print("RAG EVALUATION RESULTS")
    print("=" * 80)

    for index, result in enumerate(
        results,
        start=1,
    ):

        print()
        print(f"Query {index}")
        print("-" * 80)

        print(
            f"Query: {result['query']}"
        )

        print(
            "Retrieved chunks: "
            f"{result['retrieved_chunks']}"
        )

        print(
            "Relevant chunks:  "
            f"{result['relevant_chunks']}"
        )

        metrics = result["metrics"]

        print(
            f"Recall@3:    "
            f"{metrics['recall@3']:.3f}"
        )

        print(
            f"Recall@5:    "
            f"{metrics['recall@5']:.3f}"
        )

        print(
            f"Recall@10:   "
            f"{metrics['recall@10']:.3f}"
        )

        print(
            f"Precision@5: "
            f"{metrics['precision@5']:.3f}"
        )

        print(
            f"MRR:         "
            f"{metrics['mrr']:.3f}"
        )

    averages = calculate_average_metrics(
        results
    )

    print()
    print("=" * 80)
    print("AVERAGE METRICS")
    print("=" * 80)

    for name, value in averages.items():

        print(
            f"{name:<15}: {value:.3f}"
        )

    print("=" * 80)


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate RAG retrieval "
            "for any PDF."
        )
    )

    parser.add_argument(
        "--document-id",
        required=True,
        help=(
            "Supabase UUID of the "
            "document to evaluate."
        ),
    )

    parser.add_argument(
        "--dataset",
        required=True,
        help=(
            "JSON evaluation dataset "
            "filename."
        ),
    )

    args = parser.parse_args()

    results = run_evaluation(
        document_id=args.document_id,
        dataset_file=args.dataset,
    )

    print_results(results)


if __name__ == "__main__":
    main()