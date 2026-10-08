# PHASE 14 REPORT

```
PHASE 14 STATUS:            COMPLETED
TRACK A:                    COMPLETED
TRACK B:                    COMPLETED
PRODUCTION EMBEDDING:       sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING MIGRATION:        NOT PERFORMED
PRODUCTION VECTOR CHANGES:  NONE
DATABASE SCHEMA CHANGES:    NONE
SQL FUNCTION CHANGES:       NONE
WINDOWS SECURITY CHANGES:   NONE
EMBEDDING DECISION:         C. MIGRATION CANDIDATE IDENTIFIED — intfloat/multilingual-e5-small (conditional, see §18)
DF30 KEYWORD FILTER:        PROMISING — DEFERRED (offline only)
```

---

## 1. Executive summary

- **Track A** (cleaning, context builder, query transformer, df30) was completed earlier and its decisions stand: resume-specific cleaning rules removed (owner-approved), context-builder Unicode change reverted, query transformer unchanged, df30 offline only.
- **Track B** benchmarked the production embedding model against `multilingual-e5-small`, `-base` and `-large`. All 6 datasets × 4 models = 24 runs completed and validated. Every candidate was scored on the same chunks, queries and labels, through the same code; only the embedding model changed.
- **Odia is embedding-bound.** With the current model, dense search finds a relevant chunk in its top 20 for only 1 of 18 Odia queries (pool recall 0.009). Every E5 model fixes most of this: pool recall is 0.47–0.76.
- Full pipeline, against the current model:
  - Odia R@5 rises from 0.111 to 0.280 (e5-small), 0.226 (e5-base) or 0.370 (e5-large).
  - Hindi R@5 rises from 0.755 to 0.784–0.824.
  - English is flat to slightly better on R@5, but e5-small and e5-base lose about 0.04 MRR.
- **e5-large** has the best quality on every language. It is impractical for this deployment: 1024 dimensions (schema migration), about 13× slower document embedding and about 7× slower query embedding than the current model.
- **e5-small** keeps the current 384 dimensions (no schema change). It has the best Hindi MRR, a 2.5× better Odia R@5, and near-current latency. Its weak points are an English MRR loss (concentrated in one document) and much higher peak memory at FastEmbed's default batch size (§14). Both are fixable conditions for the migration phase.
- **Decision: C (migration candidate identified): `intfloat/multilingual-e5-small`.** Production was **not** migrated.

> Results are evidence for these evaluation datasets, not a universal claim about language coverage. There is one Hindi document (17 queries) and one Odia document (18 queries); the English set is four documents with 17–20 queries each.

## 2. Phase 14 scope

- **Track A:** remove document-specific logic, test a context-builder tokenizer change, measure the query transformer, and test a document-frequency keyword filter offline.
- **Track B:** an offline benchmark of three multilingual E5 embedding models against production, on all six evaluation datasets. It covers dense retrieval and the full pipeline, a language breakdown, candidate-pool analysis, resources and a decision.
- **Out of scope:** migrating embeddings, changing VECTOR(384), SQL functions or the schema, re-ingesting production documents, or changing query transformation or reranking.

## 3. Repository / environment state

- The project is now a git repository. Phase 14 Track B made no change to production code.
- One evaluation-only fix was made: `embedding_benchmark.peak_memory_mb()` returned 0 on Windows (§14).
- **Windows Application Control** blocks the native `mmh3.pyd` that `fastembed` imports, so the benchmark ran under a benchmark-only stub (§6). Windows security was not changed.
- The benchmark documents are the accepted Phase 12/13 baselines:

  | Dataset | Document | Labels | Chunks | Queries |
  |---|---|---|---:|---:|
  | Resume | `368779b6…` | `my_pdf.json` | 12 | 20 |
  | Flask | `dabd5826…` | `phase12_flask_intro.json` | 30 | 17 |
  | Entrepreneurship | `ce263d57…` | `phase12_entrepreneurship_notes.json` | 15 | 18 |
  | Attention | `e9dbe3f6…` | `phase12_attention_paper.json` | 58 | 20 |
  | Hindi | `116c335a…` | `phase13_hindi_textbook_rechunked.json` | 12 | 17 |
  | Odia | `7f1ef0ae…` | `phase13_odia_book_rechunked.json` | 464 | 18 |

## 4. Track A results (unchanged from the Track A run)

### A. Document-specific cleaning — REMOVED

There were 12 hardcoded replacements in `text_cleaner._repair_split_words`:
- 8 fire only on the resume ("injec tion", "preve nting", …).
- 1 is a no-op ("agent pipeline" → "agent pipeline").
- 3 are arbitrary rewrites ("real time", "end to end", "micro service") that fired on none of the 6 evaluation PDFs or 6 other English PDFs.

They are document-specific, so the step was removed. No dictionary or other rule replaces it.

**The owner approved keeping this removal on 2026-10-08.** Existing stored chunks are not re-cleaned; only new uploads are affected.

**Resume impact** (re-ingested eval copies, labels transferred by overlap):

| Version | R@5 | R@10 | P@5 | MRR |
|---|---|---|---|---|
| With rules | 0.792 | 0.900 | 0.500 | 0.839 |
| Without rules | 0.742 | 0.850 | 0.490 | 0.863 |

R@5 drops because the resume's split words stay split. Part of the old score came from fixes written for this exact document. The other 5 PDFs are unaffected, since the rules never fired on them.

### B. Context builder Unicode tokens — REVERTED (no benefit)

- **English:** identical on every metric.
- **Hindi:** context recall went down, 0.833 → 0.814.
- **Odia:** unchanged.

The change was reverted. The optional `extra_chars` parameter in `text_tokens.unicode_tokens` is unused and harmless.

### C. Query transformer — LEAVE UNCHANGED

| Dataset | No transform → current |
|---|---|
| Resume | R@5 0.700 → 0.739 |
| Flask | R@5 0.656 → 0.681, MRR 0.598 → 0.647 |
| Attention | MRR 0.772 → 0.860 |
| Entrepreneurship, Hindi, Odia | neutral |

**Finding (open):** every native-script and mixed Hindi/Odia query is classified "specific", because the list/broad patterns are English. Specific queries return only 5 results, which caps R@10 at R@5 for those languages.

### D. Document-frequency keyword filter (offline experiment only)

| Dataset | df30 | df50 | df70 |
|---|---|---|---|
| Flask | R@5 0.681 → **0.858**, MRR 0.647 → 0.706 | no change | no change |
| Attention | R@5 0.626 → 0.651, MRR 0.860 → 0.879 | **worse** (R@5 0.601) | no change |
| Hindi | R@3 0.686 → 0.716, MRR 0.618 → **0.721** | same as df30 | R@3 0.716, MRR 0.652 |
| Entrepreneurship | unchanged | unchanged | unchanged |
| Odia | metrics unchanged (pool recall 0.148 → 0.170) | unchanged | unchanged |
| Resume | R@5 0.739 → 0.727 (one query: "databases") | not completed | not completed |

## 5. Track B methodology

The tool is `rag/evaluation/embedding_benchmark.py`, unchanged except for the memory fix in §14. For each document it:

1. Reads the document's existing chunks once (read-only), in chunk order.
2. For each model, embeds those **same chunks** in memory and ranks them by cosine similarity for each query. Dense = top 20 (`TOP_N = 20`).
3. Runs the **unchanged production pipeline** (`retrieve_context`) with only its dense step answered from that in-memory index. Query transformation, keyword retrieval, RRF, reranking, diversity selection and limits are all production code (a temporary in-process patch).
4. Scores both rankings with the project's `metrics.py`: R@3, R@5, R@10, P@5, MRR.

**E5 prefixes:** documents are encoded as `passage: <text>` and queries as `query: <text>`, as the model cards require. FastEmbed adds no prefix itself (checked in its source), so there is no double prefix. No language-specific prefixes or instructions were used. The production model uses its production API (`embed()`, no prefix).

**One process per (document, model):** each process runs the production baseline plus at most one candidate.

**Fairness checks (all passed):**

| Check | Result |
|---|---|
| Same chunks | Loaded once per process from the same document; chunk counts equal across all 4 runs of each document |
| Same queries and labels | Every run's queries and `relevant_chunks` match the dataset file exactly (asserted) |
| Same top-k, scoring, evaluation code | One code path; `TOP_N = 20`; `metrics.py` |
| Same preprocessing | Chunk text as stored; only the model-card prefix differs between model families |
| Baseline = production | The in-memory baseline's dense order is identical to Supabase's `match_document_chunks` for **110/110** queries |
| Baseline stable | The baseline metrics are identical in all 4 processes of every document |
| Accepted baselines reproduced | Resume pipeline R@3/R@5/R@10/P@5/MRR = 0.558/0.739/0.843/0.490/0.863 (exact). Flask R@5 0.681, MRR 0.647 (exact). Hindi R@5 0.755, MRR 0.618 (exact). Odia R@5 0.111, MRR 0.096 (exact) |
| Failures / NaN | None. 24/24 result files present, no duplicates, no failed queries |

## 6. Benchmark safety — mmh3 stub

**Track B was executed using a process-local, benchmark-only mmh3 stub.**

- Windows Application Control blocks `mmh3.pyd`. `fastembed` imports `mmh3` when the package loads, but only its sparse models (BM25, BM42 and the sparse-vector converter) call it.
- The scratchpad launcher registers an **empty** in-memory module named `mmh3` in `sys.modules` before importing the benchmark. Any attribute access on it is logged and raises an error, so no code can receive fake output.
- **No project source file was modified for the stub.** No site-packages were modified, no `mmh3.py` file was created anywhere, and no Windows security policy was changed.
- The stub only prevents loading the blocked native DLL.
- **Verified paths:**
  - FastEmbed import.
  - `TextEmbedding` load, `embed`, `passage_embed` and `query_embed` for all four models.
  - The production pipeline (`retrieve_context`: query transformation, keyword RPC, hybrid/RRF, reranking, diversity selection, context building).
  - The Supabase dense-order validation.
- The stub was accessed 0 times in the import test, the one-chunk smoke test and all 24 runs.
- This is **not a production workaround.** Production (Linux on Render) loads the real `mmh3`.

## 7. Models tested

| Model | Dim | Max tokens | License | Notes |
|---|---:|---:|---|---|
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (production) | 384 | **128** | Apache-2.0 | Production API, no prefixes |
| intfloat/multilingual-e5-small | 384 | 512 | MIT | `query:` / `passage:` |
| intfloat/multilingual-e5-base | 768 | 512 | MIT | `query:` / `passage:` |
| intfloat/multilingual-e5-large | 1024 | 512 | MIT | `query:` / `passage:` |

**Finding:** the production tokenizer truncates every chunk to **128 tokens** (`truncation.max_length = 128`). Odia text uses many tokens per word, so the current model embeds only the beginning of each Odia chunk. E5 reads up to 512 tokens. This is the most likely reason for the size of the Odia gap; it is an inference consistent with the data, not separately ablated.

## 8. Dense retrieval results (only the embedding changes)

| Dataset | Model | R@3 | R@5 | R@10 | P@5 | MRR |
|---|---|---:|---:|---:|---:|---:|
| Resume | current | 0.488 | 0.671 | 0.969 | 0.430 | 0.778 |
| | e5-small | 0.489 | 0.709 | 0.975 | 0.470 | 0.743 |
| | e5-base | 0.414 | 0.585 | 0.949 | 0.430 | 0.704 |
| | e5-large | 0.570 | 0.692 | 0.961 | 0.410 | 0.733 |
| Flask | current | 0.450 | 0.509 | 0.605 | 0.188 | 0.398 |
| | e5-small | 0.559 | 0.644 | 0.808 | 0.212 | 0.574 |
| | e5-base | 0.644 | 0.741 | 0.831 | 0.235 | 0.723 |
| | e5-large | 0.726 | 0.820 | 0.874 | 0.282 | 0.897 |
| Entrepreneurship | current | 0.750 | 0.903 | 1.000 | 0.256 | 0.707 |
| | e5-small | 0.903 | 0.931 | 0.986 | 0.244 | 0.958 |
| | e5-base | 0.903 | 0.944 | 1.000 | 0.256 | 0.903 |
| | e5-large | 0.917 | 0.944 | 0.972 | 0.256 | 0.935 |
| Attention | current | 0.487 | 0.599 | 0.741 | 0.290 | 0.769 |
| | e5-small | 0.531 | 0.643 | 0.822 | 0.320 | 0.794 |
| | e5-base | 0.591 | 0.670 | 0.793 | 0.330 | 0.804 |
| | e5-large | 0.547 | 0.638 | 0.750 | 0.320 | 0.832 |
| Hindi | current | 0.608 | 0.686 | 0.980 | 0.212 | 0.671 |
| | e5-small | 0.833 | 0.853 | 1.000 | 0.247 | 0.843 |
| | e5-base | 0.775 | 0.833 | 0.980 | 0.247 | 0.812 |
| | e5-large | 0.784 | 0.824 | 0.980 | 0.247 | 0.772 |
| Odia | current | 0.009 | 0.009 | 0.009 | 0.011 | 0.019 |
| | e5-small | 0.257 | 0.343 | 0.445 | 0.200 | 0.435 |
| | e5-base | 0.154 | 0.248 | 0.382 | 0.178 | 0.367 |
| | e5-large | 0.346 | 0.478 | 0.590 | 0.333 | 0.573 |

**Summary (dense).** English is the macro average of the 4 English documents; Hindi and Odia are their single documents; Macro is the plain mean of all 6 documents (each document weighs the same, whatever its query count).

| Model | English R@5 | Hindi R@5 | Odia R@5 | Macro R@5 | Macro MRR | Peak memory* | Dim |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 0.670 | 0.686 | 0.009 | 0.563 | 0.557 | 2.3 GB | 384 |
| e5-small | 0.732 | 0.853 | 0.343 | 0.687 | 0.725 | 6.9 GB | 384 |
| e5-base | 0.735 | 0.833 | 0.248 | 0.670 | 0.719 | 8.8 GB | 768 |
| e5-large | 0.774 | 0.824 | 0.478 | 0.733 | 0.790 | 10.1 GB† | 1024 |

\*Peak process memory while embedding one full FastEmbed batch (256) of the largest document's chunks; see §14.

## 9. Full-pipeline results (production pipeline; only dense retrieval changes)

| Dataset | Model | R@3 | R@5 | R@10 | P@5 | MRR |
|---|---|---:|---:|---:|---:|---:|
| Resume | current | 0.558 | 0.739 | 0.843 | 0.490 | 0.863 |
| | e5-small | 0.558 | 0.747 | 0.848 | 0.500 | 0.852 |
| | e5-base | 0.563 | 0.745 | 0.853 | 0.500 | 0.825 |
| | e5-large | 0.602 | 0.772 | 0.859 | 0.490 | 0.879 |
| Flask | current | 0.623 | 0.681 | 0.681 | 0.271 | 0.647 |
| | e5-small | 0.611 | 0.699 | 0.728 | 0.247 | 0.634 |
| | e5-base | 0.729 | 0.758 | 0.758 | 0.271 | 0.688 |
| | e5-large | 0.770 | 0.816 | 0.846 | 0.282 | 0.843 |
| Entrepreneurship | current | 0.903 | 0.944 | 0.972 | 0.256 | 0.944 |
| | e5-small | 0.889 | 0.917 | 0.958 | 0.233 | 0.931 |
| | e5-base | 0.889 | 0.931 | 0.972 | 0.244 | 0.875 |
| | e5-large | 0.889 | 0.917 | 0.958 | 0.233 | 0.903 |
| Attention | current | 0.553 | 0.626 | 0.626 | 0.290 | 0.860 |
| | e5-small | 0.512 | 0.609 | 0.609 | 0.300 | 0.738 |
| | e5-base | 0.553 | 0.677 | 0.677 | 0.320 | 0.754 |
| | e5-large | 0.538 | 0.644 | 0.644 | 0.320 | 0.752 |
| Hindi | current | 0.686 | 0.755 | 0.755 | 0.235 | 0.618 |
| | e5-small | 0.706 | 0.814 | 0.814 | 0.247 | 0.750 |
| | e5-base | 0.647 | 0.784 | 0.784 | 0.247 | 0.721 |
| | e5-large | 0.706 | 0.824 | 0.824 | 0.259 | 0.683 |
| Odia | current | 0.065 | 0.111 | 0.111 | 0.044 | 0.096 |
| | e5-small | 0.220 | 0.280 | 0.280 | 0.167 | 0.298 |
| | e5-base | 0.194 | 0.226 | 0.244 | 0.133 | 0.315 |
| | e5-large | 0.311 | 0.370 | 0.370 | 0.244 | 0.472 |

**Summary and deltas (pipeline, candidate − current):**

| Model | English R@5 | Hindi R@5 | Odia R@5 | Macro R@5 | Macro MRR | English MRR | Hindi MRR | Odia MRR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current | 0.748 | 0.755 | 0.111 | 0.643 | 0.671 | 0.829 | 0.618 | 0.096 |
| e5-small | 0.743 (−0.005) | 0.814 (+0.059) | 0.280 (+0.169) | 0.678 (+0.035) | 0.700 (+0.029) | 0.789 (−0.040) | 0.750 (+0.132) | 0.298 (+0.202) |
| e5-base | 0.778 (+0.030) | 0.784 (+0.029) | 0.226 (+0.115) | 0.687 (+0.044) | 0.696 (+0.025) | 0.786 (−0.043) | 0.721 (+0.103) | 0.315 (+0.219) |
| e5-large | 0.787 (+0.039) | 0.824 (+0.069) | 0.370 (+0.259) | 0.724 (+0.081) | 0.755 (+0.084) | 0.844 (+0.015) | 0.683 (+0.065) | 0.472 (+0.376) |

**Does the embedding gain survive the pipeline?** Partly.

- **Odia and Hindi:** most of the gain survives.
- **English:**
  - The clear dense gains (+0.06 to +0.10 R@5) mostly disappear in the pipeline. Keyword retrieval, RRF and the reranker already recover much of what MiniLM misses in English.
  - For e5-small and e5-base, the pipeline loses MRR on Attention (0.860 → 0.738 / 0.754). Their dense MRR on Attention actually *rises* (0.769 → 0.794 / 0.804).
  - So this regression comes from how the new dense scores combine with the existing fusion and reranking (which were tuned while MiniLM was the dense model), not from worse embeddings.

## 10. English analysis

| Dataset | R@5 current → small / base / large | MRR current → small / base / large |
|---|---|---|
| Resume | 0.739 → 0.747 / 0.745 / 0.772 | 0.863 → 0.852 / 0.825 / 0.879 |
| Flask | 0.681 → 0.699 / 0.758 / 0.816 | 0.647 → 0.634 / 0.688 / 0.843 |
| Entrepreneurship | 0.944 → 0.917 / 0.931 / 0.917 | 0.944 → 0.931 / 0.875 / 0.903 |
| Attention | 0.626 → 0.609 / 0.677 / 0.644 | 0.860 → 0.738 / 0.754 / 0.752 |
| **English macro** | **0.748 → 0.743 / 0.778 / 0.787** | **0.829 → 0.789 / 0.786 / 0.844** |

- **Regressions:**
  - Attention MRR for every candidate (−0.11 to −0.12).
  - Entrepreneurship R@5 and MRR (small) for every candidate.
  - e5-small: per-query pipeline results across the four English documents are 12 improved, 13 regressed, 50 unchanged, i.e. neutral overall.
- **Gains:** Flask improves strongly with base and large. Resume improves slightly with large.

## 11. Hindi analysis (current pipeline baseline R@5 0.755, MRR 0.618)

| Queries (n) | current | e5-small | e5-base | e5-large |
|---|---|---|---|---|
| Native Hindi (7), R@5 / MRR | 0.714 / 0.607 | 0.714 / 0.714 | 0.714 / 0.786 | 0.714 / 0.679 |
| English (7) | 0.690 / 0.607 | 0.833 / 0.857 | 0.762 / 0.714 | 0.857 / 0.719 |
| Mixed (3) | 1.000 / 0.667 | 1.000 / 0.583 | 1.000 / 0.583 | 1.000 / 0.611 |
| **All (17)** | **0.755 / 0.618** | **0.814 / 0.750** | **0.784 / 0.721** | **0.824 / 0.683** |

- **No candidate damages Hindi.** All improve R@5 and MRR. e5-small has the best Hindi MRR.
- The relevant chunks are always in the dense top 20 (pool recall 1.000 for every model), so Hindi is a ranking problem, not a coverage one.
- The 3 mixed queries lose a little MRR. That is one rank position on very few queries.

## 12. Odia analysis (current pipeline baseline R@5 0.111, MRR 0.096)

| Model | R@3 | R@5 | R@10 | P@5 | MRR |
|---|---:|---:|---:|---:|---:|
| current | 0.065 | 0.111 | 0.111 | 0.044 | 0.096 |
| e5-small | 0.220 | 0.280 | 0.280 | 0.167 | 0.298 |
| e5-base | 0.194 | 0.226 | 0.244 | 0.133 | 0.315 |
| e5-large | 0.311 | 0.370 | 0.370 | 0.244 | 0.472 |

| Queries (n), pipeline R@5 / MRR | current | e5-small | e5-base | e5-large |
|---|---|---|---|---|
| Native Odia (6) | 0.139 / 0.067 | 0.372 / 0.339 | 0.250 / 0.333 | 0.372 / 0.444 |
| English about the Odia book (9) | 0.019 / 0.037 | 0.200 / 0.259 | 0.174 / 0.296 | 0.381 / 0.537 |
| Mixed (3) | 0.333 / 0.333 | 0.333 / 0.333 | 0.333 / 0.333 | 0.333 / 0.333 |

**Is the embedding the bottleneck?** Yes. Dense top-20 analysis, per query:

| Model | Pool recall@20 | Case A: no relevant chunk in dense top 20 | Case B: in top 20 but none in top 5 | Relevant in top 5 |
|---|---:|---:|---:|---:|
| current | 0.009 | **17 / 18** | 0 | 1 |
| e5-small | 0.527 | 5 | 4 | 9 |
| e5-base | 0.473 | 4 | 4 | 10 |
| e5-large | 0.761 | 0 | 4 | 14 |

- **The current model:** the Odia failures are almost entirely **Case A**: dense search never retrieves the relevant chunks. The baseline's 0.111 pipeline R@5 comes from keyword retrieval.
- **With E5:** most queries move into the pool. What remains is a mix of Case A and Case B for e5-small, and only Case B for e5-large. Case B is a ranking problem the pipeline can work on.
- **Per query:** e5-large improves 11 of 18 Odia queries in the pipeline (18/18 dense) and regresses none. e5-small improves 7 and regresses 1.
- **Not noise:** the Odia gain is consistent across R@3, R@5, R@10, P@5, MRR and pool recall, for native, English and dense views.
- **Still a hard case:** absolute Odia quality stays low (best R@5 0.370) because chunks are long and the 464-chunk book is large.

## 13. Model-by-model comparison and ranking

| Rank | Model | Strengths | Weaknesses |
|---|---|---|---|
| Quality 1 | **e5-large** | Best Odia (pipeline R@5 0.370, MRR 0.472, pool 0.761); best macro R@5 and MRR; English macro improves on both measures | 1024-dim → schema and SQL migration plus full re-embedding; ~13× slower document embedding; ~7× slower query embedding (~0.7 s per query variant on this CPU); highest memory |
| Practical 1 | **e5-small** | Same 384 dims (no schema/SQL change); best Hindi MRR; Odia R@5 2.5×, MRR 3.1×, pool 0.009 → 0.527; query latency close to production | English pipeline MRR −0.040 (Attention); high peak memory at batch 256 (fixable, §14) |
| 3 | e5-base | Better English R@5 than small | 768-dim (schema migration) without beating small on Hindi; weakest Odia R@5 of the E5s; ~2.6× slower queries |

## 14. Latency and memory

Times are means over the six benchmark runs on this laptop CPU (no GPU). They include contention from other processes and are indicative only.

| Model | Dim | Load (s) | Document embedding (ms per chunk) | Query embedding (ms) | Total benchmark time, 6 docs (s) | Peak memory* |
|---|---:|---:|---:|---:|---:|---:|
| current | 384 | 4.6 | 163 | 96 | 266 | 2.3 GB |
| e5-small | 384 | 4.9 | 230 | 117 | 294 | 6.9 GB |
| e5-base | 768 | 7.4 | 640 | 248 | 737 | 8.8 GB |
| e5-large | 1024 | 10.4 | 2,166 | 699 | 1,938 | 10.1 GB† |

**Peak memory.** The original runs recorded 0 MB. `peak_memory_mb()` called `GetProcessMemoryInfo` without ctypes type declarations, so the process pseudo-handle was truncated and the call failed (error 6, invalid handle). That bug is fixed in `embedding_benchmark.py`; it is the only change to that file.

Rather than re-running the 24 quality runs (which were valid), peak memory was re-measured in a separate process per model, on the same code path:
- production imports;
- load the model;
- embed one full FastEmbed batch (256) of the Odia document's chunks with the model's prefixes;
- embed the 18 Odia queries.

| Model | Peak memory, batch 256 (FastEmbed default) | Peak memory, batch 32 | Time to embed 256 Odia chunks, batch 256 → 32 |
|---|---:|---:|---:|
| current | 2.3 GB | 1.5 GB | 7.6 s → 7.7 s |
| e5-small | 6.9 GB | 2.1 GB | 26.8 s → 20.0 s |
| e5-base | 8.8 GB | not measured | 73.2 s |
| e5-large | 10.1 GB† | 3.6 GB | 257 s → 179 s |

Every process also holds the production model, which the pipeline imports, plus the Python and Supabase runtime: about 0.7 GB before the model under test is loaded. †This machine has 15.6 GB of RAM, and free memory was near zero during the e5-large probe, so its batch-256 figure may be capped by the available RAM.

**Batch size is the dominant memory factor.** At batch 32, e5-small needs about the same memory as the current model does at its default batch, and it is not slower.

**Deployment implications (inference; Render limits were not measured):**
- At FastEmbed's default batch of 256, peak memory is driven by sequence length.
  - MiniLM truncates at 128 tokens; E5 reads 512, so its attention activations are much larger.
  - Production ingestion uses the same default batch size, so even the **current** model peaks around 2.3 GB on long chunks.
  - Setting an explicit, smaller `batch_size` in `generate_embeddings` matters for any small instance, whichever model is chosen.
- **e5-large:** query embedding (~0.7 s per query variant, and the pipeline embeds several variants per question) and ingestion (~2 s per chunk; ~22 min for the 464-chunk Odia book here) make it unsuitable for a CPU-only small instance.
- **e5-small:** latency is close to production; memory is controllable with batch size.

## 15. Error analysis (pipeline, per query, R@5 first and MRR as tie-break)

**e5-large vs current** (strongest candidate):

| Group | Improved | Regressed | Unchanged |
|---|---:|---:|---:|
| Odia (all) | 11 | 0 | 7 |
| — native Odia / English / mixed | 4 / 7 / 0 | 0 / 0 / 0 | 2 / 2 / 3 |
| Hindi (all) | 4 | 3 | 10 |
| — native Hindi / English / mixed | 1 / 3 / 0 | 0 / 2 / 1 | 6 / 2 / 2 |
| Resume | 3 | 2 | 15 |
| Flask | 5 | 2 | 10 |
| Entrepreneurship | 1 | 2 | 15 |
| Attention | 5 | 4 | 11 |
| Category: section | 5 | 0 | 11 |
| Category: specific | 8 | 2 | 19 |
| Category: technical | 7 | 3 | 9 |
| Category: list | 5 | 3 | 11 |
| Category: broad | 1 | 3 | 3 |

- **Main pattern:** the clearest regressions are **broad "What is this … about?" questions** (Flask, Entrepreneurship, Attention). For these, MiniLM's 128-token, beginning-of-chunk embeddings happen to favour introductory chunks.
- **Other regressions:**
  - Attention "Who are the authors of the paper?" falls from R@5 1.0 to 0.0, because the author block competes with long passages.
  - Two resume list questions ("frameworks and libraries", "databases").
- **Hindi:** the regressions are small MRR moves (one rank position) on list and mixed queries. The Hindi R@5 total still improves.

**e5-small vs current**, per document (improved / regressed / unchanged):

| Document | Improved | Regressed | Unchanged |
|---|---:|---:|---:|
| Resume | 4 | 2 | 14 |
| Flask | 3 | 5 | 9 |
| Entrepreneurship | 2 | 2 | 14 |
| Attention | 3 | 4 | 13 |
| Hindi | 5 | 1 | 11 |
| Odia | 7 | 1 | 10 |

The full per-query listings are in the session scratchpad (`tb/perquery_e5large.txt`).

## 16. Common-word df30 finding

**PROMISING — DEFERRED.**
- df30 improved Flask substantially (R@5 0.681 → 0.858) and improved Hindi (MRR 0.618 → 0.721).
- It slightly lowered the resume (R@5 0.739 → 0.727, one query) and left Odia metrics mostly unchanged.
- It is not implemented in production, and no SQL was changed.
- A future controlled experiment should evaluate it together with whichever embedding is chosen, because both change the hybrid ranking.

## 17. Combined Phase 14 findings

1. Production RAG code is now free of document-specific cleaning rules (Track A).
2. Odia retrieval is limited first by the **embedding** (Case A failures; 128-token truncation), not by reranking or context building. Fixing the embedding is a precondition for any Odia work.
3. Hindi is already well covered by the pool; better embeddings improve its ranking.
4. In English the pipeline masks most embedding differences. The remaining English regressions with E5 are concentrated in broad "about" questions and author/list queries. The fusion and reranking settings were tuned against MiniLM and will need re-checking after any migration.
5. Non-English queries are always classified "specific" (Track A, C), which caps their R@10. That is a separate, language-independent fix candidate.
6. Default FastEmbed batch sizes can use several GB of RAM on long chunks, even with the current model.

## 18. Embedding decision

**C. MIGRATION CANDIDATE IDENTIFIED: `intfloat/multilingual-e5-small`.**

Why e5-small meets the criteria:
- **Meaningful Odia improvement:** pipeline R@5 0.111 → 0.280, MRR 0.096 → 0.298, P@5 0.044 → 0.167, dense pool recall 0.009 → 0.527. The gain is consistent across measures and across native and English Odia queries.
- **No Hindi regression:** R@5 +0.059, MRR +0.132 (the best of all models).
- **English is the conditional point:**
  - R@5 is flat (−0.005) and the per-query result is neutral (12 improved / 13 regressed).
  - MRR is −0.040, almost entirely from Attention (0.860 → 0.738).
  - This is acceptable for a candidate, but **must be recovered before migrating** (see §20).
- **Latency:** query embedding 117 ms vs 96 ms; document embedding about 1.4× the current model.
- **Resources:**
  - Same 384 dimensions, so VECTOR(384) and the SQL functions stay unchanged.
  - Every stored chunk would still have to be re-embedded, because the vector spaces differ.
  - Peak memory needs an explicit embedding batch size.

**Why not e5-large:** it has the best quality across the board, but the 1024-dim schema migration, ~7× query latency and ~13× ingestion cost are not reasonable for this deployment. It remains the quality reference.

**Production was not migrated in Phase 14.**

## 19. Production impact

Phase 14 Track B changed nothing in production:
- **Unchanged:** the production embedding, vectors, schema, SQL functions, query transformation and reranking.
- **Not done:** no production re-ingestion, no requirements change for Track B, no Windows security change.
- **Writes to Supabase during Track B:** none. Chunk loads and the dense-order validation were reads.
- **One file changed:** `rag/evaluation/embedding_benchmark.py` (the Windows peak-memory fix, evaluation-only).

## 20. Recommended next phase

A controlled **embedding migration experiment** (owner approval required before any production change):

1. Add an explicit, configurable embedding `batch_size` (for example 16–32) and measure peak memory and ingestion time for the current model and e5-small.
2. Re-ingest copies of the six evaluation documents with e5-small into **separate eval-only rows** (`passage:` prefix for chunks, `query:` for queries). Do not touch production rows.
3. Recover English MRR, in priority order:
   - check RRF weights and reranker normalisation with E5 similarity distributions;
   - handle broad "about" questions;
   - stop English-only patterns from classifying every non-English query as "specific" (Track A, C).
4. Re-run all six datasets. Accept only if English MRR is within about 0.01 of 0.829 and Odia and Hindi keep their gains.
5. Only then plan the production cut-over: a re-embedding job for all documents, then a switch. The dimension stays 384, so no SQL change is needed.
6. Evaluate df30 in the same experiment.
