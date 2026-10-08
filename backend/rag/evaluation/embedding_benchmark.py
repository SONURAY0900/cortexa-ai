"""
Phase 11 - offline embedding model benchmark.

DIAGNOSTIC ONLY. Nothing in this file is used by production code, and
it never writes to Supabase. The only database access is a read of the
chunks belonging to the --document-id given on the command line (plus
the read-only keyword RPC and neighbor lookups the unchanged production
pipeline already performs).

What it measures, for the baseline model and every candidate model:

    1. DENSE ONLY (controlled experiment)
       The document's existing chunks are embedded in memory with the
       model, each evaluation query is embedded, chunks are ranked by
       cosine similarity, and the existing metrics are computed. Only
       the embedding model changes between runs.

    2. FULL PIPELINE (comparable with the accepted baseline)
       The unchanged production pipeline (retrieve_context) is run with
       its dense retrieval step answered from the in-memory index of
       the model under test. Query transformation, keyword retrieval,
       RRF, reranking, diversity selection and result limits are
       exactly production. The substitution is a temporary in-process
       patch that is undone after every run; production files are not
       modified.

The baseline model is run first and must reproduce both the Supabase
dense ranking and the accepted pipeline baseline before candidate
results mean anything.

Nothing here depends on a particular document, dataset, query wording
or category. Query categories are read from an optional "category"
field in the dataset; when absent, the production query type
(broad_document / list / specific) is used for grouping.

Usage:

    python -m rag.evaluation.embedding_benchmark \
        --document-id <uuid> --dataset <file in datasets/ or path> \
        --output-json results.json \
        [--models MODEL [MODEL ...]]
"""

import argparse
import contextlib
import io
import json
import time
from statistics import mean, median
from unittest import mock

import numpy as np
from fastembed import TextEmbedding

from database.connection import supabase

from rag.embeddings.embeddings import MODEL_NAME as PRODUCTION_MODEL
from rag.evaluation.dataset import load_dataset
from rag.evaluation.metrics import (
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from rag.query.query_transformer import transform_query
from rag.retrieval import hybrid_retriever
from rag.retrieval.dense_retriever import retrieve_dense
from rag.retrieval.retrieval_pipeline import retrieve_context


# Default candidates: CPU, ONNX via FastEmbed, no extra dependencies.
# Override with --models.
DEFAULT_CANDIDATES = [
    "BAAI/bge-small-en-v1.5",
    "snowflake/snowflake-arctic-embed-s",
    "BAAI/bge-base-en-v1.5",
]

# Query instructions published on each model card for short-query ->
# passage retrieval. FastEmbed's query_embed does not add these for the
# models below, so they are applied here. Model-level, not
# document-level. Models not listed get no instruction.
MODEL_QUERY_INSTRUCTIONS = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "snowflake/snowflake-arctic-embed-s": "Represent this sentence for searching relevant passages: ",
    "intfloat/multilingual-e5-small": "query: ",
    "intfloat/multilingual-e5-base": "query: ",
    "intfloat/multilingual-e5-large": "query: ",
}

# Passage prefixes published on model cards (FastEmbed does not add them).
MODEL_PASSAGE_PREFIXES = {
    "intfloat/multilingual-e5-small": "passage: ",
    "intfloat/multilingual-e5-base": "passage: ",
    "intfloat/multilingual-e5-large": "passage: ",
}

# Models not in FastEmbed's built-in list but published with ONNX weights
# on Hugging Face. Registered with FastEmbed's own add_custom_model API
# (no extra dependency). Values: (dimension, ONNX file, approx size GB).
CUSTOM_ONNX_MODELS = {
    "intfloat/multilingual-e5-small": (384, "onnx/model.onnx", 0.47),
    "intfloat/multilingual-e5-base": (768, "onnx/model.onnx", 1.11),
}


def ensure_model_available(model_name: str) -> None:
    """
    Register a known Hugging Face ONNX model with FastEmbed if FastEmbed
    does not already support it. Mean pooling + L2 normalization, as
    specified by the sentence-transformers configs of these models.
    """

    supported = {m["model"] for m in TextEmbedding.list_supported_models()}

    if model_name in supported or model_name not in CUSTOM_ONNX_MODELS:
        return

    from fastembed.common.model_description import ModelSource, PoolingType

    dim, model_file, size = CUSTOM_ONNX_MODELS[model_name]

    TextEmbedding.add_custom_model(
        model=model_name,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=model_name),
        dim=dim,
        model_file=model_file,
        size_in_gb=size,
    )

TOP_N = 20

# A chunk counts as a "fragment" when it is shorter than this share of
# the document's median chunk length. Relative, so it adapts to any PDF.
FRAGMENT_RATIO = 0.20

PAGE_SIZE = 1000


# ============================================================
# HELPERS
# ============================================================

def _quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def _metrics(order: list[int], relevant: list[int]) -> dict:
    relevant_set = set(relevant)
    return {
        "recall@3": recall_at_k(order, relevant, 3),
        "recall@5": recall_at_k(order, relevant, 5),
        "recall@10": recall_at_k(order, relevant, 10),
        "precision@5": precision_at_k(order, relevant, 5),
        "mrr": reciprocal_rank(order, relevant),
        "hits@3": len(set(order[:3]) & relevant_set),
        "hits@5": len(set(order[:5]) & relevant_set),
        "hits@10": len(set(order[:10]) & relevant_set),
    }


def _average(per_query: list[dict]) -> dict:
    keys = per_query[0].keys()
    return {
        key: (
            sum(item[key] for item in per_query)
            if key.startswith("hits@")
            else mean(item[key] for item in per_query)
        )
        for key in keys
    }


def load_document_chunks(document_id: str) -> list[dict]:
    """
    Read-only load of every chunk of one document, in chunk order.
    Paginates so it works for documents of any size.
    """

    chunks = []
    start = 0

    while True:
        rows = (
            supabase
            .table("document_chunks")
            .select("id, document_id, chunk_index, content, page_number")
            .eq("document_id", document_id)
            .order("chunk_index")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        chunks.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    return chunks


def peak_memory_mb() -> float | None:
    """
    Peak resident memory of this process in MB (standard library only).
    Windows: PeakWorkingSetSize. Unix: ru_maxrss.
    """

    try:
        import sys

        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            # Explicit types are required: without them the
            # GetCurrentProcess() pseudo-handle is truncated to a
            # 32-bit int, the call fails and the peak reads as 0.
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if not psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                return None
            return counters.PeakWorkingSetSize / 1e6

        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3

    except Exception:
        return None


# ============================================================
# IN-MEMORY DENSE INDEX
# ============================================================

class InMemoryDenseIndex:
    """
    Embeds one document's chunks with one model and answers dense
    queries by cosine similarity, returning rows shaped like the
    production match_document_chunks RPC.

    production_api=True reproduces the production call (plain embed()
    for both chunks and queries). Otherwise the model's own
    query_embed / passage_embed are used, plus the query instruction
    from MODEL_QUERY_INSTRUCTIONS when the model card prescribes one.
    """

    def __init__(
        self,
        model_name: str,
        chunks: list[dict],
        production_api: bool,
    ):
        self.model_name = model_name
        self.chunks = chunks
        self.production_api = production_api
        self.query_instruction = (
            "" if production_api
            else MODEL_QUERY_INSTRUCTIONS.get(model_name, "")
        )
        self.query_times: list[float] = []
        self.rank_times: list[float] = []
        self._query_cache: dict[str, np.ndarray] = {}

        ensure_model_available(model_name)

        started = time.perf_counter()
        self.model = TextEmbedding(model_name=model_name)
        self.load_seconds = time.perf_counter() - started

        passage_prefix = (
            "" if production_api
            else MODEL_PASSAGE_PREFIXES.get(model_name, "")
        )

        texts = [
            passage_prefix + (chunk.get("content") or "")
            for chunk in chunks
        ]

        started = time.perf_counter()
        if production_api:
            vectors = list(self.model.embed(texts))
        else:
            vectors = list(self.model.passage_embed(texts))
        self.chunk_embed_seconds = time.perf_counter() - started

        matrix = np.array(vectors, dtype=np.float32)
        self.dimensions = int(matrix.shape[1])
        self.matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    def embed_query(self, query: str) -> np.ndarray:
        if query in self._query_cache:
            return self._query_cache[query]

        started = time.perf_counter()
        if self.production_api:
            vector = list(self.model.embed([query]))[0]
        else:
            vector = list(self.model.query_embed(self.query_instruction + query))[0]
        self.query_times.append(time.perf_counter() - started)

        vector = np.asarray(vector, dtype=np.float32)
        vector = vector / np.linalg.norm(vector)
        self._query_cache[query] = vector
        return vector

    def retrieve(
        self,
        query: str,
        document_id: str | None = None,
        match_count: int = TOP_N,
    ) -> list[dict]:
        """
        Drop-in replacement for retrieve_dense (same signature and row
        shape), restricted to this index's document.
        """

        if not query or not query.strip():
            return []

        vector = self.embed_query(query)

        started = time.perf_counter()
        similarities = self.matrix @ vector
        order = np.argsort(-similarities, kind="stable")[:match_count]
        self.rank_times.append(time.perf_counter() - started)

        return [
            {
                **self.chunks[position],
                "similarity": float(similarities[position]),
                "dense_rank": rank,
                "dense_score": float(similarities[position]),
            }
            for rank, position in enumerate(order, start=1)
        ]


# ============================================================
# BENCHMARK ONE MODEL
# ============================================================

def benchmark_model(
    model_name: str,
    production_api: bool,
    chunks: list[dict],
    dataset: list[dict],
    document_id: str,
) -> dict:

    started = time.perf_counter()

    index = InMemoryDenseIndex(model_name, chunks, production_api)

    queries = []

    for item in dataset:

        query = item["query"]
        relevant = item["relevant_chunks"]

        dense_rows = index.retrieve(query, document_id, TOP_N)
        dense_order = [row["chunk_index"] for row in dense_rows]

        with mock.patch.object(
            hybrid_retriever, "retrieve_dense", index.retrieve
        ):
            pipeline = _quiet(
                retrieve_context,
                query=query,
                document_id=document_id,
            )

        pipeline_order = [
            row.get("chunk_index") for row in pipeline["results"]
        ]

        queries.append(
            {
                "query": query,
                "relevant_chunks": relevant,
                "dense_order": dense_order,
                "pipeline_order": pipeline_order,
                "dense_metrics": _metrics(dense_order, relevant),
                "pipeline_metrics": _metrics(pipeline_order, relevant),
            }
        )

    total_seconds = time.perf_counter() - started

    return {
        "model": model_name,
        "api": "embed (production)" if production_api else "query_embed / passage_embed",
        "query_instruction": index.query_instruction,
        "dimensions": index.dimensions,
        "queries": queries,
        "dense_average": _average([q["dense_metrics"] for q in queries]),
        "pipeline_average": _average([q["pipeline_metrics"] for q in queries]),
        "timing": {
            "model_load_s": index.load_seconds,
            "chunk_embed_total_s": index.chunk_embed_seconds,
            "chunk_embed_per_chunk_ms": 1000 * index.chunk_embed_seconds / max(len(chunks), 1),
            "query_embed_avg_ms": 1000 * mean(index.query_times) if index.query_times else None,
            "query_embed_count": len(index.query_times),
            "rank_avg_ms": 1000 * mean(index.rank_times) if index.rank_times else None,
            "model_total_s": total_seconds,
        },
    }


# ============================================================
# BASELINE VALIDATION
# ============================================================

def validate_against_supabase(
    baseline: dict,
    dataset: list[dict],
    document_id: str,
) -> dict:
    """
    Compare the in-memory baseline dense order with the production
    Supabase dense order for every query.
    """

    identical = 0
    mismatches = []

    for item, result in zip(dataset, baseline["queries"]):
        production_order = [
            row["chunk_index"]
            for row in _quiet(
                retrieve_dense,
                query=item["query"],
                document_id=document_id,
                match_count=TOP_N,
            )
        ]
        if production_order == result["dense_order"]:
            identical += 1
        else:
            mismatches.append(
                {
                    "query": item["query"],
                    "supabase": production_order,
                    "in_memory": result["dense_order"],
                }
            )

    return {
        "identical_orders": identical,
        "queries": len(dataset),
        "mismatches": mismatches,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Offline embedding benchmark (diagnostic only)."
    )
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--models", nargs="*", default=DEFAULT_CANDIDATES)
    args = parser.parse_args()

    run_started = time.perf_counter()

    dataset = load_dataset(args.dataset)
    chunks = load_document_chunks(args.document_id)

    if not chunks:
        raise SystemExit(f"No chunks found for document {args.document_id}")

    lengths = [len(chunk.get("content") or "") for chunk in chunks]
    fragment_limit = FRAGMENT_RATIO * median(lengths)
    fragments = [
        chunk["chunk_index"]
        for chunk, length in zip(chunks, lengths)
        if length < fragment_limit
    ]

    categories = []
    for item in dataset:
        if item.get("category"):
            categories.append({"source": "dataset", "value": item["category"]})
        else:
            categories.append(
                {
                    "source": "production query_type",
                    "value": transform_query(item["query"])["query_type"],
                }
            )

    print(f"Chunks: {len(chunks)}  Queries: {len(dataset)}  Fragments: {fragments}")

    print(f"[baseline] {PRODUCTION_MODEL}", flush=True)
    baseline = benchmark_model(
        PRODUCTION_MODEL, True, chunks, dataset, args.document_id
    )
    validation = validate_against_supabase(
        baseline, dataset, args.document_id
    )

    results = [baseline]

    for model_name in args.models:
        if model_name == PRODUCTION_MODEL:
            continue
        print(f"[candidate] {model_name}", flush=True)
        results.append(
            benchmark_model(
                model_name, False, chunks, dataset, args.document_id
            )
        )

    output = {
        "document_id": args.document_id,
        "dataset": args.dataset,
        "chunk_count": len(chunks),
        "chunk_lengths": {
            chunk["chunk_index"]: length
            for chunk, length in zip(chunks, lengths)
        },
        "fragment_rule": f"length < {FRAGMENT_RATIO} x median chunk length ({fragment_limit:.0f} chars)",
        "fragments": fragments,
        "categories": categories,
        "baseline_validation": validation,
        "models": results,
        "total_benchmark_s": time.perf_counter() - run_started,
        "process_peak_memory_mb": peak_memory_mb(),
    }

    with open(args.output_json, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, default=str)

    print()
    print(
        f"Baseline dense order identical to Supabase for "
        f"{validation['identical_orders']}/{validation['queries']} queries"
    )
    for result in results:
        for stage in ("dense_average", "pipeline_average"):
            values = result[stage]
            print(
                f"{result['model']:<60} {stage[:-8]:<8} dim={result['dimensions']:<5}"
                + " ".join(
                    f"{k}={v:.3f}" for k, v in values.items()
                    if not k.startswith("hits@")
                )
            )
    print(f"Saved: {args.output_json}")


if __name__ == "__main__":
    main()
