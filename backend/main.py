import io
import json
import queue
import threading
from pathlib import Path

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    Depends,
    Request
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
from agents.rag_agent import rag_agent
from agents.speech_agent import speech_to_text_agent

from admin.routes import router as admin_router
from auth.supabase_auth import get_current_user, is_admin
from config.settings import (
    ALLOWED_ORIGINS,
    APP_ENV,
    APP_VERSION,
    MAX_AUDIO_MB,
    MAX_IMAGE_MB,
    MAX_PDF_MB,
    SUPABASE_PUBLISHABLE_KEY,
    SUPABASE_URL,
)
from database.connection import supabase
from rag.pipeline import ingest_pdf
from services.activity import log_activity
from services.image_analysis import analyze_image
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
    title="LangGraph AI Chatbot",
    description="Full-stack AI chatbot using LangGraph, LangChain and Groq",
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
        allow_methods=["GET", "POST", "DELETE"],
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


@app.get("/")
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/admin")
def admin_page():
    # The page is static; every admin API call is authorized server-side.
    return FileResponse(FRONTEND_DIR / "admin.html")


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
        },
    }


# ============================================================
# SUPABASE AUTH
# ============================================================

@app.get("/auth/me")
def get_me(user=Depends(get_current_user)):
    """
    Return the currently authenticated Supabase user.
    """
    return {
        "success": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "created_at": user.created_at,
            "is_admin": is_admin(user),
        }
    }


# ============================================================
# REQUEST MODEL
# ============================================================

class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None

    # Optional PDF document ID (must belong to the user).
    document_id: str | None = None

    # Optional uploaded image ID returned by /upload/image
    # (must belong to the user). File paths are never accepted.
    image_id: str | None = None


# ============================================================
# PERSISTENT CHAT MEMORY / SUPABASE
# ============================================================

def _user_id(user) -> str:
    return str(user.id)


def _document_owned_by_user(document_id: str, user) -> bool:
    """
    Verify that the requested PDF belongs to the authenticated user.
    """
    try:
        result = (
            supabase
            .table("documents")
            .select("id")
            .eq("id", document_id)
            .eq("user_id", _user_id(user))
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        log_agent("DATABASE", f"Document ownership check failed: {e}")
        return False


def _resolve_owned_image(image_id: str, user) -> tuple[str, Path]:
    """
    Return (file_id, server path) for an image the user uploaded.
    Raises 404 for unknown, foreign, deleted or missing files.
    """
    try:
        result = (
            supabase
            .table("files")
            .select("id,storage_path")
            .eq("id", image_id)
            .eq("user_id", _user_id(user))
            .eq("file_type", "IMAGE")
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
    except Exception as e:
        log_agent("DATABASE", f"Image lookup failed: {e}")
        result = None

    path = resolve_inside_uploads(result.data[0]["storage_path"]) if result and result.data else None

    if not path:
        raise HTTPException(
            status_code=404,
            detail="The attached image could not be found. Please upload it again.",
        )

    return result.data[0]["id"], path


def _conversation_owned_by_user(conversation_id: str, user) -> bool:
    try:
        result = (
            supabase
            .table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("user_id", _user_id(user))
            .eq("is_archived", False)
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        log_agent("DATABASE", f"Conversation ownership check failed: {e}")
        return False


def _create_conversation(user, title: str = "New Chat") -> dict:
    result = (
        supabase
        .table("conversations")
        .insert({
            "user_id": _user_id(user),
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
        result = (
            supabase
            .table("conversations")
            .select("*")
            .eq("id", conversation_id)
            .eq("user_id", _user_id(user))
            .eq("is_archived", False)
            .limit(1)
            .execute()
        )

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
    result = (
        supabase
        .table("messages")
        .select("role,content,created_at")
        .eq("conversation_id", conversation_id)
        .eq("user_id", _user_id(user))
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

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
    result = (
        supabase
        .table("conversations")
        .select("title")
        .eq("id", conversation_id)
        .eq("user_id", _user_id(user))
        .limit(1)
        .execute()
    )

    if not result.data:
        return

    current_title = (result.data[0].get("title") or "").strip()

    if current_title and current_title.lower() != "new chat":
        return

    title = " ".join((query or "").strip().split())

    if len(title) > 60:
        title = title[:57].rstrip() + "..."

    supabase.table("conversations").update({
        "title": title or "New Chat"
    }).eq(
        "id", conversation_id
    ).eq(
        "user_id", _user_id(user)
    ).execute()


def _validate_chat_attachments(request: ChatRequest, user) -> Path | None:
    """
    Ownership checks for the optional PDF and image. Returns the
    server path of the image (if any).
    """
    if request.document_id and not _document_owned_by_user(request.document_id, user):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this PDF.",
        )

    if request.image_id:
        _, path = _resolve_owned_image(request.image_id, user)
        return path

    return None


def _record_image_analysis(image_id: str, result: dict) -> None:
    try:
        supabase.table("images").update({
            "ocr_text": (result.get("ocr_text") or "")[:20000],
            "vision_result": (result.get("vision_answer") or "")[:20000],
            "ocr_status": "COMPLETED" if result.get("ocr_ok") else "FAILED",
            "vision_status": "COMPLETED" if result.get("vision_ok") else "FAILED",
        }).eq("file_id", image_id).execute()
    except Exception as e:
        log_agent("DATABASE", f"Image status update failed: {e}")


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
    "extracting": "Extracting text",
    "chunking": "Chunking",
    "embedding": "Creating embeddings",
    "indexing": "Indexing",
}


@app.post("/upload/pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    stream: bool = False,
    user=Depends(get_current_user),
):
    """
    Upload a PDF and process it through the RAG pipeline.

    With ?stream=true the response is newline-delimited JSON with the
    real ingestion stages followed by a final "done" or "error" event.
    """

    original_filename = safe_display_name(file.filename)

    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    content = await _read_upload(file, MAX_PDF_MB, "PDF")

    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="The uploaded file does not appear to be a valid PDF.")

    file_path = save_upload(PDF_UPLOAD_DIR, content, ".pdf")
    user_id = _user_id(user)

    log_agent("PDF", f"Received {original_filename} ({len(content)} bytes)")

    def run_ingestion(progress=None) -> str:
        try:
            document_id = ingest_pdf(
                file_path=str(file_path),
                filename=original_filename,
                user_id=user_id,
                progress=progress,
            )
            log_agent("PDF", f"PDF ingestion completed: {document_id}")
            return document_id
        except Exception as e:
            log_agent("PDF", f"PDF processing failed: {e!r}")
            delete_stored_file(str(file_path))
            log_activity(user_id, "PDF_UPLOAD_FAILED", original_filename, "FAILED", file_name=original_filename)
            raise

    def success_payload(document_id: str) -> dict:
        return {
            "success": True,
            "message": "PDF uploaded and processed successfully.",
            "document_id": document_id,
            "filename": original_filename,
            "file_size": len(content),
        }

    failure_message = "We couldn't process this PDF. Please try another file."

    if not stream:
        try:
            return success_payload(run_ingestion())
        except Exception:
            raise HTTPException(status_code=422, detail=failure_message)

    events: queue.Queue = queue.Queue()

    def worker():
        def progress(stage, details=None):
            events.put({"type": "stage", "stage": stage, "label": PDF_STAGE_LABELS.get(stage, stage), **(details or {})})
        try:
            events.put({"type": "done", **success_payload(run_ingestion(progress))})
        except Exception:
            events.put({"type": "error", "detail": failure_message})

    def event_stream():
        yield json.dumps({"type": "stage", "stage": "uploaded", "label": PDF_STAGE_LABELS["uploaded"]}) + "\n"
        threading.Thread(target=worker, daemon=True).start()
        while True:
            event = events.get()
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
    Upload an image for OCR + Vision analysis. Returns an image_id
    that only this user can attach to a chat message.
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
        row = supabase.table("files").insert({
            "user_id": user_id,
            "filename": original_filename,
            "storage_path": str(file_path),
            "file_type": "IMAGE",
            "mime_type": mime_type,
            "file_size": len(content),
            "status": "UPLOADED",
        }).execute().data[0]

        supabase.table("images").insert({
            "file_id": row["id"],
            "ocr_status": "PENDING",
            "vision_status": "PENDING",
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
        "image_id": row["id"],
        "filename": original_filename,
        "file_size": len(content),
    }


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
def list_conversations(user=Depends(get_current_user)):
    try:
        result = (
            supabase
            .table("conversations")
            .select("id,title,is_archived,created_at,updated_at,last_message_at")
            .eq("user_id", _user_id(user))
            .eq("is_archived", False)
            .order("updated_at", desc=True)
            .execute()
        )
        return {"success": True, "conversations": result.data or []}
    except Exception as e:
        log_agent("DATABASE", f"List conversations failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to load conversations.")


@app.post("/conversations")
def create_conversation(
    body: ConversationCreate | None = None,
    user=Depends(get_current_user),
):
    try:
        conversation = _create_conversation(user, body.title if body else "New Chat")
        return {"success": True, "conversation": conversation}
    except Exception as e:
        log_agent("DATABASE", f"Create conversation failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to create conversation.")


@app.get("/conversations/{conversation_id}/messages")
def get_conversation_messages(
    conversation_id: str,
    user=Depends(get_current_user),
):
    if not _conversation_owned_by_user(conversation_id, user):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    try:
        result = (
            supabase
            .table("messages")
            .select("id,conversation_id,role,content,route,metadata,created_at")
            .eq("conversation_id", conversation_id)
            .eq("user_id", _user_id(user))
            .order("created_at", desc=False)
            .execute()
        )
        return {
            "success": True,
            "conversation_id": conversation_id,
            "messages": result.data or [],
        }
    except Exception as e:
        log_agent("DATABASE", f"Load messages failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to load conversation messages.")


@app.delete("/conversations/{conversation_id}")
def archive_conversation(
    conversation_id: str,
    user=Depends(get_current_user),
):
    if not _conversation_owned_by_user(conversation_id, user):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    try:
        supabase.table("conversations").update({
            "is_archived": True,
        }).eq(
            "id", conversation_id
        ).eq(
            "user_id", _user_id(user)
        ).execute()

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

    image_path = _validate_chat_attachments(request, user)

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
        metadata={
            "document_id": request.document_id,
            "image_id": request.image_id,
        },
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
        "document_id": request.document_id,

        # Image OCR + Vision (server-resolved path only)
        "image_path": str(image_path) if image_path else None,
        "image_ocr_text": "",
        "image_vision_result": ""
    }

    try:
        result = chat_graph.invoke(initial_state)
    except Exception as e:
        log_agent("SYSTEM", f"Normal chat failed: {e!r}")
        log_activity(_user_id(user), "CHAT_ERROR", request.query, "FAILED", document_id=request.document_id)
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
def chat_stream(request: ChatRequest, user=Depends(get_current_user)):

    query = request.query
    document_id = request.document_id
    user_id = _user_id(user)

    # Never allow another user's PDF or image into the workflow.
    image_path = _validate_chat_attachments(request, user)

    conversation = _get_or_create_conversation(
        request.conversation_id,
        user,
        query,
    )
    conversation_id = str(conversation["id"])

    current_history = _get_conversation_history(conversation_id, user)

    user_message_id = _save_message(
        conversation_id,
        user,
        "USER",
        query,
        metadata={
            "document_id": document_id,
            "image_id": request.image_id,
        },
    )

    def generate():

        route = ""

        try:

            # =================================================
            # STEP 1: ROUTER
            # =================================================

            yield _sse({"type": "status", "stage": "routing", "label": "Choosing the best route"})

            if document_id:
                route = "PDF_RAG"
                route_reason = "An uploaded PDF is attached to this request, so the question will use PDF retrieval."
            elif image_path:
                route = "IMAGE"
                route_reason = "An uploaded image is attached to this request, so OCR + Vision analysis will be used."
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

            tool_result = ""
            sources = []
            error = ""

            if route == "DIRECT":
                pass

            elif route in SEARCH_AGENTS:
                agent, label = SEARCH_AGENTS[route]
                yield _sse({"type": "status", "stage": "tool", "label": label})
                result = agent(query)
                tool_result = result.get("content", "")
                sources = result.get("sources", [])
                error = result.get("error", "")

            elif route == "PDF_RAG":
                yield _sse({"type": "status", "stage": "retrieval", "label": "Searching your PDF"})
                try:
                    result = rag_agent(query=query, document_id=document_id)
                    tool_result = result.get("answer", "")
                    sources = result.get("sources", [])

                    # No relevant PDF information: do not let the LLM
                    # answer from general knowledge.
                    if not result.get("chunks"):
                        fallback = "I could not find this information in the uploaded PDF."
                        yield _sse({"type": "content", "content": fallback})
                        _save_message(conversation_id, user, "ASSISTANT", fallback, route=route,
                                      metadata={"sources": [], "route_reason": route_reason})
                        yield _sse({"type": "sources", "sources": []})
                        yield _sse({"type": "done"})
                        return

                except Exception as e:
                    error = str(e)

            elif route == "IMAGE":
                yield _sse({"type": "status", "stage": "image", "label": "Reading text and analyzing the image"})
                try:
                    result = analyze_image(str(image_path), query)
                    _record_image_analysis(request.image_id, result)
                    tool_result = result["tool_result"]
                    error = result["error"]
                except Exception as e:
                    error = str(e)

            else:
                error = "Unable to determine the appropriate route."

            if error:
                log_agent("SYSTEM", f"Tool execution failed on {route}: {error}")
                log_activity(user_id, "CHAT_ERROR", query, "FAILED", route=route, document_id=document_id,
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

            for chunk in stream_response(
                query=query,
                tool_result=tool_result,
                chat_history=current_history
            ):
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
            log_agent("SYSTEM", f"Unexpected streaming error: {e!r}")
            log_activity(user_id, "CHAT_ERROR", query, "FAILED", route=route or None, document_id=document_id,
                         metadata={"stage": "stream"})
            yield _sse({
                "type": "error",
                "content": "Something went wrong while processing your request.",
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
# ADMIN API
# ============================================================

app.include_router(admin_router)
