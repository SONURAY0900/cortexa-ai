# PHASE 14 REPORT (PARTIAL — stopped at usage limit)

PHASE 14 STATUS: IN PROGRESS (Track A measured; Track B not run)
PRODUCTION EMBEDDING: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING MIGRATION: NOT PERFORMED
PRODUCTION DATABASE: NO DESTRUCTIVE CHANGES (2 eval-only resume copies added)
PRODUCTION VECTOR CHANGES: NONE

**Repository state:** the project folder is still not a git repository. Changed files are backed up in the session scratchpad (`phase14_backup/`). `phase13_backup/` is untouched.

## Track A results (full production pipeline; Phase 13 state = baseline)

### A. Document-specific cleaning — REMOVED

There were 12 hardcoded replacements in `text_cleaner._repair_split_words`:
- 8 fire only on the resume ("injec tion", "preve nting", ...).
- 1 is a no-op ("agent pipeline" → "agent pipeline").
- 3 are arbitrary rewrites ("real time", "end to end", "micro service") that fired on none of the 6 evaluation PDFs or 6 other English PDFs.

They are document-specific, so the step was removed. No dictionary or other rule replaces it.

**Owner approved keeping this removal on 2026-10-08.** Existing stored chunks are not re-cleaned; only new uploads are affected. Re-verifying with the evaluator was blocked that day: Windows Application Control stops `mmh3.pyd` (needed by fastembed) from loading. The numbers below are from the original 00:36 run.

**Resume impact** (re-ingested eval copies, labels transferred by overlap):

| Version | R@5 | R@10 | P@5 | MRR |
|---|---|---|---|---|
| With rules | 0.792 | 0.900 | 0.500 | 0.839 |
| Without rules | 0.742 | 0.850 | 0.490 | 0.863 |

R@5 drops because the resume's split words stay split. Part of the old score came from fixes written for this exact document. The other 5 PDFs are unaffected, since the rules never fired on them.

### B. Context builder Unicode tokens — REVERTED (no benefit)

- **English:** identical on every metric and on context recall.
- **Hindi:** context recall went down, 0.833 → 0.814.
- **Odia:** unchanged.

The change was reverted to the Phase 13 version. The new optional `extra_chars` parameter in `text_tokens.unicode_tokens` is now unused and harmless.

### C. Query transformer — LEAVE UNCHANGED

| Dataset | No transform → current |
|---|---|
| Resume | R@5 0.700 → 0.739 |
| Flask | R@5 0.656 → 0.681, MRR 0.598 → 0.647 |
| Attention | MRR 0.772 → 0.860 |
| Entrepreneurship | neutral |
| Hindi | neutral |
| Odia | neutral |

The transformer helps English and is neutral elsewhere.

**Finding:** every native-script and mixed Hindi/Odia query is classified "specific", because the list/broad patterns are English. Specific queries return only 5 results, which caps R@10 at R@5 for those languages. I haven't changed this.

### D. Document-frequency keyword filter (offline experiment only, NOT in production)

The experiment drops query keywords that appear in ≥ X% of the document's chunks. Results:

| Dataset | df30 | df50 | df70 |
|---|---|---|---|
| Flask | R@5 0.681 → **0.858**, MRR 0.647 → 0.706 | no change | no change |
| Attention | R@5 0.626 → 0.651, MRR 0.860 → 0.879 | **worse** (R@5 0.601) | no change |
| Hindi | R@3 0.686 → 0.716, MRR 0.618 → **0.721** | same as df30 | R@3 0.716, MRR 0.652 |
| Entrepreneurship | unchanged | unchanged | unchanged |
| Odia | metrics unchanged (pool recall 0.148 → 0.170) | unchanged | unchanged |
| Resume | R@5 0.739 → 0.727 (one query: "databases") | not completed | not completed |

df30 is promising: 6 queries improve and 1 gets worse.

**It is not implemented in production yet.** Production would need the document's chunk text at query time (fetched and cached per document); the SQL function would not change. This needs your decision.

## Track B — NOT RUN

**Candidates chosen and verified:** intfloat/multilingual-e5-small (384-dim), -base (768) and -large (1024).
- Their model cards list both `hi` and `or` (Hindi and Odia).
- MIT license.
- ONNX weights load through the existing fastembed; no new package.
- All three are downloaded, and their tokenizers read Odia with no unknown tokens.
- `embedding_benchmark.py` now applies the "query: " / "passage: " prefixes e5 requires and records peak memory.

**Still to do:** run the benchmark for all 3 candidates on the 6 datasets. No embedding decision has been made yet.
