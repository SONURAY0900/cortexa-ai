"""
Aggregate several embedding_benchmark.py result files (one per
document) into a cross-document comparison.

DIAGNOSTIC ONLY. Reads JSON files written by
rag.evaluation.embedding_benchmark; never touches the database.

Every file must contain the same models. The first model in each file
(the production baseline) is the reference for all deltas. Nothing here
depends on a particular document, dataset, model or category name.

Usage:

    python -m rag.evaluation.aggregate_embedding_benchmarks \
        results_doc_a.json results_doc_b.json ... \
        [--labels A B ...] [--output-json summary.json]
"""

import argparse
import json
from statistics import mean

from rag.evaluation.dataset import load_dataset


METRICS = ["recall@3", "recall@5", "recall@10", "precision@5", "mrr"]
STAGES = ["dense", "pipeline"]
EPS = 1e-9


def _load(paths: list[str], labels: list[str] | None) -> list[dict]:
    runs = []
    for index, path in enumerate(paths):
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        data["label"] = labels[index] if labels else data["document_id"]
        runs.append(data)
    names = [[m["model"] for m in run["models"]] for run in runs]
    if any(n != names[0] for n in names):
        raise SystemExit("All result files must contain the same models in the same order.")
    return runs


def _compare(base: dict, cand: dict) -> str:
    """Per-query verdict: R@5 first, MRR as tie-break."""
    d5 = cand["recall@5"] - base["recall@5"]
    dm = cand["mrr"] - base["mrr"]
    if d5 > EPS or (abs(d5) <= EPS and dm > EPS):
        return "improved"
    if d5 < -EPS or (abs(d5) <= EPS and dm < -EPS):
        return "worsened"
    return "unchanged"


def summarize(runs: list[dict], group_field: str | None = None) -> dict:

    model_names = [m["model"] for m in runs[0]["models"]]
    summary = {"models": model_names, "documents": [], "overall": {}, "categories": {}}

    # ---------------- per document ----------------
    for run in runs:
        doc = {
            "label": run["label"],
            "document_id": run["document_id"],
            "dataset": run["dataset"],
            "chunks": run["chunk_count"],
            "queries": len(run["models"][0]["queries"]),
            "relevant_labels": sum(len(q["relevant_chunks"]) for q in run["models"][0]["queries"]),
            "fragments": run["fragments"],
            "baseline_validation": f"{run['baseline_validation']['identical_orders']}/{run['baseline_validation']['queries']}",
            "models": {},
        }
        base = run["models"][0]
        fragments = set(run["fragments"])
        for model in run["models"]:
            entry = {
                stage: {k: model[f"{stage}_average"][k] for k in METRICS}
                for stage in STAGES
            }
            entry["timing"] = model["timing"]
            entry["fragment_top5"] = {}
            for stage in STAGES:
                total = non_relevant = 0
                for q in model["queries"]:
                    for c in q[f"{stage}_order"][:5]:
                        if c in fragments:
                            total += 1
                            non_relevant += c not in q["relevant_chunks"]
                entry["fragment_top5"][stage] = {"total": total, "non_relevant": non_relevant}
            if model is not base:
                entry["per_query"] = {}
                for stage in STAGES:
                    verdicts = [
                        _compare(qb[f"{stage}_metrics"], qc[f"{stage}_metrics"])
                        for qb, qc in zip(base["queries"], model["queries"])
                    ]
                    entry["per_query"][stage] = {
                        v: verdicts.count(v) for v in ("improved", "worsened", "unchanged")
                    }
            doc["models"][model["model"]] = entry
        summary["documents"].append(doc)

    # ---------------- overall ----------------
    for stage in STAGES:
        summary["overall"][stage] = {}
        for index, name in enumerate(model_names):
            per_doc = [run["models"][index][f"{stage}_average"] for run in runs]
            all_queries = [
                q[f"{stage}_metrics"] for run in runs for q in run["models"][index]["queries"]
            ]
            summary["overall"][stage][name] = {
                "macro (mean of documents)": {k: mean(d[k] for d in per_doc) for k in METRICS},
                "micro (mean of all queries)": {k: mean(q[k] for q in all_queries) for k in METRICS},
            }

    # ---------------- groups ----------------
    summary["categories"] = _group(runs, model_names, lambda run: [c["value"] for c in run["categories"]])
    if group_field:
        summary[f"by_{group_field}"] = _group(
            runs,
            model_names,
            lambda run: [
                str(item.get(group_field, "unspecified"))
                for item in load_dataset(run["dataset"])
            ],
        )

    return summary


def _group(runs: list[dict], model_names: list[str], labels_for) -> dict:
    """
    Pool per-query metrics across documents by a per-query label.
    """

    grouped = {}
    for stage in STAGES:
        cats: dict[str, dict[str, list]] = {}
        for run in runs:
            labels = labels_for(run)
            for index, name in enumerate(model_names):
                for label, q in zip(labels, run["models"][index]["queries"]):
                    cats.setdefault(label, {}).setdefault(name, []).append(q[f"{stage}_metrics"])
        grouped[stage] = {
            label: {
                name: {"n": len(rows), **{k: mean(r[k] for r in rows) for k in METRICS}}
                for name, rows in by_model.items()
            }
            for label, by_model in sorted(cats.items())
        }

    return grouped


def _print_groups(title: str, groups: dict, names: list[str], short: dict) -> None:
    print(f"  {title}:")
    for label, by_model in groups.items():
        for n in names:
            v = by_model[n]
            print(f"    {label[:16]:<17}n={v['n']:<4}{short[n]:<30}" + "".join(f"{v[k]:>8.3f}" for k in METRICS))


def print_summary(summary: dict) -> None:
    names = summary["models"]
    short = {n: n.split("/")[-1][:28] for n in names}
    for stage in STAGES:
        print(f"\n===== {stage.upper()} =====")
        print(f"{'Document':<18}{'Model':<30}" + "".join(f"{k:>10}" for k in METRICS) + f"{'total_s':>9}")
        for doc in summary["documents"]:
            for n in names:
                m = doc["models"][n]
                print(f"{doc['label'][:17]:<18}{short[n]:<30}"
                      + "".join(f"{m[stage][k]:>10.3f}" for k in METRICS)
                      + f"{m['timing']['model_total_s']:>9.1f}")
            for n in names[1:]:
                b, c = doc["models"][names[0]][stage], doc["models"][n][stage]
                print(f"{'':<18}{'delta ' + short[n][:22]:<30}" + "".join(f"{c[k] - b[k]:>+10.3f}" for k in METRICS)
                      + f"   per-query {doc['models'][n]['per_query'][stage]}")
        for agg in ("macro (mean of documents)", "micro (mean of all queries)"):
            for n in names:
                v = summary["overall"][stage][n][agg]
                print(f"{'ALL ' + agg[:5]:<18}{short[n]:<30}" + "".join(f"{v[k]:>10.3f}" for k in METRICS))
        _print_groups("categories", summary["categories"][stage], names, short)
        for key in summary:
            if key.startswith("by_"):
                _print_groups(key, summary[key][stage], names, short)


def main():
    parser = argparse.ArgumentParser(description="Aggregate embedding benchmark results across documents.")
    parser.add_argument("results", nargs="+")
    parser.add_argument("--labels", nargs="*")
    parser.add_argument("--output-json")
    parser.add_argument(
        "--group-field",
        help="Optional per-query dataset field to group by as well (e.g. a language field).",
    )
    args = parser.parse_args()
    if args.labels and len(args.labels) != len(args.results):
        raise SystemExit("--labels must match the number of result files")
    summary = summarize(_load(args.results, args.labels), args.group_field)
    print_summary(summary)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as file:
            json.dump(summary, file, indent=2)


if __name__ == "__main__":
    main()
