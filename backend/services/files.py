"""
Canonical file records (public.files) and the status vocabularies of the
tables that hang off them. files.id is the identity of every upload:

    files ──┬── PDF   → documents (documents.file_id)
            ├── IMAGE → images    (images.file_id)
            └── AUDIO → audio_files

Each table has its own status column with its own allowed values (see
the CHECK constraints); they are deliberately not shared.
"""

from datetime import datetime, timezone

from database.connection import supabase
from services.storage import delete_stored_file, resolve_inside_uploads
from utils.logger import log_agent


class FileType:
    PDF = "PDF"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"


class FileStatus:                 # files.status
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    DELETED = "DELETED"


class ImageStepStatus:            # images.ocr_status / images.vision_status
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class DocumentStatus:             # documents.processing_status
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


FILE_COLUMNS = "id,user_id,filename,storage_path,file_type,mime_type,file_size,status,deleted_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_file(
    user_id: str,
    filename: str,
    storage_path: str,
    file_type: str,
    mime_type: str,
    file_size: int,
    status: str = FileStatus.UPLOADED,
) -> dict:
    result = supabase.table("files").insert({
        "user_id": user_id,
        "filename": filename,
        "storage_path": storage_path,
        "file_type": file_type,
        "mime_type": mime_type,
        "file_size": file_size,
        "status": status,
    }).execute()

    if not result.data:
        raise RuntimeError("File record could not be created.")

    return result.data[0]


def set_file_status(file_id: str, status: str, metadata: dict | None = None) -> None:
    """
    Update files.status (and, if given, replace files.metadata, e.g. the
    PDF processing summary). Failures are logged, never raised.
    """
    values = {"status": status}
    if metadata is not None:
        values["metadata"] = metadata
    try:
        supabase.table("files").update(values).eq("id", file_id).execute()
    except Exception as e:
        log_agent("DATABASE", f"File status update failed: {e}")


def image_statuses(ocr_ok: bool, vision_ok: bool) -> dict:
    """images.* step statuses plus the resulting files.status."""
    return {
        "ocr_status": ImageStepStatus.SUCCESS if ocr_ok else ImageStepStatus.FAILED,
        "vision_status": ImageStepStatus.SUCCESS if vision_ok else ImageStepStatus.FAILED,
        "file_status": FileStatus.PROCESSED if (ocr_ok or vision_ok) else FileStatus.FAILED,
    }


def owned_files(file_ids: list[str], user_id: str) -> dict[str, dict]:
    """Rows for the given IDs that belong to the user and are not deleted."""
    if not file_ids:
        return {}
    rows = (
        supabase.table("files")
        .select(FILE_COLUMNS)
        .in_("id", list(dict.fromkeys(file_ids)))
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .execute()
        .data
        or []
    )
    return {row["id"]: row for row in rows}


def completed_documents_for_files(file_ids: list[str], user_id: str) -> dict[str, dict]:
    """file_id -> completed document owned by the user."""
    if not file_ids:
        return {}
    rows = (
        supabase.table("documents")
        .select("id,file_id,filename,processing_status")
        .in_("file_id", file_ids)
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    return {
        row["file_id"]: row
        for row in rows
        if row.get("processing_status") == DocumentStatus.COMPLETED
    }


def attachment_reference(row: dict) -> dict:
    """What a message stores about an attachment (no paths, no content)."""
    return {
        "file_id": row["id"],
        "filename": row["filename"],
        "file_type": row["file_type"],
        "mime_type": row.get("mime_type"),
    }


def owned_file_path(file_id: str, user_id: str):
    """(row, server path) for a stored file the user owns, else (None, None)."""
    row = owned_files([file_id], user_id).get(file_id)
    path = resolve_inside_uploads(row.get("storage_path")) if row else None
    return (row, path) if path else (None, None)


def soft_delete_file(row: dict) -> None:
    """
    Remove what a file produced (document + chunks, image analysis) and
    its stored bytes; keep the files row as DELETED for history.
    """
    from rag.retrieval.vector_store import delete_document

    if row["file_type"] == FileType.PDF:
        docs = supabase.table("documents").select("id").eq("file_id", row["id"]).execute().data or []
        for doc in docs:
            delete_document(doc["id"])
    elif row["file_type"] == FileType.IMAGE:
        supabase.table("images").delete().eq("file_id", row["id"]).execute()

    delete_stored_file(row.get("storage_path"))

    supabase.table("files").update({
        "status": FileStatus.DELETED,
        "deleted_at": _now(),
    }).eq("id", row["id"]).execute()
