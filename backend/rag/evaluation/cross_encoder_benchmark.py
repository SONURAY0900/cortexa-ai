"""
Phase 10 follow-up - offline cross-encoder reranking benchmark.

DIAGNOSTIC ONLY. Nothing here is used by the production pipeline.

Question answered:

    If the existing production candidate pool were reranked by a
    general-purpose semantic relevance model, how much would
    retrieval improve?

For every query in the supplied dataset:

    1. Build the exact production candidate pool
       (bottleneck_diagnosis.build_production_pool).
    2. Score every (query, chunk content) pair with a cross-encoder.
    3. Sort the pool by cross-encoder score.
    4. Compare with dense order, the production reranker order and the
       production final results.

The cross-encoder runs through FastEmbed's ONNX TextCrossEncoder,
which is already a project dependency (no torch, no GPU). The model
weights are downloaded to the local FastEmbed cache on first use.

Nothing in this file depends on a particular document, dataset or
domain. document_id, dataset and model are runtime arguments.

Usage:

    python -m rag.evaluation.cross_encoder_benchmark \
        --document-id <uuid> --dataset my_pdf.json \
        --output-json cross_encoder.json \
        [--model Xenova/ms-marco-MiniLM-L-12-v2]
"""

import argparse
import json
from statistics import mean

from fastembed.rerank.cross_encoder import TextCrossEncoder

from rag.evaluation.bottleneck_diagnosis import (
    _quiet,
    build_production_pool,
)
from rag.evaluation.dataset import load_dataset
from rag.evaluation.metrics import (
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from rag.retrieval.dense_retriever import retrieve_dense
from rag.retrieval.reranker import rerank_results
from rag.retrieval.retrieval_pipeline import (
    _get_rerank_count,
    retrieve_context,
)


DEFAULT_MODEL = "Xenova/ms-marco-MiniLM-L-12-v2"

TOP_N = 20

RANKINGS = [
    "dense",
    "pool",
    "reranker",
    "final",
    "cross_encoder",
    "cross_encoder_limited",
]


def _metrics(order: list[int], relevant: list[int]) -> dict:
    return {
        "recall@3": recall_at_k(order, relevant, 3),
        "recall@5": recall_at_k(order, relevant, 5),
        "recall@10": recall_at_k(order, relevant, 10),
        "precision@5": precision_at_k(order, relevant, 5),
        "mrr": reciprocal_rank(order, relevant),
    }


def _rank_of(order: list[int], chunk_index: int) -> int | None:
    for rank, value in enumerate(order, start=1):
        if value == chunk_index:
            return rank
    return None


def cross_encoder_rank(
    model: TextCrossEncoder,
    query: str,
    candidates: list[dict],
) -> list[dict]:
    """
    Score (query, chunk content) for every candidate and sort by score.
    Ties keep the incoming pool order.
    """

    if not candidates:
        return []

    documents = [
        candidate.get("content", "") or ""
        for candidate in candidates
    ]

    scores = list(model.rerank(query, documents))

    scored = [
        {**candidate, "cross_encoder_score": float(score), "pool_rank": rank}
        for rank, (candidate, score) in enumerate(
            zip(candidates, scores), start=1
        )
    ]

    scored.sort(
        key=lambda item: (-item["cross_encoder_score"], item["pool_rank"])
    )

    return scored


def benchmark_query(
    model: TextCrossEncoder,
    query: str,
    document_id: str,
    relevant: list[int],
) -> dict:

    query_info, pool = build_production_pool(query, document_id)

    dense = _quiet(
        retrieve_dense,
        query=query,
        document_id=document_id,
        match_count=TOP_N,
    )

    reranker = _quiet(
        rerank_results,
        query=query_info["original_query"],
        results=pool,
        result_count=len(pool),
        expanded_queries=query_info["retrieval_queries"],
    )

    final = _quiet(
        retrieve_context,
        query=query,
        document_id=document_id,
    )["results"]

    # The cross-encoder scores the original user query, mirroring the
    # production rule that expanded queries are for discovery only.
    cross = cross_encoder_rank(model, query_info["original_query"], pool)

    # Same result-count limit the production pipeline applies for this
    # query type, so R@10 is comparable with the baseline.
    limit = _get_rerank_count(query_info["query_type"], None)

    orders = {
        "dense": [r.get("chunk_index") for r in dense],
        "pool": [r.get("chunk_index") for r in pool],
        "reranker": [r.get("chunk_index") for r in reranker],
        "final": [r.get("chunk_index") for r in final],
        "cross_encoder": [r.get("chunk_index") for r in cross],
    }
    orders["cross_encoder_limited"] = orders["cross_encoder"][:limit]

    movement = [
        {
            "chunk_index": chunk_index,
            "dense_rank": _rank_of(orders["dense"], chunk_index),
            "pool_rank": _rank_of(orders["pool"], chunk_index),
            "reranker_rank": _rank_of(orders["reranker"], chunk_index),
            "final_rank": _rank_of(orders["final"], chunk_index),
            "cross_encoder_rank": _rank_of(orders["cross_encoder"], chunk_index),
        }
        for chunk_index in relevant
    ]

    return {
        "query": query,
        "query_type": query_info["query_type"],
        "result_limit": limit,
        "relevant_chunks": relevant,
        "orders": orders,
        "cross_encoder_scores": {
            r.get("chunk_index"): r["cross_encoder_score"] for r in cross
        },
        "metrics": {
            name: _metrics(orders[name], relevant)
            for name in RANKINGS
        },
        "relevant_movement": movement,
    }


def main():

    parser = argparse.ArgumentParser(
        description="Offline cross-encoder reranking benchmark (diagnostic only)."
    )
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)

    model = TextCrossEncoder(model_name=args.model)

    results = []

    for index, item in enumerate(dataset, start=1):
        print(f"[{index}/{len(dataset)}] {item['query']}", flush=True)
        results.append(
            benchmark_query(
                model=model,
                query=item["query"],
                document_id=args.document_id,
                relevant=item["relevant_chunks"],
            )
        )

    averages = {
        name: {
            metric: mean(r["metrics"][name][metric] for r in results)
            for metric in results[0]["metrics"][name]
        }
        for name in RANKINGS
    }

    output = {
        "document_id": args.document_id,
        "dataset": args.dataset,
        "model": args.model,
        "queries": results,
        "averages": averages,
    }

    with open(args.output_json, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, default=str)

    print()
    print(f"Model: {args.model}")
    for name, values in averages.items():
        print(
            f"{name:<22} "
            + " ".join(f"{k}={v:.3f}" for k, v in values.items())
        )
    print(f"Saved: {args.output_json}")


if __name__ == "__main__":
    main()
