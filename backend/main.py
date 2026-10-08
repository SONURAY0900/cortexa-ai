from pathlib import Path
import json
import uuid

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    Depends
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from graph.workflow import chat_graph
from graph.router import route_query

from agents.llm_agent import stream_response
from agents.wikipedia_agent import wikipedia_agent
from agents.tavily_agent import tavily_agent
from agents.arxiv_agent import arxiv_agent
from agents.rag_agent import rag_agent
from agents.ocr_agent import ocr_agent
from agents.vision_agent import vision_agent
from agents.speech_agent import speech_to_text_agent

from rag.pipeline import ingest_pdf
from utils.logger import log_agent
from auth.supabase_auth import get_current_user
from config.supabase_client import get_supabase

# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="LangGraph AI Chatbot",
    description="Full-stack AI chatbot using LangGraph, LangChain and Groq",
    version="1.0.0"
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# PROJECT DIRECTORIES
# ============================================================

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent

FRONTEND_DIR = PROJECT_DIR / "frontend"

PDF_UPLOAD_DIR = BACKEND_DIR / "uploads" / "pdfs"
IMAGE_UPLOAD_DIR = BACKEND_DIR / "uploads" / "images"
AUDIO_UPLOAD_DIR = BACKEND_DIR / "uploads" / "audio"

PDF_UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

IMAGE_UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

AUDIO_UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# FRONTEND
# ============================================================

app.mount(
    "/frontend",
    StaticFiles(directory=FRONTEND_DIR),
    name="frontend"
)


@app.get("/")
def home():
    return FileResponse(
        FRONTEND_DIR / "index.html"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }


# ============================================================
# SUPABASE AUTH
# ============================================================

@app.get("/auth/me")
def get_me(user=Depends(get_current_user)):
    """
    Return the currently authenticated Supabase user.

    Frontend sends:
        Authorization: Bearer <supabase_access_token>
    """
    return {
        "success": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "created_at": user.created_at
        }
    }


@app.get("/auth/test")
def auth_test(user=Depends(get_current_user)):
    """
    Protected endpoint used to verify that the
    Supabase session token is accepted by FastAPI.
    """
    return {
        "success": True,
        "message": "Authentication successful.",
        "user_id": user.id,
        "email": user.email
    }


# ============================================================
# REQUEST MODEL
# ============================================================

class ChatRequest(BaseModel):
    query: str
    conversation_id: str | None = None

    # Optional PDF document ID.
    # If present, the question can be answered using PDF RAG.
    document_id: str | None = None

    # Optional uploaded image path.
    # If present, the image workflow uses OCR + Vision.
    image_path: str | None = None


# ============================================================
# PERSISTENT CHAT MEMORY / SUPABASE
# ============================================================

def _supabase():
    return get_supabase()


def _user_id(user) -> str:
    return str(user.id)


def _document_owned_by_user(document_id: str, user) -> bool:
    """
    Verify that the requested PDF belongs to the authenticated user.
    """
    try:
        result = (
            _supabase()
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


def _conversation_owned_by_user(conversation_id: str, user) -> bool:
    try:
        result = (
            _supabase()
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
        _supabase()
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
            _supabase()
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
        _supabase()
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
):
    if not content:
        return

    _supabase().table("messages").insert({
        "conversation_id": conversation_id,
        "user_id": _user_id(user),
        "role": role.upper(),
        "content": content,
        "route": route,
        "metadata": metadata or {},
    }).execute()


def _update_conversation_title_if_needed(
    conversation_id: str,
    user,
    query: str,
):
    result = (
        _supabase()
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

    _supabase().table("conversations").update({
        "title": title or "New Chat"
    }).eq(
        "id", conversation_id
    ).eq(
        "user_id", _user_id(user)
    ).execute()


# ============================================================
# PDF UPLOAD
# ============================================================

@app.post("/upload/pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """
    Upload a PDF and process it through the RAG pipeline.

    Flow:

    PDF
      ↓
    Save locally
      ↓
    Extract text
      ↓
    Clean text
      ↓
    Chunk text
      ↓
    Generate embeddings
      ↓
    Store in Supabase
      ↓
    Return document_id
    """

    # ========================================================
    # 1. Validate filename
    # ========================================================

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No file selected."
        )

    # ========================================================
    # 2. Validate extension
    # ========================================================

    original_filename = Path(file.filename).name

    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed."
        )

    # ========================================================
    # 3. Validate MIME type
    # ========================================================

    # Some browsers/Swagger configurations may send
    # application/octet-stream instead of application/pdf.
    #
    # Therefore we don't reject the file only because of
    # the MIME type.
    # ========================================================

    log_agent(
        "PDF",
        f"Received file: {original_filename}"
    )

    log_agent(
        "PDF",
        f"Content type: {file.content_type}"
    )

    # ========================================================
    # 4. Read uploaded file
    # ========================================================

    try:

        file_content = await file.read()

    except Exception as e:

        log_agent(
            "PDF",
            f"Unable to read uploaded file: {str(e)}"
        )

        raise HTTPException(
            status_code=400,
            detail=f"Unable to read uploaded file: {str(e)}"
        )

    # ========================================================
    # 5. Check file size
    # ========================================================

    file_size = len(file_content)

    log_agent(
        "PDF",
        f"Uploaded file size: {file_size} bytes"
    )

    if file_size == 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Uploaded PDF is empty. "
                "Please select a valid PDF file and try again."
            )
        )

    # ========================================================
    # 6. Check PDF signature
    # ========================================================
    #
    # A real PDF normally starts with:
    #
    # %PDF-
    #
    # ========================================================

    if not file_content.startswith(b"%PDF-"):

        raise HTTPException(
            status_code=400,
            detail=(
                "The uploaded file does not appear to be a valid PDF."
            )
        )

    # ========================================================
    # 7. Create unique filename
    # ========================================================

    unique_filename = (
        f"{uuid.uuid4().hex}_{original_filename}"
    )

    file_path = PDF_UPLOAD_DIR / unique_filename

    try:

        # ====================================================
        # 8. Save PDF locally
        # ====================================================

        with open(
            file_path,
            "wb"
        ) as pdf_file:

            pdf_file.write(
                file_content
            )

        log_agent(
            "PDF",
            f"PDF saved: {file_path}"
        )

        # ====================================================
        # 9. Verify saved file
        # ====================================================

        if not file_path.exists():

            raise RuntimeError(
                "PDF file could not be saved."
            )

        saved_size = file_path.stat().st_size

        log_agent(
            "PDF",
            f"Saved PDF size: {saved_size} bytes"
        )

        if saved_size == 0:

            raise RuntimeError(
                "Saved PDF is empty."
            )

        # ====================================================
        # 10. Run RAG ingestion
        # ====================================================

        log_agent(
            "PDF",
            "Starting PDF ingestion..."
        )

        document_id = ingest_pdf(
            file_path=str(file_path),
            filename=original_filename
        )

        # ====================================================
        # 10A. Attach PDF ownership to authenticated user
        # ====================================================
        # ingest_pdf() creates the document/chunks. The ownership
        # fields are assigned here because the upload endpoint
        # knows the authenticated Supabase user.
        ownership_update = (
            _supabase()
            .table("documents")
            .update({
                "user_id": _user_id(user),
                "processing_status": "COMPLETED",
            })
            .eq("id", document_id)
            .select("id,user_id,processing_status")
            .execute()
        )

        if not ownership_update.data:
            raise RuntimeError(
                "PDF was ingested, but document ownership could not be saved."
            )

        log_agent(
            "PDF",
            f"PDF ingestion completed: {document_id}"
        )
        log_agent(
            "PDF",
            f"PDF ownership assigned to user: {_user_id(user)}"
        )

        # ====================================================
        # 11. Return response
        # ====================================================

        return {
            "success": True,
            "message": (
                "PDF uploaded and processed successfully."
            ),
            "document_id": document_id,
            "filename": original_filename,
            "file_size": saved_size
        }

    except Exception as e:

        log_agent(
            "PDF",
            f"PDF processing failed: {str(e)}"
        )

        # ====================================================
        # Delete local file if processing fails
        # ====================================================

        if file_path.exists():

            file_path.unlink(
                missing_ok=True
            )

        raise HTTPException(
            status_code=500,
            detail=f"PDF processing failed: {str(e)}"
        )


# ============================================================
# IMAGE UPLOAD
# ============================================================

@app.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...)
):
    """
    Upload an image for OCR + Vision analysis.

    Flow:

    Image
      ↓
    Validate
      ↓
    Save locally
      ↓
    Return image_path
    """

    # ========================================================
    # 1. Validate filename
    # ========================================================

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No file selected."
        )

    original_filename = Path(file.filename).name

    # ========================================================
    # 2. Validate image extension
    # ========================================================

    allowed_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp"
    }

    extension = Path(
        original_filename
    ).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only JPG, JPEG, PNG, WEBP and BMP "
                "images are allowed."
            )
        )

    # ========================================================
    # 3. Validate MIME type when available
    # ========================================================

    allowed_mime_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/bmp"
    }

    if (
        file.content_type
        and file.content_type not in allowed_mime_types
    ):
        raise HTTPException(
            status_code=400,
            detail="Unsupported image MIME type."
        )

    log_agent(
        "IMAGE",
        f"Received file: {original_filename}"
    )

    log_agent(
        "IMAGE",
        f"Content type: {file.content_type}"
    )

    # ========================================================
    # 4. Read uploaded file
    # ========================================================

    try:

        file_content = await file.read()

    except Exception as e:

        log_agent(
            "IMAGE",
            f"Unable to read uploaded file: {str(e)}"
        )

        raise HTTPException(
            status_code=400,
            detail=(
                f"Unable to read uploaded file: {str(e)}"
            )
        )

    # ========================================================
    # 5. Check file size
    # ========================================================

    file_size = len(file_content)

    log_agent(
        "IMAGE",
        f"Uploaded image size: {file_size} bytes"
    )

    if file_size == 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Uploaded image is empty. "
                "Please select a valid image."
            )
        )

    # Keep upload size reasonable before sending it to
    # OCR.space or the Vision model.
    MAX_IMAGE_SIZE = 10 * 1024 * 1024

    if file_size > MAX_IMAGE_SIZE:

        raise HTTPException(
            status_code=400,
            detail=(
                "Image is too large. "
                "Maximum upload size is 10 MB."
            )
        )

    # ========================================================
    # 6. Create unique filename
    # ========================================================

    unique_filename = (
        f"{uuid.uuid4().hex}_{original_filename}"
    )

    file_path = IMAGE_UPLOAD_DIR / unique_filename

    try:

        # ====================================================
        # 7. Save image locally
        # ====================================================

        with open(
            file_path,
            "wb"
        ) as image_file:

            image_file.write(
                file_content
            )

        log_agent(
            "IMAGE",
            f"Image saved: {file_path}"
        )

        # ====================================================
        # 8. Verify saved file
        # ====================================================

        if not file_path.exists():

            raise RuntimeError(
                "Image file could not be saved."
            )

        saved_size = file_path.stat().st_size

        log_agent(
            "IMAGE",
            f"Saved image size: {saved_size} bytes"
        )

        return {
            "success": True,
            "message": (
                "Image uploaded successfully."
            ),
            "image_path": str(file_path),
            "filename": original_filename,
            "file_size": saved_size
        }

    except Exception as e:

        log_agent(
            "IMAGE",
            f"Image upload failed: {str(e)}"
        )

        if file_path.exists():

            file_path.unlink(
                missing_ok=True
            )

        raise HTTPException(
            status_code=500,
            detail=f"Image upload failed: {str(e)}"
        )


# ============================================================
# VOICE TO TEXT
# ============================================================

@app.post("/voice/transcribe")
async def transcribe_voice(
    file: UploadFile = File(...),
    language: str | None = None
):
    """
    Transcribe an uploaded audio file using Groq Whisper Large V3 Turbo.

    Flow:

    Audio
      ↓
    Validate
      ↓
    Save temporarily
      ↓
    Groq Whisper Large V3 Turbo
      ↓
    Return transcript

    The frontend can then send the returned text to /chat/stream.
    """

    # ========================================================
    # 1. Validate filename
    # ========================================================

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No audio file selected."
        )

    original_filename = Path(file.filename).name
    extension = Path(original_filename).suffix.lower()

    allowed_extensions = {
        ".flac",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpga",
        ".m4a",
        ".ogg",
        ".wav",
        ".webm"
    }

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported audio format. Allowed formats: "
                "flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm."
            )
        )

    log_agent(
        "VOICE",
        f"Received audio: {original_filename}"
    )

    log_agent(
        "VOICE",
        f"Content type: {file.content_type}"
    )

    # ========================================================
    # 2. Read audio
    # ========================================================

    try:
        audio_content = await file.read()
    except Exception as e:
        log_agent(
            "VOICE",
            f"Unable to read audio: {str(e)}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Unable to read audio file: {str(e)}"
        )

    # ========================================================
    # 3. Validate size
    # ========================================================

    audio_size = len(audio_content)

    # Keep this within Groq's free-tier direct upload limit.
    max_audio_size = 25 * 1024 * 1024

    if audio_size == 0:
        raise HTTPException(
            status_code=400,
            detail="The uploaded audio file is empty."
        )

    if audio_size > max_audio_size:
        raise HTTPException(
            status_code=400,
            detail="Audio file is too large. Maximum allowed size is 25 MB."
        )

    log_agent(
        "VOICE",
        f"Audio size: {audio_size} bytes"
    )

    # ========================================================
    # 4. Transcribe using Groq Whisper
    # ========================================================

    try:
        result = speech_to_text_agent(
            audio_bytes=audio_content,
            filename=original_filename,
            language=language
        )

        if result.get("error"):
            log_agent(
                "VOICE",
                f"Transcription failed: {result['error']}"
            )
            raise HTTPException(
                status_code=500,
                detail="Speech transcription failed."
            )

        transcript = result.get("text", "").strip()

        if not transcript:
            log_agent(
                "VOICE",
                "No speech detected."
            )

            return {
                "success": True,
                "filename": original_filename,
                "language": language,
                "text": "",
                "message": "No speech was detected in the audio."
            }

        log_agent(
            "VOICE",
            "Speech transcription completed successfully."
        )

        return {
            "success": True,
            "filename": original_filename,
            "language": language,
            "text": transcript
        }

    except HTTPException:
        raise

    except Exception as e:
        log_agent(
            "VOICE",
            f"Voice transcription failed: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail="Speech transcription service is temporarily unavailable."
        )


# ============================================================
# CONVERSATIONS / CHAT HISTORY
# ============================================================

@app.get("/conversations")
def list_conversations(user=Depends(get_current_user)):
    try:
        result = (
            _supabase()
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
    title: str = "New Chat",
    user=Depends(get_current_user),
):
    try:
        conversation = _create_conversation(user, title)
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
            _supabase()
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
        _supabase().table("conversations").update({
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
# NORMAL CHAT
# ============================================================

@app.post("/chat")
def chat(request: ChatRequest, user=Depends(get_current_user)):

    if request.document_id and not _document_owned_by_user(
        request.document_id,
        user,
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this PDF.",
        )

    conversation = _get_or_create_conversation(
        request.conversation_id,
        user,
        request.query,
    )
    conversation_id = str(conversation["id"])
    current_history = _get_conversation_history(
        conversation_id,
        user,
    )

    _save_message(
        conversation_id,
        user,
        "USER",
        request.query,
        metadata={
            "document_id": request.document_id,
            "image_path": request.image_path,
        },
    )

    log_agent(
        "SYSTEM",
        f"Processing normal chat request: {request.query}"
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

        # Image OCR + Vision
        "image_path": request.image_path,
        "image_ocr_text": "",
        "image_vision_result": ""
    }

    try:

        log_agent(
            "SYSTEM",
            "Starting LangGraph execution"
        )

        result = chat_graph.invoke(
            initial_state
        )

        log_agent(
            "SYSTEM",
            "LangGraph execution completed"
        )

        # ----------------------------------------------------
        # Persist assistant response
        # ----------------------------------------------------

        final_response = result.get("final_response", "")

        _save_message(
            conversation_id,
            user,
            "ASSISTANT",
            final_response,
            route=result.get("route", ""),
            metadata={
                "sources": result.get("sources", []),
                "route_reason": result.get("route_reason", ""),
            },
        )

        _update_conversation_title_if_needed(
            conversation_id,
            user,
            request.query,
        )

        log_agent(
            "DATABASE",
            f"Saved conversation {conversation_id}"
        )

        return {
            "success": True,
            "conversation_id": conversation_id,
            "query": request.query,

            "route": result.get(
                "route",
                ""
            ),

            "route_reason": result.get(
                "route_reason",
                ""
            ),

            "response": result.get(
                "final_response",
                ""
            ),

            "sources": result.get(
                "sources",
                []
            ),

            "error": result.get(
                "error",
                ""
            )
        }

    except Exception as e:

        log_agent(
            "SYSTEM",
            f"Normal chat failed: {str(e)}"
        )

        return {
            "success": False,
            "query": request.query,
            "route": "",
            "route_reason": "",
            "response": (
                "Sorry, something went wrong "
                "while processing your request."
            ),
            "sources": [],
            "error": str(e)
        }


# ============================================================
# STREAMING CHAT
# ============================================================

@app.post("/chat/stream")
def chat_stream(request: ChatRequest, user=Depends(get_current_user)):

    query = request.query
    document_id = request.document_id

    # Never allow a document_id from another user to enter
    # the conversation/RAG workflow.
    if document_id and not _document_owned_by_user(
        document_id,
        user,
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this PDF.",
        )

    conversation = _get_or_create_conversation(
        request.conversation_id,
        user,
        query,
    )
    conversation_id = str(conversation["id"])

    current_history = _get_conversation_history(
        conversation_id,
        user,
    )

    _save_message(
        conversation_id,
        user,
        "USER",
        query,
        metadata={
            "document_id": request.document_id,
            "image_path": request.image_path,
        },
    )


    image_path = request.image_path

    # ========================================================
    # STREAM GENERATOR
    # ========================================================

    def generate():

        try:

            # =================================================
            # SYSTEM START
            # =================================================

            log_agent(
                "SYSTEM",
                f"Processing query: {query}"
            )

            # =================================================
            # STEP 1: ROUTER
            # =================================================
            #
            # If a PDF document_id is supplied, we know that
            # this conversation currently has an uploaded PDF.
            #
            # Therefore we use PDF_RAG directly.
            #
            # Without document_id, the existing router handles:
            #
            # DIRECT
            # WIKIPEDIA
            # TAVILY
            # ARXIV
            #
            # =================================================

            if document_id:

                route = "PDF_RAG"

                route_reason = (
                    "An uploaded PDF is attached to this request, "
                    "so the question will use PDF retrieval."
                )

                log_agent(
                    "ROUTER",
                    "PDF document detected. Selecting PDF_RAG."
                )

            elif image_path:

                route = "IMAGE"

                route_reason = (
                    "An uploaded image is attached to this request, "
                    "so OCR + Vision analysis will be used."
                )

                log_agent(
                    "ROUTER",
                    "Image detected. Selecting IMAGE."
                )

            else:

                log_agent(
                    "ROUTER",
                    "Starting route analysis"
                )

                decision = route_query(
                    query
                )

                route = decision.route

                route_reason = decision.reason

                log_agent(
                    "ROUTER",
                    f"Route selected: {route}"
                )

            # =================================================
            # SEND ROUTE TO FRONTEND
            # =================================================

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "route",
                        "route": route,
                        "route_reason": route_reason
                    }
                )
                + "\n\n"
            )

            # =================================================
            # STEP 2: TOOL EXECUTION
            # =================================================

            tool_result = ""

            sources = []

            error = ""

            # =================================================
            # DIRECT
            # =================================================

            if route == "DIRECT":

                log_agent(
                    "DIRECT",
                    "No external tool required"
                )

                tool_result = ""

            # =================================================
            # WIKIPEDIA
            # =================================================

            elif route == "WIKIPEDIA":

                log_agent(
                    "WIKIPEDIA",
                    "Starting Wikipedia agent"
                )

                result = wikipedia_agent(
                    query
                )

                tool_result = result.get(
                    "content",
                    ""
                )

                sources = result.get(
                    "sources",
                    []
                )

                error = result.get(
                    "error",
                    ""
                )

                if error:

                    log_agent(
                        "WIKIPEDIA",
                        f"Agent failed: {error}"
                    )

                else:

                    log_agent(
                        "WIKIPEDIA",
                        "Agent execution successful"
                    )

            # =================================================
            # TAVILY
            # =================================================

            elif route == "TAVILY":

                log_agent(
                    "TAVILY",
                    "Starting Tavily agent"
                )

                result = tavily_agent(
                    query
                )

                tool_result = result.get(
                    "content",
                    ""
                )

                sources = result.get(
                    "sources",
                    []
                )

                error = result.get(
                    "error",
                    ""
                )

                if error:

                    log_agent(
                        "TAVILY",
                        f"Agent failed: {error}"
                    )

                else:

                    log_agent(
                        "TAVILY",
                        "Agent execution successful"
                    )

            # =================================================
            # ARXIV
            # =================================================

            elif route == "ARXIV":

                log_agent(
                    "ARXIV",
                    "Starting arXiv agent"
                )

                result = arxiv_agent(
                    query
                )

                tool_result = result.get(
                    "content",
                    ""
                )

                sources = result.get(
                    "sources",
                    []
                )

                error = result.get(
                    "error",
                    ""
                )

                if error:

                    log_agent(
                        "ARXIV",
                        f"Agent failed: {error}"
                    )

                else:

                    log_agent(
                        "ARXIV",
                        "Agent execution successful"
                    )

            # =================================================
            # PDF RAG
            # =================================================

            elif route == "PDF_RAG":

                log_agent(
                    "PDF_RAG",
                    "Starting PDF RAG agent"
                )

                try:

                    result = rag_agent(
                        query=query,
                        document_id=document_id,
                        match_count=5
                    )

                    tool_result = result.get(
                        "answer",
                        ""
                    )

                    sources = result.get(
                        "sources",
                        []
                    )

                    error = ""

                    log_agent(
                        "PDF_RAG",
                        "PDF RAG execution successful"
                    )

                    # =================================================
                    # NO RELEVANT PDF INFORMATION FOUND
                    # =================================================
                    # Do NOT send unsupported PDF questions to Groq.
                    # Otherwise Groq may answer from general knowledge.
                    # =================================================

                    if not result.get("chunks"):

                        log_agent(
                            "PDF_RAG",
                            "No relevant PDF chunks found. "
                            "Returning grounded fallback response."
                        )

                        fallback_message = (
                            "I could not find this information "
                            "in the uploaded PDF."
                        )

                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "type": "content",
                                    "content": fallback_message
                                }
                            )
                            + "\n\n"
                        )

                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "type": "sources",
                                    "sources": []
                                }
                            )
                            + "\n\n"
                        )

                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "type": "done"
                                }
                            )
                            + "\n\n"
                        )

                        return

                except Exception as e:

                    error = str(e)

                    log_agent(
                        "PDF_RAG",
                        f"PDF RAG failed: {error}"
                    )

            # =================================================
            # IMAGE
            # =================================================

            elif route == "IMAGE":

                log_agent(
                    "IMAGE",
                    "Starting OCR + Vision analysis"
                )

                try:

                    if not image_path:

                        error = (
                            "No image is attached to this request."
                        )

                    else:

                        # ---------------------------------------------
                        # OCR
                        # ---------------------------------------------

                        log_agent(
                            "OCR",
                            "Starting OCR agent"
                        )

                        ocr_result = ocr_agent(
                            image_path=image_path,
                            query=query
                        )

                        ocr_text = ocr_result.get(
                            "ocr_text",
                            ""
                        )

                        ocr_error = ocr_result.get(
                            "error",
                            ""
                        )

                        # ---------------------------------------------
                        # VISION
                        # ---------------------------------------------

                        log_agent(
                            "VISION",
                            "Starting Vision agent"
                        )

                        vision_result = vision_agent(
                            image_path=image_path,
                            query=query
                        )

                        vision_answer = vision_result.get(
                            "answer",
                            ""
                        )

                        vision_error = vision_result.get(
                            "error",
                            ""
                        )

                        # ---------------------------------------------
                        # Combine OCR + Vision
                        # ---------------------------------------------

                        tool_result = f"""
IMAGE ANALYSIS RESULT

OCR TEXT:
{ocr_text}

VISION ANALYSIS:
{vision_answer}
"""

                        errors = []

                        if ocr_error:

                            errors.append(
                                f"OCR error: {ocr_error}"
                            )

                        if vision_error:

                            errors.append(
                                f"Vision error: {vision_error}"
                            )

                        error = "\n".join(errors)

                        if error:

                            log_agent(
                                "IMAGE",
                                f"Image analysis failed: {error}"
                            )

                        else:

                            log_agent(
                                "IMAGE",
                                "OCR + Vision analysis successful"
                            )

                except Exception as e:

                    error = str(e)

                    log_agent(
                        "IMAGE",
                        f"Image analysis failed: {error}"
                    )

            # =================================================
            # UNKNOWN ROUTE
            # =================================================

            else:

                error = (
                    "Unable to determine the "
                    "appropriate route."
                )

                log_agent(
                    "SYSTEM",
                    error
                )

            # =================================================
            # TOOL FAILED
            # =================================================

            if error:

                log_agent(
                    "SYSTEM",
                    "Tool execution failed"
                )

                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "error",
                            "content": (
                                "The requested information "
                                "service is temporarily "
                                "unavailable. Please try "
                                "again later."
                            )
                        }
                    )
                    + "\n\n"
                )

                return

            # =================================================
            # STEP 3: GROQ STREAMING
            # =================================================

            log_agent(
                "LLM",
                "Starting Groq response streaming"
            )

            full_response = ""

            chunk_count = 0

            for chunk in stream_response(
                query=query,
                tool_result=tool_result,
                chat_history=current_history
            ):

                if not chunk:
                    continue

                full_response += chunk

                chunk_count += 1

                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "content",
                            "content": chunk
                        }
                    )
                    + "\n\n"
                )

            log_agent(
                "LLM",
                f"Streaming completed: {chunk_count} chunks"
            )

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

            _update_conversation_title_if_needed(
                conversation_id,
                user,
                query,
            )

            log_agent(
                "DATABASE",
                f"Conversation {conversation_id} saved"
            )

            # =================================================
            # STEP 5: SEND SOURCES
            # =================================================

            if sources:

                log_agent(
                    "SYSTEM",
                    f"Sending {len(sources)} source(s) to frontend"
                )

            else:

                log_agent(
                    "SYSTEM",
                    "No sources to send"
                )

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "sources",
                        "sources": sources
                    }
                )
                + "\n\n"
            )

            # =================================================
            # STEP 6: COMPLETE
            # =================================================

            log_agent(
                "SYSTEM",
                "Request completed successfully"
            )

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "done"
                    }
                )
                + "\n\n"
            )

        except Exception as e:

            # =================================================
            # UNEXPECTED ERROR
            # =================================================

            log_agent(
                "SYSTEM",
                f"Unexpected error: {str(e)}"
            )

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "error",
                        "content": (
                            "Something went wrong "
                            "while processing your request."
                        )
                    }
                )
                + "\n\n"
            )

    # ========================================================
    # RETURN SSE RESPONSE
    # ========================================================

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )