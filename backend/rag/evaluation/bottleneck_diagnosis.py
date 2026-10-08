"""
Phase 10 - stage-level bottleneck diagnosis.

DIAGNOSTIC ONLY. This script calls the existing production retrieval
functions and records what each stage does with the ground-truth
relevant chunks. It never changes any production setting.

Stages measured per query:

    dense      retrieve_dense(original query)            top-20
    keyword    retrieve_keyword(original query)          top-20
    hybrid_oq  retrieve_hybrid(original query)           top-20
    hybrid     production candidate pool (all transformed
               queries, merged exactly as the pipeline does)
    reranker   rerank_results over the full pool, original query
    final      retrieve_context()["results"]  (what evaluator.py scores)
    context    retrieve_context()["context_chunks"]  (what the LLM sees)

The query-category labels below are evaluation metadata for the
supplied dataset only. They are never used by production code.

Usage:

    python -m rag.evaluation.bottleneck_diagnosis \
        --document-id <uuid> --dataset my_pdf.json \
        --output-json diagnosis.json
"""

import argparse
import contextlib
import io
import json
from statistics import mean

from database.connection import supabase

from rag.evaluation.dataset import load_dataset
from rag.evaluation.metrics import (
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from rag.query.query_transformer import transform_query
from rag.retrieval.dense_retriever import retrieve_dense
from rag.retrieval.hybrid_retriever import retrieve_hybrid
from rag.retrieval.keyword_retriever import retrieve_keyword
from rag.retrieval.reranker import rerank_results
from rag.retrieval.retrieval_pipeline import (
    DEFAULT_CANDIDATE_COUNT,
    DEFAULT_HYBRID_COUNT,
    _merge_retrieval_results,
    retrieve_context,
)


TOP_N = 20

STAGES = [
    "dense",
    "keyword",
    "hybrid_oq",
    "hybrid",
    "reranker",
    "final",
]

SIGNALS = [
    "normalized_rrf_score",
    "original_keyword_overlap",
    "phrase_match",
    "exact_query_match",
    "rerank_score",
]


# ============================================================
# HELPERS
# ============================================================

def _quiet(function, *args, **kwargs):
    """
    Run a production function while discarding its debug prints.
    """

    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def _chunk_order(results: list[dict]) -> list[int]:
    return [
        result.get("chunk_index")
        for result in results
    ]


def _rank_of(order: list[int], chunk_index: int) -> int | None:
    for rank, value in enumerate(order, start=1):
        if value == chunk_index:
            return rank
    return None


def _stage_metrics(order: list[int], relevant: list[int]) -> dict:
    return {
        "recall@3": recall_at_k(order, relevant, 3),
        "recall@5": recall_at_k(order, relevant, 5),
        "recall@10": recall_at_k(order, relevant, 10),
        "precision@5": precision_at_k(order, relevant, 5),
        "mrr": reciprocal_rank(order, relevant),
    }


def _document_chunk_count(document_id: str) -> int:
    response = (
        supabase
        .table("document_chunks")
        .select("chunk_index", count="exact")
        .eq("document_id", document_id)
        .execute()
    )
    return response.count or len(response.data or [])


# ============================================================
# PER-QUERY CAPTURE
# ============================================================

def build_production_pool(
    query: str,
    document_id: str,
) -> tuple[dict, list[dict]]:
    """
    Rebuild the exact candidate pool the production pipeline passes to
    the reranker: every transformed query through hybrid retrieval,
    merged with the pipeline's own merge function, limited to the
    default candidate count.
    """

    query_info = transform_query(query)

    result_sets = [
        _quiet(
            retrieve_hybrid,
            query=retrieval_query,
            document_id=document_id,
            candidate_count=DEFAULT_CANDIDATE_COUNT,
            result_count=DEFAULT_HYBRID_COUNT,
        )
        for retrieval_query in query_info["retrieval_queries"]
    ]

    pool = _merge_retrieval_results(result_sets)[
        :DEFAULT_CANDIDATE_COUNT
    ]

    return query_info, pool


def capture_query(
    query: str,
    document_id: str,
    relevant: list[int],
) -> dict:

    query_info, hybrid = build_production_pool(query, document_id)

    dense = _quiet(
        retrieve_dense,
        query=query,
        document_id=document_id,
        match_count=TOP_N,
    )

    keyword = _quiet(
        retrieve_keyword,
        query=query,
        document_id=document_id,
        match_count=TOP_N,
    )

    hybrid_oq = _quiet(
        retrieve_hybrid,
        query=query,
        document_id=document_id,
        candidate_count=TOP_N,
        result_count=TOP_N,
    )

    # Full reranked order of the pool (production weights, original
    # query). Production truncates this list; the order is identical.
    reranker = _quiet(
        rerank_results,
        query=query_info["original_query"],
        results=hybrid,
        result_count=len(hybrid),
        expanded_queries=query_info["retrieval_queries"],
    )

    pipeline = _quiet(
        retrieve_context,
        query=query,
        document_id=document_id,
    )

    orders = {
        "dense": _chunk_order(dense),
        "keyword": _chunk_order(keyword),
        "hybrid_oq": _chunk_order(hybrid_oq),
        "hybrid": _chunk_order(hybrid),
        "reranker": _chunk_order(reranker),
        "final": _chunk_order(pipeline["results"]),
    }

    context_chunks = [
        {
            "chunk_index": item.get("chunk_index"),
            "is_retrieved": bool(item.get("is_retrieved")),
            "is_neighbor": bool(item.get("is_neighbor")),
        }
        for item in pipeline["context_chunks"]
    ]

    dense_by_chunk = {r.get("chunk_index"): r for r in dense}
    keyword_by_chunk = {r.get("chunk_index"): r for r in keyword}
    hybrid_by_chunk = {r.get("chunk_index"): r for r in hybrid}
    rerank_by_chunk = {r.get("chunk_index"): r for r in reranker}

    pages = {}
    for source in (dense, keyword, hybrid):
        for r in source:
            pages.setdefault(r.get("chunk_index"), r.get("page_number"))

    chunks = []

    for chunk_index in relevant:

        d = dense_by_chunk.get(chunk_index, {})
        k = keyword_by_chunk.get(chunk_index, {})
        h = hybrid_by_chunk.get(chunk_index, {})
        r = rerank_by_chunk.get(chunk_index, {})

        chunks.append(
            {
                "chunk_index": chunk_index,
                "page_number": pages.get(chunk_index),
                "dense_rank": _rank_of(orders["dense"], chunk_index),
                "dense_similarity": d.get("similarity"),
                "keyword_rank": _rank_of(orders["keyword"], chunk_index),
                "keyword_score": k.get("keyword_score"),
                "hybrid_oq_rank": _rank_of(orders["hybrid_oq"], chunk_index),
                "hybrid_rank": _rank_of(orders["hybrid"], chunk_index),
                "rrf_score": h.get("rrf_score"),
                "rerank_rank": _rank_of(orders["reranker"], chunk_index),
                "final_rank": _rank_of(orders["final"], chunk_index),
                "in_context": any(
                    c["chunk_index"] == chunk_index
                    for c in context_chunks
                ),
                "in_context_as_neighbor": any(
                    c["chunk_index"] == chunk_index and c["is_neighbor"]
                    for c in context_chunks
                ),
                "signals": {
                    name: r.get(name)
                    for name in SIGNALS
                },
            }
        )

    # Signals for every candidate, so relevant vs non-relevant
    # candidates can be compared.
    candidate_signals = [
        {
            "chunk_index": r.get("chunk_index"),
            "relevant": r.get("chunk_index") in relevant,
            "rerank_rank": rank,
            "hybrid_rank": _rank_of(orders["hybrid"], r.get("chunk_index")),
            **{name: r.get(name) for name in SIGNALS},
        }
        for rank, r in enumerate(reranker, start=1)
    ]

    return {
        "query": query,
        "query_type": query_info["query_type"],
        "retrieval_queries": query_info["retrieval_queries"],
        "relevant_chunks": relevant,
        "orders": orders,
        "context_chunks": context_chunks,
        "metrics": {
            stage: _stage_metrics(orders[stage], relevant)
            for stage in STAGES
        },
        "relevant_chunk_detail": chunks,
        "candidate_signals": candidate_signals,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Phase 10 stage-level bottleneck diagnosis."
    )
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)

    captured = []

    for index, item in enumerate(dataset, start=1):
        print(f"[{index}/{len(dataset)}] {item['query']}", flush=True)
        captured.append(
            capture_query(
                query=item["query"],
                document_id=args.document_id,
                relevant=item["relevant_chunks"],
            )
        )

    output = {
        "document_id": args.document_id,
        "dataset": args.dataset,
        "document_chunk_count": _document_chunk_count(args.document_id),
        "queries": captured,
        "averages": {
            stage: {
                metric: mean(
                    q["metrics"][stage][metric] for q in captured
                )
                for metric in captured[0]["metrics"][stage]
            }
            for stage in STAGES
        },
    }

    with open(args.output_json, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, default=str)

    print()
    print(f"Document chunks: {output['document_chunk_count']}")
    for stage, values in output["averages"].items():
        print(
            f"{stage:<10} "
            + " ".join(f"{k}={v:.3f}" for k, v in values.items())
        )
    print(f"Saved: {args.output_json}")


if __name__ == "__main__":
    main()
