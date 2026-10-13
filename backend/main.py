import io
import json
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    Depends,
    Request,
    BackgroundTasks,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from graph.workflow import chat_graph
from graph.router import route_query

from agents.llm_agent import stream_response
from agents.wikipedia_agent import wikipedia_agent
from agents.tavily_agent import tavily_agent
from agents.arxiv_agent import arxiv_agent
from agents.rag_agent import NOT_FOUND, retrieve_pdf_context, stream_pdf_answer
from agents.speech_agent import speech_to_text_agent

from account.routes import router as account_router
from admin.routes import router as admin_router
from auth.supabase_auth import RoleUnavailable, get_current_user, get_user_or_guest, is_admin, is_guest
from config.settings import (
    ALLOWED_ORIGINS,
    APP_ENV,
    APP_VERSION,
    EMAIL_OTP_EXPIRY_S,
    EMAIL_OTP_RESEND_S,
    MAX_AUDIO_MB,
    MAX_IMAGE_MB,
    MAX_PDF_MB,
    SUPABASE_PUBLISHABLE_KEY,
    SUPABASE_URL,
)
from database.connection import supabase
from rag.pipeline import IngestionError, ingest_pdf_with_summary
from rag.retrieval.vector_store import delete_document
from services import guest_limits
from services.activity import log_activity
from services.welcome_email import send_welcome_if_new
from services.files import (
    FileStatus,
    FileType,
    ImageStepStatus,
    attachment_reference,
    completed_documents_for_files,
    create_file,
    image_statuses,
    owned_file_path,
    owned_files,
    set_file_status,
    soft_delete_file,
)
from services.image_analysis import combine, run_ocr, run_vision
from services.storage import (
    IMAGE_UPLOAD_DIR,
    PDF_UPLOAD_DIR,
    delete_stored_file,
    resolve_inside_uploads,
    safe_display_name,
    save_upload,
)
from utils.logger import log_agent

# ============================================================
# FASTAPI APP
# ============================================================

IS_PRODUCTION = APP_ENV.lower() == "production"

app = FastAPI(
    title="Cortexa AI",
    description="Cortexa AI: multi-agent assistant built with LangGraph, LangChain and Groq",
    version=APP_VERSION,
    # Interactive API docs are disabled in production.
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None,
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)


# ============================================================
# CORS / SECURITY HEADERS / ERRORS
# ============================================================

# The frontend is served by this app (same origin), so cross-origin
# access is only enabled for origins listed in ALLOWED_ORIGINS.
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "The request is invalid. Please check the input and try again."},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    # Details go to the server log only; clients never see internals.
    log_agent("SYSTEM", f"Unhandled error on {request.url.path}: {exc!r}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong. Please try again."},
    )


# ============================================================
# FRONTEND
# ============================================================

BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

app.mount(
    "/frontend",
    StaticFiles(directory=FRONTEND_DIR),
    name="frontend"
)


def _page(name: str) -> FileResponse:
    # Revalidate HTML on every visit so a deploy's new ?v= asset URLs are
    # picked up. (no-cache, not no-store: Back/Forward caching still works.)
    return FileResponse(FRONTEND_DIR / name, headers={"Cache-Control": "no-cache"})


@app.get("/")
def home():
    return _page("index.html")


# Static pages; every API call they make is authorized server-side.
@app.get("/admin")
def admin_page():
    return _page("admin.html")


@app.get("/profile")
def profile_page():
    return _page("profile.html")


@app.get("/settings")
def settings_page():
    return _page("settings.html")


@app.get("/about")
def about_page():
    return _page("about.html")


# Browsers probe /favicon.ico even with <link rel="icon">; serve the logo
# instead of logging a 404 on every page view.
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FRONTEND_DIR / "assets" / "logo" / "logo.svg", media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})


# ============================================================
# HEALTH / PUBLIC CONFIG
# ============================================================

@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/config")
def public_config():
    """
    Public values the browser needs to start Supabase Auth.
    The publishable key is public by design; no secret is returned.
    """
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_publishable_key": SUPABASE_PUBLISHABLE_KEY,
        "limits": {
            "pdf_mb": MAX_PDF_MB,
            "image_mb": MAX_IMAGE_MB,
            "audio_mb": MAX_AUDIO_MB,
            "attachments_per_message": MAX_ATTACHMENTS,
        },
        # Display only (countdowns). Supabase Auth enforces code expiry.
        "email_otp": {
            "expiry_seconds": EMAIL_OTP_EXPIRY_S,
            "resend_seconds": EMAIL_OTP_RESEND_S,
        },
    }


# ============================================================
# SUPABASE AUTH
# ============================================================

@app.get("/auth/me")
def get_me(background_tasks: BackgroundTasks, user=Depends(get_current_user)):
    """
    Return the currently authenticated Supabase user.
    is_admin is null when the role could not be read right now.

    Every page calls this once per signed-in user, so it is also where a
    new account's welcome email is queued (sent after the response; at
    most once per account, see services/welcome_email.py).
    """
    background_tasks.add_task(send_welcome_if_new, user)
    try:
        admin = is_admin(user)
    except RoleUnavailable:
        admin = None

    return {
        "success": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "created_at": user.created_at,
            "is_admin": admin,
        }
    }


# ============================================================
# REQUEST MODEL
# ============================================================

MAX_ATTACHMENTS = 10
MAX_CONTEXT_FILES = 5


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None

    # Files attached to this message (files.id values returned by the
    # upload endpoints; ownership is checked server-side).
    attachments: list[UUID] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    # PDFs attached earlier in the conversation that the user keeps
    # using as context for follow-up questions.
    context_file_ids: list[UUID] = Field(default_factory=list, max_length=MAX_CONTEXT_FILES)

    # Legacy single-attachment fields (still accepted).
    document_id: str | None = None
    image_id: str | None = None

    # True when the client re-sends a question whose answer failed; the
    # stored, unanswered question is reused instead of saved again.
    retry: bool = False


@dataclass
class AttachmentPlan:
    """Server-resolved, ownership-checked inputs of one chat request."""
    references: list[dict] = field(default_factory=list)   # stored in message metadata
    documents: list[dict] = field(default_factory=list)    # [{"id", "filename"}] for RAG
    images: list[dict] = field(default_factory=list)       # [{"file_id", "filename", "path"}]


# ============================================================
# PERSISTENT CHAT MEMORY / SUPABASE
# ============================================================

def _user_id(user) -> str | None:
    """The account id, or None for a guest."""
    return None if is_guest(user) else str(user.id)


def _own_conversations(query, owner):
    """Limit a conversations query to the owner's rows (account or guest)."""
    if is_guest(owner):
        return query.eq("guest_session_id", owner.guest_id).is_("user_id", "null")
    return query.eq("user_id", _user_id(owner))


def _own_messages(query, owner):
    """Limit a messages query that is already scoped to an owned conversation."""
    if is_guest(owner):
        return query.is_("user_id", "null")
    return query.eq("user_id", _user_id(owner))


def _document_owned_by_user(document_id: str, user) -> dict | None:
    """
    Return the requested document if it belongs to the authenticated user.
    """
    try:
        result = (
            supabase
            .table("documents")
            .select("id,filename,file_id")
            .eq("id", document_id)
            .eq("user_id", _user_id(user))
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        log_agent("DATABASE", f"Document ownership check failed: {e}")
        return None


def _conversation_owned_by_user(conversation_id: str, user) -> bool:
    try:
        query = (
            supabase
            .table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("is_archived", False)
        )
        result = _own_conversations(query, user).limit(1).execute()
        return bool(result.data)
    except Exception as e:
        log_agent("DATABASE", f"Conversation ownership check failed: {e}")
        return False


def _create_conversation(user, title: str = "New Chat") -> dict:
    owner = {"guest_session_id": user.guest_id} if is_guest(user) else {"user_id": _user_id(user)}
    result = (
        supabase
        .table("conversations")
        .insert({
            **owner,
            "title": (title or "New Chat").strip()[:120] or "New Chat",
        })
        .execute()
    )

    if not result.data:
        raise RuntimeError("Unable to create conversation.")

    return result.data[0]


def _get_or_create_conversation(
    conversation_id: str | None,
    user,
    title: str = "New Chat",
) -> dict:
    if conversation_id:
        query = (
            supabase
            .table("conversations")
            .select("*")
            .eq("id", conversation_id)
            .eq("is_archived", False)
        )
        result = _own_conversations(query, user).limit(1).execute()

        if result.data:
            return result.data[0]

        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return _create_conversation(user, title)


def _get_conversation_history(
    conversation_id: str,
    user,
    limit: int = 20,
) -> list[dict]:
    query = (
        supabase
        .table("messages")
        .select("role,content,created_at")
        .eq("conversation_id", conversation_id)
    )
    result = _own_messages(query, user).order("created_at", desc=True).limit(limit).execute()

    rows = list(reversed(result.data or []))

    return [
        {
            "role": str(row.get("role", "")).lower(),
            "content": row.get("content", ""),
        }
        for row in rows
        if row.get("content")
    ]


def _save_message(
    conversation_id: str,
    user,
    role: str,
    content: str,
    route: str | None = None,
    metadata: dict | None = None,
) -> str | None:
    if not content:
        return None

    result = supabase.table("messages").insert({
        "conversation_id": conversation_id,
        "user_id": _user_id(user),
        "role": role.upper(),
        "content": content,
        "route": route,
        "metadata": metadata or {},
    }).execute()

    return result.data[0]["id"] if result.data else None


def _unanswered_question_id(conversation_id: str, user, query: str) -> str | None:
    """ID of the conversation's last message if it is this same, unanswered user question."""
    try:
        lookup = supabase.table("messages").select("id,role,content").eq("conversation_id", conversation_id)
        rows = _own_messages(lookup, user).order("created_at", desc=True).limit(1).execute().data
    except Exception as e:
        log_agent("DATABASE", f"Retry lookup failed: {e}")
        return None
    if rows and str(rows[0].get("role")).upper() == "USER" and rows[0].get("content") == query:
        return rows[0]["id"]
    return None


def _set_message_route(message_id: str | None, route: str) -> None:
    """Record the selected route on the user's message (used by admin activity)."""
    if not message_id:
        return
    try:
        supabase.table("messages").update({"route": route}).eq("id", message_id).execute()
    except Exception as e:
        log_agent("DATABASE", f"Route update failed: {e}")


def _update_conversation_title_if_needed(
    conversation_id: str,
    user,
    query: str,
):
    lookup = supabase.table("conversations").select("title").eq("id", conversation_id)
    result = _own_conversations(lookup, user).limit(1).execute()

    if not result.data:
        return

    current_title = (result.data[0].get("title") or "").strip()

    if current_title and current_title.lower() != "new chat":
        return

    title = " ".join((query or "").strip().split())

    if len(title) > 60:
        title = title[:57].rstrip() + "..."

    update = supabase.table("conversations").update({"title": title or "New Chat"}).eq("id", conversation_id)
    _own_conversations(update, user).execute()


def _plan_attachments(request: ChatRequest, user) -> AttachmentPlan:
    """
    Resolve every file the request refers to, checking ownership.
    Client values are only IDs; paths come from the database and must
    resolve inside the upload directory.
    """
    user_id = _user_id(user)
    plan = AttachmentPlan()

    attached = [str(i) for i in request.attachments]
    context = [str(i) for i in request.context_file_ids if str(i) not in attached]
    if request.image_id:
        attached.append(request.image_id)

    requested = list(dict.fromkeys(attached + context))
    try:
        rows = owned_files(requested, user_id)
    except Exception as e:
        log_agent("DATABASE", f"Attachment lookup failed: {e}")
        rows = {}

    if len(rows) != len(requested):
        raise HTTPException(
            status_code=404,
            detail="An attached file could not be found. Please upload it again.",
        )

    pdf_ids = [fid for fid in requested if rows[fid]["file_type"] == FileType.PDF]
    documents = completed_documents_for_files(pdf_ids, user_id)

    for fid in requested:
        row = rows[fid]
        if fid in attached:
            plan.references.append(attachment_reference(row))

        if row["file_type"] == FileType.PDF:
            document = documents.get(fid)
            if not document:
                raise HTTPException(
                    status_code=409,
                    detail=f"“{row['filename']}” is not ready yet. Please wait until it has been processed.",
                )
            plan.documents.append({"id": document["id"], "filename": row["filename"]})

        elif row["file_type"] == FileType.IMAGE:
            # Images apply to the message they are attached to only.
            if fid not in attached:
                continue
            path = resolve_inside_uploads(row.get("storage_path"))
            if not path:
                raise HTTPException(
                    status_code=404,
                    detail="An attached image could not be found. Please upload it again.",
                )
            plan.images.append({"file_id": fid, "filename": row["filename"], "path": str(path)})

    # Legacy: a bare document_id (PDFs uploaded before files rows existed).
    if request.document_id and not any(d["id"] == request.document_id for d in plan.documents):
        document = _document_owned_by_user(request.document_id, user)
        if not document:
            raise HTTPException(status_code=403, detail="You do not have access to this PDF.")
        plan.documents.append({"id": document["id"], "filename": document["filename"]})

    return plan


def _message_metadata(plan: AttachmentPlan, request: ChatRequest) -> dict:
    # "attachments" is always present on new messages (possibly empty), which
    # tells readers not to treat document_id below as an attached file.
    metadata = {"attachments": plan.references}
    if request.context_file_ids:
        metadata["context_file_ids"] = [str(i) for i in request.context_file_ids]
    # Kept for older readers (admin activity) of message metadata.
    if plan.documents:
        metadata["document_id"] = plan.documents[0]["id"]
    if plan.images:
        metadata["image_id"] = plan.images[0]["file_id"]
    return metadata


def _record_image_analysis(file_id: str, result: dict) -> None:
    """Persist OCR/vision results with the images/files status vocabularies."""
    statuses = image_statuses(result.get("ocr_ok"), result.get("vision_ok"))
    try:
        supabase.table("images").update({
            "ocr_text": (result.get("ocr_text") or "")[:20000],
            "vision_result": (result.get("vision_answer") or "")[:20000],
            "ocr_status": statuses["ocr_status"],
            "vision_status": statuses["vision_status"],
        }).eq("file_id", file_id).execute()
    except Exception as e:
        log_agent("DATABASE", f"Image status update failed: {e}")
    set_file_status(file_id, statuses["file_status"])


async def _read_upload(file: UploadFile, max_mb: int, kind: str) -> bytes:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected.")

    content = await file.read(max_mb * 1024 * 1024 + 1)

    if not content:
        raise HTTPException(status_code=400, detail=f"The uploaded {kind} is empty.")

    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"The {kind} is too large. Maximum size is {max_mb} MB.")

    return content


# ============================================================
# PDF UPLOAD
# ============================================================

PDF_STAGE_LABELS = {
    "uploaded": "Uploaded",
    "extracting": "Extracting embedded text",
    "ocr": "Reading scanned pages (OCR)",
    "chunking": "Chunking",
    "embedding": "Creating embeddings",
    "indexing": "Indexing",
}

# User-facing messages per IngestionError.reason (no internals).
PDF_FAILURE_MESSAGES = {
    "ocr_failed": "We couldn't read the scanned pages of this PDF because the text-recognition service "
                  "kept failing. Please try again in a few minutes.",
    "no_text": "This PDF has no readable text. If it is a scan, try a clearer copy.",
    "indexing_failed": "The PDF was read, but saving it for search failed. Please try again.",
    "invalid_pdf": "This PDF could not be opened. Please try another file.",
    "cancelled": "The upload was removed.",
}
PDF_FAILURE_DEFAULT = "PDF processing failed. Please try another file."

# Seconds between keep-alive lines while a long step (OCR) runs, so the
# client and any proxy keep the stream open.
PDF_HEARTBEAT_S = 15


def _file_was_deleted(file_id: str) -> bool:
    try:
        rows = supabase.table("files").select("status").eq("id", file_id).limit(1).execute().data
        return bool(rows) and rows[0].get("status") == FileStatus.DELETED
    except Exception:
        return False


def _pdf_result_message(summary: dict) -> str:
    failed = summary.get("failed_pages") or []
    if summary.get("partial") and failed:
        pages = ", ".join(str(n) for n in failed)
        plural = len(failed) > 1
        return (
            f"PDF processed with missing pages: page{'s' if plural else ''} {pages} "
            f"could not be read and {'are' if plural else 'is'} not searchable."
        )
    return "PDF uploaded and processed successfully."


@app.post("/upload/pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    stream: bool = False,
    user=Depends(get_current_user),
):
    """
    Upload a PDF and process it through the RAG pipeline.

    The result always carries a processing summary (pages, OCR results,
    failed pages, chunks, indexed). Outcomes:

      success   every page with text indexed            files.status PROCESSED
      partial   some pages unreadable, the rest indexed  files.status PROCESSED,
                                                         summary.partial = true
      failure   nothing searchable                       files.status FAILED (422)

    The summary is also stored in files.metadata["processing"].

    With ?stream=true the response is newline-delimited JSON with the
    real ingestion stages (including per-page OCR progress), keep-alive
    lines, and a final "done" or "error" event.
    """

    original_filename = safe_display_name(file.filename)

    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    content = await _read_upload(file, MAX_PDF_MB, "PDF")

    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="The uploaded file does not appear to be a valid PDF.")

    file_path = save_upload(PDF_UPLOAD_DIR, content, ".pdf")
    user_id = _user_id(user)

    try:
        file_row = create_file(
            user_id, original_filename, str(file_path), FileType.PDF,
            "application/pdf", len(content), FileStatus.PROCESSING,
        )
    except Exception as e:
        log_agent("PDF", f"File record failed: {e!r}")
        delete_stored_file(str(file_path))
        raise HTTPException(status_code=500, detail="PDF upload failed. Please try again.")

    file_id = file_row["id"]
    log_agent("PDF", f"Received {original_filename} ({len(content)} bytes) as file {file_id}")

    def run_ingestion(progress=None) -> dict:
        """Returns the success payload, or raises IngestionError."""
        try:
            document_id, summary = ingest_pdf_with_summary(
                file_path=str(file_path),
                filename=original_filename,
                user_id=user_id,
                progress=progress,
                file_id=file_id,
            )
        except Exception as e:
            error = e if isinstance(e, IngestionError) else IngestionError("unknown", repr(e))
            if error.reason == "cancelled":
                raise error
            log_agent("PDF", f"PDF processing failed ({error.reason}): {error} | summary={error.summary}")
            delete_stored_file(str(file_path))
            set_file_status(file_id, FileStatus.FAILED, {"processing": {**error.summary, "reason": error.reason}})
            log_activity(user_id, "PDF_UPLOAD_FAILED", original_filename, "FAILED", file_name=original_filename,
                         metadata={"reason": error.reason, "failed_pages": error.summary.get("failed_pages", [])})
            raise error

        if _file_was_deleted(file_id):
            # The user removed the attachment while it was processing.
            delete_document(document_id)
            delete_stored_file(str(file_path))
            log_agent("PDF", f"File {file_id} was removed during processing; document discarded.")
            raise IngestionError("cancelled", "Upload was removed during processing.", summary)

        set_file_status(file_id, FileStatus.PROCESSED, {"processing": summary})
        if summary["partial"]:
            log_activity(user_id, "PDF_OCR_PARTIAL", original_filename, "FAILED", document_id=document_id,
                         file_name=original_filename, metadata={"failed_pages": summary["failed_pages"]})
        log_agent(
            "PDF",
            f"PDF ingestion completed: {document_id} "
            + (f"(partial: pages {summary['failed_pages']} unreadable)" if summary["partial"] else "(all pages)"),
        )
        return {
            "success": True,
            "partial": summary["partial"],
            "message": _pdf_result_message(summary),
            "file_id": file_id,
            "document_id": document_id,
            "filename": original_filename,
            "file_size": len(content),
            "summary": summary,
        }

    def failure_payload(error: IngestionError) -> dict:
        return {
            "detail": PDF_FAILURE_MESSAGES.get(error.reason, PDF_FAILURE_DEFAULT),
            "reason": error.reason,
            "retryable": error.reason in ("ocr_failed", "indexing_failed"),
            "file_id": file_id,
            "summary": error.summary,
        }

    if not stream:
        try:
            return run_ingestion()
        except IngestionError as error:
            return JSONResponse(status_code=422, content=failure_payload(error))

    events: queue.Queue = queue.Queue()

    def worker():
        def progress(stage, details=None):
            events.put({"type": "stage", "stage": stage, "label": PDF_STAGE_LABELS.get(stage, stage), **(details or {})})
        try:
            events.put({"type": "done", **run_ingestion(progress)})
        except IngestionError as error:
            events.put({"type": "error", **failure_payload(error)})
        except Exception as e:      # never leave the client waiting
            log_agent("PDF", f"Unexpected ingestion error: {e!r}")
            events.put({"type": "error", "detail": PDF_FAILURE_DEFAULT, "reason": "unknown", "retryable": True})

    def event_stream():
        yield json.dumps({"type": "stage", "stage": "uploaded", "label": PDF_STAGE_LABELS["uploaded"], "file_id": file_id}) + "\n"
        threading.Thread(target=worker, daemon=True).start()
        while True:
            try:
                event = events.get(timeout=PDF_HEARTBEAT_S)
            except queue.Empty:
                yield json.dumps({"type": "heartbeat"}) + "\n"
                continue
            yield json.dumps(event) + "\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


# ============================================================
# IMAGE UPLOAD
# ============================================================

IMAGE_TYPES = {
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".png": ("PNG", "image/png"),
    ".webp": ("WEBP", "image/webp"),
    ".bmp": ("BMP", "image/bmp"),
}


@app.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """
    Upload an image for OCR + Vision analysis. Returns a file_id that
    only this user can attach to a chat message.
    """

    original_filename = safe_display_name(file.filename)
    extension = Path(original_filename).suffix.lower()

    if extension not in IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPG, JPEG, PNG, WEBP and BMP images are allowed.")

    content = await _read_upload(file, MAX_IMAGE_MB, "image")

    # Verify the bytes really are an image of the claimed type.
    try:
        with Image.open(io.BytesIO(content)) as image:
            detected_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        detected_format = None

    expected_format, mime_type = IMAGE_TYPES[extension]

    if detected_format != expected_format:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image.")

    file_path = save_upload(IMAGE_UPLOAD_DIR, content, extension)
    user_id = _user_id(user)

    try:
        row = create_file(
            user_id, original_filename, str(file_path), FileType.IMAGE,
            mime_type, len(content), FileStatus.UPLOADED,
        )

        supabase.table("images").insert({
            "file_id": row["id"],
            "ocr_status": ImageStepStatus.PENDING,
            "vision_status": ImageStepStatus.PENDING,
        }).execute()

    except Exception as e:
        log_agent("IMAGE", f"Image record failed: {e!r}")
        delete_stored_file(str(file_path))
        log_activity(user_id, "IMAGE_UPLOAD_FAILED", original_filename, "FAILED", file_name=original_filename)
        raise HTTPException(status_code=500, detail="Image upload failed. Please try again.")

    log_agent("IMAGE", f"Image saved for user {user_id}: {row['id']}")

    return {
        "success": True,
        "message": "Image uploaded successfully.",
        "file_id": row["id"],
        "image_id": row["id"],
        "filename": original_filename,
        "mime_type": mime_type,
        "file_size": len(content),
    }


# ============================================================
# USER FILES (owner only)
# ============================================================

@app.get("/files/{file_id}/content")
def file_content(file_id: UUID, user=Depends(get_current_user)):
    """
    Stream one of the user's own uploaded files (thumbnails, opening a
    PDF). Unknown, foreign and deleted files are all reported as 404.
    """
    row, path = owned_file_path(str(file_id), _user_id(user))

    if not path:
        raise HTTPException(status_code=404, detail="File not found.")

    return FileResponse(
        path,
        media_type=row.get("mime_type") or "application/octet-stream",
        filename=row["filename"],
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.delete("/files/{file_id}")
def delete_file(file_id: UUID, user=Depends(get_current_user)):
    """Delete one of the user's own uploads (e.g. removed before sending)."""
    rows = owned_files([str(file_id)], _user_id(user))
    row = rows.get(str(file_id))

    if not row:
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        soft_delete_file(row)
    except Exception as e:
        log_agent("FILES", f"Delete failed for {file_id}: {e!r}")
        raise HTTPException(status_code=500, detail="The file could not be removed. Please try again.")

    return {"success": True}


# ============================================================
# VOICE TO TEXT
# ============================================================

AUDIO_EXTENSIONS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}


@app.post("/voice/transcribe")
async def transcribe_voice(
    file: UploadFile = File(...),
    language: str | None = None,
    user=Depends(get_current_user),
):
    """
    Transcribe an uploaded audio file using Groq Whisper Large V3 Turbo.
    The frontend then sends the returned text to /chat/stream.
    """

    original_filename = safe_display_name(file.filename)

    if Path(original_filename).suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported audio format. Allowed formats: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm.",
        )

    if language and (len(language) > 8 or not language.replace("-", "").isalpha()):
        raise HTTPException(status_code=400, detail="Invalid language code.")

    content = await _read_upload(file, MAX_AUDIO_MB, "audio file")
    user_id = _user_id(user)

    result = speech_to_text_agent(
        audio_bytes=content,
        filename=original_filename,
        language=language
    )

    if result.get("error"):
        log_agent("VOICE", f"Transcription failed: {result['error']}")
        log_activity(user_id, "VOICE", "Voice transcription", "FAILED")
        raise HTTPException(status_code=502, detail="Speech transcription is temporarily unavailable.")

    transcript = (result.get("text") or "").strip()

    log_activity(
        user_id,
        "VOICE",
        "Voice transcription",
        "SUCCESS",
        metadata={"characters": len(transcript), "language": language},
    )

    return {
        "success": True,
        "language": language,
        "text": transcript,
        "message": "" if transcript else "No speech was detected in the audio.",
    }


# ============================================================
# CONVERSATIONS / CHAT HISTORY
# ============================================================

class ConversationCreate(BaseModel):
    title: str = Field(default="New Chat", max_length=120)


@app.get("/conversations")
def list_conversations(user=Depends(get_user_or_guest)):
    try:
        query = (
            supabase
            .table("conversations")
            .select("id,title,is_archived,created_at,updated_at,last_message_at")
            .eq("is_archived", False)
        )
        result = _own_conversations(query, user).order("updated_at", desc=True).execute()
        return {"success": True, "conversations": result.data or []}
    except Exception as e:
        log_agent("DATABASE", f"List conversations failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to load conversations.")


@app.post("/conversations")
def create_conversation(
    body: ConversationCreate | None = None,
    user=Depends(get_user_or_guest),
):
    try:
        conversation = _create_conversation(user, body.title if body else "New Chat")
        return {"success": True, "conversation": conversation}
    except Exception as e:
        log_agent("DATABASE", f"Create conversation failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to create conversation.")


@app.post("/conversations/archive-all")
def archive_all_conversations(user=Depends(get_user_or_guest)):
    """Remove every chat from the user's history (soft archive, like single delete)."""
    try:
        update = supabase.table("conversations").update({"is_archived": True}).eq("is_archived", False)
        archived = _own_conversations(update, user).execute().data or []
        return {"success": True, "archived": len(archived)}
    except Exception as e:
        log_agent("DATABASE", f"Archive all conversations failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to clear your chat history.")


class GuestClaim(BaseModel):
    guest_session_id: UUID


@app.post("/conversations/claim-guest")
def claim_guest_conversations(body: GuestClaim, user=Depends(get_current_user)):
    """
    Move a visitor's guest chats into the account they just signed in to.
    Only that browser knows the random guest id, which proves ownership.

    Messages are moved before their conversations, so a run interrupted
    half-way can simply be repeated: unclaimed conversations are still
    found by guest id and their remaining messages still have no owner.
    """
    guest_id = str(body.guest_session_id)
    user_id = _user_id(user)
    try:
        rows = (
            supabase.table("conversations").select("id")
            .eq("guest_session_id", guest_id).is_("user_id", "null")
            .execute().data or []
        )
        ids = [r["id"] for r in rows]
        if ids:
            (supabase.table("messages").update({"user_id": user_id})
             .in_("conversation_id", ids).is_("user_id", "null").execute())
            (supabase.table("conversations").update({"user_id": user_id, "guest_session_id": None})
             .in_("id", ids).is_("user_id", "null").execute())
    except Exception as e:
        log_agent("DATABASE", f"Claim guest conversations failed: {e!r}")
        raise HTTPException(status_code=500, detail="Unable to save your guest chats to your account.")

    if ids:
        log_agent("CHAT", f"Moved {len(ids)} guest conversation(s) to user {user_id[:8]}")
    return {"success": True, "claimed": len(ids)}


def _client_message_metadata(messages: list[dict], user) -> None:
    """
    Make stored metadata safe and useful for the browser:
    - drop server paths written by older versions (image_path);
    - rebuild attachment references for older messages that only
      stored document_id / image_id, using rows the user owns.
    """
    user_id = _user_id(user)
    legacy_docs, legacy_images = set(), set()

    if is_guest(user):
        # Guests cannot attach files; only drop server-side fields.
        for m in messages:
            (m.get("metadata") or {}).pop("image_path", None)
        return

    for m in messages:
        meta = m.get("metadata") or {}
        meta.pop("image_path", None)
        m["metadata"] = meta
        if str(m.get("role", "")).upper() == "USER" and "attachments" not in meta and "context_file_ids" not in meta:
            if meta.get("document_id"):
                legacy_docs.add(meta["document_id"])
            if meta.get("image_id"):
                legacy_images.add(meta["image_id"])

    if not legacy_docs and not legacy_images:
        return

    try:
        docs = {
            d["id"]: d for d in (
                supabase.table("documents").select("id,filename,file_id")
                .in_("id", list(legacy_docs)).eq("user_id", user_id).execute().data or []
            )
        } if legacy_docs else {}
        images = owned_files(list(legacy_images), user_id)
    except Exception as e:
        log_agent("DATABASE", f"Legacy attachment lookup failed: {e}")
        return

    for m in messages:
        meta = m["metadata"]
        if "attachments" in meta or "context_file_ids" in meta or str(m.get("role", "")).upper() != "USER":
            continue
        rebuilt = []
        doc = docs.get(meta.get("document_id"))
        if doc:
            rebuilt.append({"file_id": doc.get("file_id"), "filename": doc["filename"],
                            "file_type": FileType.PDF, "mime_type": "application/pdf"})
        image = images.get(meta.get("image_id"))
        if image:
            rebuilt.append(attachment_reference(image))
        if rebuilt:
            meta["attachments"] = rebuilt


@app.get("/conversations/{conversation_id}/messages")
def get_conversation_messages(
    conversation_id: str,
    user=Depends(get_user_or_guest),
):
    if not _conversation_owned_by_user(conversation_id, user):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    try:
        query = (
            supabase
            .table("messages")
            .select("id,conversation_id,role,content,route,metadata,created_at")
            .eq("conversation_id", conversation_id)
        )
        result = _own_messages(query, user).order("created_at", desc=False).execute()
        messages = result.data or []
        _client_message_metadata(messages, user)
        return {
            "success": True,
            "conversation_id": conversation_id,
            "messages": messages,
        }
    except Exception as e:
        log_agent("DATABASE", f"Load messages failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to load conversation messages.")


@app.delete("/conversations/{conversation_id}")
def archive_conversation(
    conversation_id: str,
    user=Depends(get_user_or_guest),
):
    if not _conversation_owned_by_user(conversation_id, user):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    try:
        update = supabase.table("conversations").update({"is_archived": True}).eq("id", conversation_id)
        _own_conversations(update, user).execute()

        return {
            "success": True,
            "message": "Conversation archived.",
            "conversation_id": conversation_id,
        }
    except Exception as e:
        log_agent("DATABASE", f"Archive conversation failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to archive conversation.")


# ============================================================
# NORMAL CHAT (LangGraph)
# ============================================================

@app.post("/chat")
def chat(request: ChatRequest, user=Depends(get_current_user)):
    """
    Non-streaming chat through the LangGraph workflow. The graph takes
    one PDF and one image; the first of each attachment kind is used.
    """

    plan = _plan_attachments(request, user)

    conversation = _get_or_create_conversation(
        request.conversation_id,
        user,
        request.query,
    )
    conversation_id = str(conversation["id"])
    current_history = _get_conversation_history(conversation_id, user)

    user_message_id = _save_message(
        conversation_id,
        user,
        "USER",
        request.query,
        metadata=_message_metadata(plan, request),
    )

    initial_state = {
        "query": request.query,
        "route": "",
        "route_reason": "",
        "tool_result": "",
        "final_response": "",
        "error": "",
        "chat_history": current_history,
        "sources": [],

        # PDF RAG
        "document_id": plan.documents[0]["id"] if plan.documents else None,

        # Image OCR + Vision (server-resolved path only)
        "image_path": plan.images[0]["path"] if plan.images else None,
        "image_ocr_text": "",
        "image_vision_result": ""
    }

    try:
        result = chat_graph.invoke(initial_state)
    except Exception as e:
        log_agent("SYSTEM", f"Normal chat failed: {e!r}")
        log_activity(_user_id(user), "CHAT_ERROR", request.query, "FAILED", document_id=initial_state["document_id"])
        return {
            "success": False,
            "conversation_id": conversation_id,
            "query": request.query,
            "route": "",
            "route_reason": "",
            "response": "Sorry, something went wrong while processing your request.",
            "sources": [],
        }

    route = result.get("route", "")
    final_response = result.get("final_response", "")

    _set_message_route(user_message_id, route)

    _save_message(
        conversation_id,
        user,
        "ASSISTANT",
        final_response,
        route=route,
        metadata={
            "sources": result.get("sources", []),
            "route_reason": result.get("route_reason", ""),
        },
    )

    _update_conversation_title_if_needed(conversation_id, user, request.query)

    return {
        "success": True,
        "conversation_id": conversation_id,
        "query": request.query,
        "route": route,
        "route_reason": result.get("route_reason", ""),
        "response": final_response,
        "sources": result.get("sources", []),
    }


# ============================================================
# STREAMING CHAT
# ============================================================

SEARCH_AGENTS = {
    "WIKIPEDIA": (wikipedia_agent, "Reading Wikipedia"),
    "TAVILY": (tavily_agent, "Searching the web"),
    "ARXIV": (arxiv_agent, "Searching research papers"),
}


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


@app.post("/chat/stream")
def chat_stream(request: ChatRequest, http_request: Request, user=Depends(get_user_or_guest)):

    query = request.query
    user_id = _user_id(user)

    if is_guest(user):
        # Guests can chat, but files, images and voice need an account.
        if request.attachments or request.context_file_ids or request.document_id or request.image_id:
            raise HTTPException(status_code=401, detail="Please log in to upload PDFs and images.")
        if not guest_limits.allow(user.guest_id, guest_limits.client_ip(http_request)):
            raise HTTPException(
                status_code=429,
                detail="You've reached the guest chat limit for now. Log in to keep chatting.",
            )

    # Every file is resolved by ID and ownership before anything runs.
    plan = _plan_attachments(request, user)
    first_document_id = plan.documents[0]["id"] if plan.documents else None

    conversation = _get_or_create_conversation(
        request.conversation_id,
        user,
        query,
    )
    conversation_id = str(conversation["id"])

    current_history = _get_conversation_history(conversation_id, user)

    user_message_id = _unanswered_question_id(conversation_id, user, query) if request.retry else None

    if user_message_id:
        # The history already ends with this question; don't repeat it to the LLM.
        if current_history and current_history[-1]["role"] == "user" and current_history[-1]["content"] == query:
            current_history = current_history[:-1]
    else:
        user_message_id = _save_message(
            conversation_id,
            user,
            "USER",
            query,
            metadata=_message_metadata(plan, request),
        )

    def generate():

        route = ""

        try:

            # =================================================
            # STEP 1: ROUTER
            # =================================================

            yield _sse({"type": "status", "stage": "routing", "label": "Choosing the best route"})

            if plan.documents:
                route = "PDF_RAG"
                route_reason = (
                    "PDFs are attached, so the question will use PDF retrieval"
                    + (" together with image analysis." if plan.images else ".")
                )
            elif plan.images:
                route = "IMAGE"
                route_reason = "An image is attached, so OCR + Vision analysis will be used."
            else:
                decision = route_query(query)
                route = decision.route
                route_reason = decision.reason

            log_agent("ROUTER", f"Route selected: {route}")
            _set_message_route(user_message_id, route)

            yield _sse({"type": "route", "route": route, "route_reason": route_reason})

            # =================================================
            # STEP 2: TOOL EXECUTION
            # =================================================

            tool_parts = []
            sources = []
            error = ""
            pdf_context = ""

            if route in SEARCH_AGENTS:
                agent, label = SEARCH_AGENTS[route]
                yield _sse({"type": "status", "stage": "tool", "label": label})
                result = agent(query)
                tool_parts.append(result.get("content", ""))
                sources = result.get("sources", [])
                error = result.get("error", "")

            # ---------------- images (one or more) ----------------
            for index, image in enumerate(plan.images, start=1):
                suffix = f" ({index} of {len(plan.images)})" if len(plan.images) > 1 else ""
                set_file_status(image["file_id"], FileStatus.PROCESSING)
                try:
                    yield _sse({"type": "status", "stage": "ocr", "label": f"Reading text in the image{suffix}"})
                    ocr_result = run_ocr(image["path"], query)
                    yield _sse({"type": "status", "stage": "vision", "label": f"Analyzing the image{suffix}"})
                    vision_result = run_vision(image["path"], query)
                    result = combine(ocr_result, vision_result, image["filename"] if suffix else "")
                    _record_image_analysis(image["file_id"], result)
                except Exception as e:
                    result = {"error": str(e), "ocr_ok": False, "vision_ok": False}
                    _record_image_analysis(image["file_id"], result)

                if result.get("ocr_ok") or result.get("vision_ok"):
                    tool_parts.append(result["tool_result"])
                    if result.get("error"):
                        log_agent("IMAGE", f"Partial image analysis: {result['error']}")
                else:
                    error = result.get("error") or "Image analysis failed."

            # ---------------- PDFs (one or more) ----------------
            if plan.documents and not error:
                yield _sse({
                    "type": "status",
                    "stage": "retrieval",
                    "label": "Searching your PDF" + ("s" if len(plan.documents) > 1 else ""),
                })
                try:
                    # Retrieval only; the grounded answer is streamed below
                    # in a single LLM call.
                    result = retrieve_pdf_context(query, plan.documents, chat_history=current_history)
                    sources = result.get("sources", [])

                    if result.get("chunks"):
                        pdf_context = result["context"]
                    elif not plan.images:
                        # No relevant PDF information: do not let the LLM
                        # answer from general knowledge.
                        fallback = NOT_FOUND
                        yield _sse({"type": "content", "content": fallback})
                        _save_message(conversation_id, user, "ASSISTANT", fallback, route=route,
                                      metadata={"sources": [], "route_reason": route_reason})
                        yield _sse({"type": "sources", "sources": []})
                        yield _sse({"type": "done"})
                        return

                except Exception as e:
                    error = str(e)

            if route not in SEARCH_AGENTS and route not in ("DIRECT", "PDF_RAG", "IMAGE"):
                error = "Unable to determine the appropriate route."

            if error:
                log_agent("SYSTEM", f"Tool execution failed on {route}: {error}")
                log_activity(user_id, "CHAT_ERROR", query, "FAILED", route=route, document_id=first_document_id,
                             metadata={"stage": "tool"})
                yield _sse({
                    "type": "error",
                    "content": "The requested information service is temporarily unavailable. Please try again later.",
                })
                return

            # =================================================
            # STEP 3: GROQ STREAMING
            # =================================================

            yield _sse({"type": "status", "stage": "generating", "label": "Writing the answer"})

            full_response = ""

            if pdf_context:
                # PDF questions: one grounded call that answers only from
                # the retrieved pages (plus any attached image analysis).
                answer_stream = stream_pdf_answer(
                    query=query,
                    context=pdf_context,
                    chat_history=current_history,
                    image_context="\n\n".join(part for part in tool_parts if part),
                )
            else:
                answer_stream = stream_response(
                    query=query,
                    tool_result="\n\n".join(part for part in tool_parts if part),
                    chat_history=current_history
                )

            for chunk in answer_stream:
                if not chunk:
                    continue
                full_response += chunk
                yield _sse({"type": "content", "content": chunk})

            # =================================================
            # STEP 4: PERSIST CONVERSATION
            # =================================================

            _save_message(
                conversation_id,
                user,
                "ASSISTANT",
                full_response,
                route=route,
                metadata={
                    "sources": sources,
                    "route_reason": route_reason,
                },
            )

            _update_conversation_title_if_needed(conversation_id, user, query)

            yield _sse({"type": "sources", "sources": sources})
            yield _sse({"type": "done"})

        except Exception as e:
            status = getattr(e, "status_code", None)
            busy, too_large = status == 429, status == 413
            log_agent("SYSTEM", f"Unexpected streaming error: {e!r}")
            reason = {429: "llm_rate_limited", 413: "llm_request_too_large"}.get(status)
            log_activity(user_id, "CHAT_ERROR", query, "FAILED", route=route or None, document_id=first_document_id,
                         metadata={"stage": "stream", **({"reason": reason} if reason else {})})
            yield _sse({
                "type": "error",
                "content": (
                    "The AI service is busy right now. Please wait a few seconds and try again." if busy
                    else "That request was too long for the AI service. Try a shorter question or start a new chat."
                    if too_large else "Something went wrong while processing your request."
                ),
            })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Conversation-Id": conversation_id,
        }
    )


# ============================================================
# ACCOUNT / ADMIN API
# ============================================================

app.include_router(account_router)
app.include_router(admin_router)
