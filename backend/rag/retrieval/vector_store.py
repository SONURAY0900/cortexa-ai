from database.connection import supabase


# ============================================================
# CREATE DOCUMENT
# ============================================================

def create_document(
    filename: str,
    file_path: str,
    file_type: str,
    file_size: int,
    user_id: str | None = None,
    file_id: str | None = None
) -> str:

    response = (
        supabase
        .table("documents")
        .insert(
            {
                "filename": filename,
                "file_path": file_path,
                "file_type": file_type,
                "file_size": file_size,
                "user_id": user_id,
                "file_id": file_id,
                "processing_status": "PROCESSING"
            }
        )
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Failed to create document record."
        )

    return response.data[0]["id"]


# ============================================================
# STORE CHUNKS
# ============================================================

def store_chunks(
    document_id: str,
    chunks: list[dict]
) -> list:

    if not chunks:
        return []

    rows = []

    for chunk in chunks:

        rows.append(
            {
                "document_id": document_id,

                "chunk_index": chunk[
                    "chunk_index"
                ],

                "content": chunk[
                    "content"
                ],

                "page_number": chunk.get(
                    "page_number"
                ),

                "embedding": chunk[
                    "embedding"
                ]
            }
        )

    response = (
        supabase
        .table("document_chunks")
        .insert(rows)
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Failed to store document chunks."
        )

    return response.data


# ============================================================
# DELETE DOCUMENT
# ============================================================

def document_chunks_in_order(document_id: str, limit: int = 400) -> list[dict]:
    """A document's chunks in reading order (for whole-document requests)."""

    return (
        supabase.table("document_chunks")
        .select("chunk_index,page_number,content")
        .eq("document_id", document_id)
        .order("chunk_index")
        .limit(limit)
        .execute()
        .data
        or []
    )


def delete_document(
    document_id: str
) -> None:
    """
    Remove a document and all of its chunks (chunks first, so the
    foreign key from document_chunks never blocks the delete).
    """

    supabase.table("document_chunks").delete().eq(
        "document_id", document_id
    ).execute()

    supabase.table("documents").delete().eq(
        "id", document_id
    ).execute()
