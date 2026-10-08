"""
Retrieval ablation experiments (DIAGNOSTIC ONLY).

Runs the unchanged production pipeline (retrieve_context) for every query
in a dataset under one experimental variant at a time. Variants are
applied as temporary in-process patches and are undone after each run;
no production file, database row or SQL function is modified.

Variants:

    baseline            current production behaviour
    no_transform        query transformation disabled (original query only,
                        same query type) - shows what transformation adds
    latin_context       context builder with the pre-Phase-14 Latin-only
                        query tokenizer (for before/after comparison)
    df<X>               document-frequency keyword filter: query keywords
                        that occur in >= X% of the document's chunks are
                        dropped before keyword search (if every keyword
                        would be dropped, the single rarest one is kept)

Measures per variant: R@3, R@5, R@10, P@5, MRR on the final results,
candidate-pool recall (relevant chunks present before reranking) and
context recall (relevant chunks present in the final LLM context).

Nothing here depends on a particular document, dataset or language.

Usage:

    python -m rag.evaluation.retrieval_ablation \
        --document-id <uuid> --dataset <file> \
        --variants baseline no_transform df30 df50 \
        --output-json results.json
"""

import argparse
import contextlib
import io
import json
from statistics import mean
from unittest import mock

import re

import rag.retrieval.context_builder as context_module
import rag.retrieval.keyword_retriever as keyword_module
import rag.retrieval.retrieval_pipeline as pipeline_module

from rag.evaluation.dataset import load_dataset
from rag.evaluation.embedding_benchmark import load_document_chunks
from rag.evaluation.metrics import (
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from rag.query.query_transformer import transform_query
from rag.retrieval.text_tokens import unicode_tokens


def _quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def document_frequencies(chunks: list[dict]) -> dict[str, float]:
    """
    Share of chunks containing each token (Unicode tokens, any script).
    """

    counts: dict[str, int] = {}

    for chunk in chunks:
        for token in set(unicode_tokens(chunk.get("content") or "")):
            counts[token] = counts.get(token, 0) + 1

    total = max(len(chunks), 1)

    return {token: count / total for token, count in counts.items()}


def df_filtered_extractor(original_extractor, frequencies, threshold):
    """
    Wrap extract_keywords: drop keywords that occur in at least
    `threshold` of the document's chunks; keep the rarest one if all
    would be dropped.
    """

    def extract(query: str) -> list[str]:

        keywords = original_extractor(query)

        if not keywords:
            return keywords

        kept = [
            keyword
            for keyword in keywords
            if frequencies.get(keyword, 0.0) < threshold
        ]

        if kept:
            return kept

        return [min(keywords, key=lambda k: frequencies.get(k, 0.0))]

    return extract


def no_transform(query: str) -> dict:
    """
    Original query only, but keep the production query type so result
    limits stay identical.
    """

    info = transform_query(query)

    return {
        **info,
        "retrieval_queries": [info["original_query"]],
    }


def _patches(variant: str, frequencies: dict[str, float]) -> list:

    if variant == "baseline":
        return []

    if variant == "no_transform":
        return [
            mock.patch.object(pipeline_module, "transform_query", no_transform),
        ]

    if variant == "latin_context":
        return [
            mock.patch.object(
                context_module,
                "_tokenize_query",
                lambda text: {
                    token
                    for token in re.findall(r"[A-Za-z0-9_+#.-]+", (text or "").lower())
                    if len(token) >= 2
                },
            )
        ]

    if variant.startswith("df"):
        threshold = int(variant[2:]) / 100
        return [
            mock.patch.object(
                keyword_module,
                "extract_keywords",
                df_filtered_extractor(
                    keyword_module.extract_keywords,
                    frequencies,
                    threshold,
                ),
            )
        ]

    raise ValueError(f"Unknown variant: {variant}")


def run_variant(
    variant: str,
    document_id: str,
    dataset: list[dict],
    frequencies: dict[str, float],
) -> dict:

    rows = []

    # Capture the candidate pool the pipeline passes to the reranker.
    captured = {}
    original_rerank = pipeline_module.rerank_results

    def capturing_rerank(*args, **kwargs):
        captured["pool"] = list(kwargs.get("results", args[1] if len(args) > 1 else []))
        return original_rerank(*args, **kwargs)

    with contextlib.ExitStack() as stack:

        for patch in _patches(variant, frequencies):
            stack.enter_context(patch)

        stack.enter_context(
            mock.patch.object(pipeline_module, "rerank_results", capturing_rerank)
        )

        for item in dataset:

            query = item["query"]
            relevant = item["relevant_chunks"]

            captured.clear()

            result = _quiet(
                pipeline_module.retrieve_context,
                query=query,
                document_id=document_id,
            )

            pool = captured.get("pool", [])
            info = {
                "query_type": result["query_type"],
                "retrieval_queries": result["retrieval_queries"],
            }

            order = [r.get("chunk_index") for r in result["results"]]
            context = {c.get("chunk_index") for c in result["context_chunks"]}
            pool_set = {r.get("chunk_index") for r in pool}

            rows.append(
                {
                    "query": query,
                    "category": item.get("category"),
                    "query_language": item.get("query_language"),
                    "query_type": info["query_type"],
                    "retrieval_queries": info["retrieval_queries"],
                    "relevant_chunks": relevant,
                    "order": order,
                    "metrics": {
                        "recall@3": recall_at_k(order, relevant, 3),
                        "recall@5": recall_at_k(order, relevant, 5),
                        "recall@10": recall_at_k(order, relevant, 10),
                        "precision@5": precision_at_k(order, relevant, 5),
                        "mrr": reciprocal_rank(order, relevant),
                        "pool_recall": len(pool_set & set(relevant)) / len(relevant),
                        "context_recall": len(context & set(relevant)) / len(relevant),
                    },
                }
            )

    return {
        "variant": variant,
        "queries": rows,
        "average": {
            key: mean(row["metrics"][key] for row in rows)
            for key in rows[0]["metrics"]
        },
    }


def main():

    parser = argparse.ArgumentParser(description="Retrieval ablation experiments.")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--variants", nargs="+", default=["baseline"])
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    chunks = load_document_chunks(args.document_id)
    frequencies = document_frequencies(chunks)

    results = []

    for variant in args.variants:
        print(f"[{variant}]", flush=True)
        output = run_variant(variant, args.document_id, dataset, frequencies)
        results.append(output)
        print(
            f"{variant:<14}"
            + " ".join(f"{k}={v:.3f}" for k, v in output["average"].items()),
            flush=True,
        )

    with open(args.output_json, "w", encoding="utf-8") as file:
        json.dump(
            {
                "document_id": args.document_id,
                "dataset": args.dataset,
                "chunk_count": len(chunks),
                "results": results,
            },
            file,
            indent=2,
            ensure_ascii=False,
        )


if __name__ == "__main__":
    main()
