"""
Re-embed every stored PDF chunk with the current embedding model.

Run this once after changing MODEL_NAME in rag/embeddings/embeddings.py:
vectors from different models are not comparable, so old chunks would
otherwise stop matching new questions.

    python -m scripts.reindex_embeddings            # count only, change nothing
    python -m scripts.reindex_embeddings --apply    # rewrite document_chunks.embedding

Only the `embedding` column is updated, row by row; chunk text, page
numbers and documents are untouched. Chunks are processed in id order,
so an interrupted run can be resumed with --after <last printed id>.
Uses the server-side secret key from backend/.env (trusted machine only).
"""

import sys
import time

from database.connection import supabase
from rag.embeddings.embeddings import MODEL_NAME, generate_embeddings

PAGE = 200


def _args() -> tuple[bool, str | None]:
    apply = "--apply" in sys.argv
    after = None
    if "--after" in sys.argv:
        after = sys.argv[sys.argv.index("--after") + 1]
    return apply, after


def _pages(after: str | None):
    while True:
        query = supabase.table("document_chunks").select("id,content").order("id").limit(PAGE)
        if after:
            query = query.gt("id", after)
        rows = query.execute().data or []
        if not rows:
            return
        yield rows
        after = rows[-1]["id"]


def main() -> None:
    apply, after = _args()

    total = supabase.table("document_chunks").select("id", count="exact").limit(1).execute().count
    documents = supabase.table("documents").select("id", count="exact").limit(1).execute().count
    print(f"Model: {MODEL_NAME}")
    print(f"{total} chunks in {documents} documents")

    if not apply:
        print("Dry run: nothing changed. Add --apply to re-embed.")
        return

    done, started = 0, time.monotonic()
    for rows in _pages(after):
        vectors = generate_embeddings([r["content"] or "" for r in rows])
        for row, vector in zip(rows, vectors):
            supabase.table("document_chunks").update({"embedding": vector}).eq("id", row["id"]).execute()
        done += len(rows)
        print(f"{done}/{total} re-embedded (last id {rows[-1]['id']}, {time.monotonic() - started:.0f}s)")

    print("Done.")


if __name__ == "__main__":
    main()
