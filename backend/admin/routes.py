"""
Admin API.

Every route depends on get_current_admin, so authorization is enforced
server-side for each request (valid Supabase session + ADMIN_EMAIL
allowlist + app_metadata.role == "admin").

Only data that the application actually stores is returned:

    users            Supabase Auth (email, created, last sign-in, status)
    chat / searches  public.messages (every user question + route)
    PDF uploads      public.documents
    image uploads    public.files + public.images (recorded since v1.1)
    voice, failures  public.activity_logs (after the migration is run)
    admin actions    public.admin_audit_log (after the migration is run)

Aggregation happens in Python over paged reads. That is fine for the
current scale (hundreds to low thousands of rows); for much larger
data, move the counts into SQL views or RPCs.
"""

import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.supabase_auth import get_current_admin, is_admin
from config.settings import (
    ADMIN_EMAILS,
    ALLOWED_ORIGINS,
    APP_ENV,
    APP_VERSION,
    GROQ_API_KEY,
    MAX_AUDIO_MB,
    MAX_IMAGE_MB,
    MAX_PDF_MB,
    SUPABASE_PUBLISHABLE_KEY,
)
from database.connection import supabase
from rag.retrieval.vector_store import delete_document
from services.activity import log_admin_action, table_available
from services.storage import (
    IMAGE_UPLOAD_DIR,
    PDF_UPLOAD_DIR,
    UPLOAD_ROOT,
    delete_stored_file,
    directory_usage,
)
from utils.logger import log_agent


router = APIRouter(prefix="/admin", tags=["admin"])

SEARCH_ROUTES = {"WIKIPEDIA", "TAVILY", "ARXIV"}
ACTIVE_WINDOW_DAYS = 30
PAGE_SIZE_MAX = 100
FETCH_PAGE = 1000
FETCH_CAP = 50_000
STARTED_AT = time.time()


# ============================================================
# HELPERS
# ============================================================

def _fetch_all(table: str, columns: str, apply=None, order: str | None = None) -> list[dict]:
    """Paged read of a table (capped at FETCH_CAP rows)."""

    rows, start = [], 0

    while start < FETCH_CAP:
        query = supabase.table(table).select(columns)
        if apply:
            query = apply(query)
        if order:
            query = query.order(order, desc=True)
        batch = query.range(start, start + FETCH_PAGE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < FETCH_PAGE:
            break
        start += FETCH_PAGE

    return rows


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value) -> str | None:
    parsed = _parse_time(value)
    return parsed.isoformat() if parsed else None


def _auth_users() -> list[dict]:
    users, page = [], 1

    while True:
        batch = supabase.auth.admin.list_users(page=page, per_page=200)
        for u in batch:
            banned_until = _parse_time(getattr(u, "banned_until", None))
            if banned_until and banned_until > datetime.now(timezone.utc):
                status = "BANNED"
            elif not getattr(u, "email_confirmed_at", None):
                status = "UNCONFIRMED"
            else:
                status = "ACTIVE"
            users.append({
                "id": str(u.id),
                "email": u.email or "",
                "created_at": _iso(u.created_at),
                "last_sign_in_at": _iso(getattr(u, "last_sign_in_at", None)),
                "status": status,
                "role": "ADMIN" if is_admin(u) else "USER",
                "provider": (u.app_metadata or {}).get("provider", "email"),
            })
        if len(batch) < 200:
            break
        page += 1

    return users


def _page(items: list, page: int, page_size: int) -> dict:
    page_size = max(1, min(page_size, PAGE_SIZE_MAX))
    page = max(1, page)
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": len(items),
        "pages": max(1, -(-len(items) // page_size)),
    }


def _chat_events(messages: list[dict]) -> list[dict]:
    """
    One event per user question. The route comes from the question
    itself (recorded since v1.1) or from the assistant reply that
    followed it in the same conversation (older history). A question
    with no reply is reported as FAILED ("no response recorded").
    """

    by_conversation = defaultdict(list)
    for m in messages:
        by_conversation[m["conversation_id"]].append(m)

    events = []

    for rows in by_conversation.values():
        rows.sort(key=lambda r: r["created_at"])
        for index, m in enumerate(rows):
            if str(m.get("role")).upper() != "USER":
                continue
            reply = rows[index + 1] if index + 1 < len(rows) else None
            answered = bool(reply and str(reply.get("role")).upper() == "ASSISTANT")
            route = m.get("route") or (reply.get("route") if answered else None)
            metadata = m.get("metadata") or {}
            events.append({
                "id": m["id"],
                "source": "messages",
                "type": "SEARCH" if route in SEARCH_ROUTES else "CHAT",
                "timestamp": _iso(m["created_at"]),
                "user_id": m.get("user_id"),
                "action": m.get("content") or "",
                "status": "SUCCESS" if answered else "FAILED",
                "status_note": "" if answered else "No response recorded",
                "route": route,
                "document_id": metadata.get("document_id"),
                "conversation_id": m["conversation_id"],
                "deletable": False,
            })

    return events


def _load_messages() -> list[dict]:
    return _fetch_all("messages", "id,conversation_id,user_id,role,content,route,metadata,created_at")


def _load_documents() -> list[dict]:
    return _fetch_all(
        "documents",
        "id,filename,file_type,file_size,uploaded_at,user_id,processing_status,file_path",
        order="uploaded_at",
    )


def _chunk_counts() -> Counter:
    return Counter(r["document_id"] for r in _fetch_all("document_chunks", "document_id"))


def _load_images() -> list[dict]:
    files = _fetch_all(
        "files",
        "id,user_id,filename,file_size,mime_type,status,uploaded_at,storage_path",
        apply=lambda q: q.eq("file_type", "IMAGE").is_("deleted_at", "null"),
        order="uploaded_at",
    )
    statuses = {
        r["file_id"]: r
        for r in _fetch_all("images", "file_id,ocr_status,vision_status")
    }
    for f in files:
        s = statuses.get(f["id"], {})
        f["ocr_status"] = s.get("ocr_status")
        f["vision_status"] = s.get("vision_status")
    return files


def _load_activity_logs() -> list[dict]:
    if not table_available("activity_logs"):
        return []
    return _fetch_all(
        "activity_logs",
        "id,user_id,activity_type,action,status,route,document_id,file_name,created_at",
        order="created_at",
    )


def _image_status(f: dict) -> str:
    if "FAILED" in (f.get("ocr_status"), f.get("vision_status")):
        return "FAILED"
    if f.get("ocr_status") == "COMPLETED" or f.get("vision_status") == "COMPLETED":
        return "SUCCESS"
    return "PENDING"


def _all_events() -> list[dict]:
    documents = {d["id"]: d for d in _load_documents()}

    events = _chat_events(_load_messages())

    for e in events:
        doc = documents.get(e["document_id"]) if e["document_id"] else None
        e["source_name"] = doc["filename"] if doc else None

    for d in documents.values():
        status = (d.get("processing_status") or "").upper()
        events.append({
            "id": d["id"],
            "source": "documents",
            "type": "PDF_UPLOAD",
            "timestamp": _iso(d["uploaded_at"]),
            "user_id": d.get("user_id"),
            "action": d.get("filename") or "",
            "status": "SUCCESS" if status == "COMPLETED" else ("PENDING" if status == "PROCESSING" else status or "UNKNOWN"),
            "status_note": "" if d.get("user_id") else "Evaluation copy / no owner",
            "route": None,
            "document_id": d["id"],
            "source_name": d.get("filename"),
            "deletable": False,
        })

    for f in _load_images():
        events.append({
            "id": f["id"],
            "source": "files",
            "type": "IMAGE_UPLOAD",
            "timestamp": _iso(f["uploaded_at"]),
            "user_id": f.get("user_id"),
            "action": f.get("filename") or "",
            "status": _image_status(f),
            "status_note": f"OCR {f.get('ocr_status') or '-'} · Vision {f.get('vision_status') or '-'}",
            "route": "IMAGE",
            "document_id": None,
            "source_name": f.get("filename"),
            "deletable": False,
        })

    for a in _load_activity_logs():
        kind = a["activity_type"]
        events.append({
            "id": a["id"],
            "source": "activity_logs",
            "type": "VOICE" if kind == "VOICE" else "ERROR",
            "timestamp": _iso(a["created_at"]),
            "user_id": a.get("user_id"),
            "action": a.get("action") or kind,
            "status": a.get("status") or "SUCCESS",
            "status_note": "" if kind == "VOICE" else kind.replace("_", " ").title(),
            "route": a.get("route"),
            "document_id": a.get("document_id"),
            "source_name": a.get("file_name"),
            "deletable": True,
        })

    events.sort(key=lambda e: e["timestamp"] or "", reverse=True)
    return events


def _attach_emails(items: list[dict], emails: dict[str, str]) -> list[dict]:
    for item in items:
        item["user_email"] = emails.get(str(item.get("user_id"))) if item.get("user_id") else None
    return items


def _filter_events(
    events: list[dict],
    emails: dict[str, str],
    types: set[str] | None,
    user: str | None,
    status: str | None,
    date_from: str | None,
    date_to: str | None,
    q: str | None,
) -> list[dict]:

    _attach_emails(events, emails)
    start = _parse_time(date_from) if date_from else None
    end = _parse_time(date_to) if date_to else None
    if end and len(date_to) <= 10:
        end += timedelta(days=1)  # inclusive whole day
    needle = (q or "").strip().lower()
    user_needle = (user or "").strip().lower()

    def keep(e):
        if types and e["type"] not in types:
            return False
        if status and e["status"] != status.upper():
            return False
        if user_needle and user_needle not in (e.get("user_email") or "").lower() and user_needle != str(e.get("user_id")):
            return False
        ts = _parse_time(e["timestamp"])
        if start and (not ts or ts < start):
            return False
        if end and (not ts or ts >= end):
            return False
        if needle and needle not in " ".join(
            str(e.get(k) or "") for k in ("action", "user_email", "route", "source_name", "status_note")
        ).lower():
            return False
        return True

    return [e for e in events if keep(e)]


# ============================================================
# IDENTITY
# ============================================================

@router.get("/me")
def admin_me(admin=Depends(get_current_admin)):
    return {"is_admin": True, "email": admin.email, "id": str(admin.id)}


# ============================================================
# DASHBOARD
# ============================================================

@router.get("/dashboard")
def dashboard(admin=Depends(get_current_admin)):

    users = _auth_users()
    messages = _load_messages()
    documents = _load_documents()
    images = _load_images()
    logs = _load_activity_logs()
    chat_events = _chat_events(messages)

    cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIVE_WINDOW_DAYS)
    active_ids = {
        u["id"] for u in users
        if (_parse_time(u["last_sign_in_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    }
    for row in [*messages, *documents, *images, *logs]:
        ts = _parse_time(row.get("created_at") or row.get("uploaded_at"))
        if row.get("user_id") and ts and ts >= cutoff:
            active_ids.add(str(row["user_id"]))

    logging_enabled = table_available("activity_logs")

    return {
        "cards": {
            "total_users": len(users),
            "active_users": len(active_ids),
            "total_searches": sum(1 for e in chat_events if e["type"] == "SEARCH"),
            "total_pdf_uploads": sum(1 for d in documents if d.get("user_id")),
            "total_image_uploads": len(images),
            "total_documents": len(documents),
            "total_chat_messages": len(messages),
            "failed_requests": (
                sum(1 for a in logs if a.get("status") == "FAILED") if logging_enabled else None
            ),
        },
        "details": {
            "active_window_days": ACTIVE_WINDOW_DAYS,
            "user_questions": len(chat_events),
            "unanswered_questions": sum(1 for e in chat_events if e["status"] == "FAILED"),
            "unowned_documents": sum(1 for d in documents if not d.get("user_id")),
            "routes": dict(Counter(e["route"] or "UNKNOWN" for e in chat_events)),
            "activity_logging_enabled": logging_enabled,
            "image_tracking_note": "Image uploads are recorded from v1.1 onward; earlier image uploads were not stored.",
        },
        "recent_activity": _attach_emails(_all_events()[:8], {u["id"]: u["email"] for u in users}),
    }


# ============================================================
# USERS
# ============================================================

def _user_stats() -> dict[str, dict]:
    stats = defaultdict(lambda: {"searches": 0, "chats": 0, "uploads": 0, "last_activity": None})

    def touch(user_id, ts):
        if not user_id:
            return
        s = stats[str(user_id)]
        if ts and (not s["last_activity"] or ts > s["last_activity"]):
            s["last_activity"] = ts

    for e in _chat_events(_load_messages()):
        if not e["user_id"]:
            continue
        stats[str(e["user_id"])]["searches" if e["type"] == "SEARCH" else "chats"] += 1
        touch(e["user_id"], e["timestamp"])

    for d in _load_documents():
        if d.get("user_id"):
            stats[str(d["user_id"])]["uploads"] += 1
            touch(d["user_id"], _iso(d["uploaded_at"]))

    for f in _load_images():
        if f.get("user_id"):
            stats[str(f["user_id"])]["uploads"] += 1
            touch(f["user_id"], _iso(f["uploaded_at"]))

    return stats


SORT_KEYS = {
    "created_at": lambda u: u["created_at"] or "",
    "email": lambda u: u["email"].lower(),
    "last_activity": lambda u: u["last_activity"] or "",
    "searches": lambda u: u["total_searches"],
    "chats": lambda u: u["total_chat_requests"],
    "uploads": lambda u: u["total_uploads"],
}


@router.get("/users")
def list_users(
    search: str | None = None,
    status: str | None = Query(default=None, pattern="^(ACTIVE|UNCONFIRMED|BANNED)$"),
    role: str | None = Query(default=None, pattern="^(ADMIN|USER)$"),
    sort: str = Query(default="created_at", pattern="^(created_at|email|last_activity|searches|chats|uploads)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = 1,
    page_size: int = 20,
    admin=Depends(get_current_admin),
):
    stats = _user_stats()
    users = []

    for u in _auth_users():
        s = stats.get(u["id"], {})
        last = max(filter(None, [s.get("last_activity"), u["last_sign_in_at"]]), default=None)
        users.append({
            **u,
            "last_activity": last,
            "total_searches": s.get("searches", 0),
            "total_chat_requests": s.get("chats", 0),
            "total_uploads": s.get("uploads", 0),
        })

    needle = (search or "").strip().lower()
    users = [
        u for u in users
        if (not needle or needle in u["email"].lower() or needle == u["id"])
        and (not status or u["status"] == status)
        and (not role or u["role"] == role)
    ]
    users.sort(key=SORT_KEYS[sort], reverse=order == "desc")

    return _page(users, page, page_size)


@router.get("/users/{user_id}")
def get_user(user_id: str, admin=Depends(get_current_admin)):
    user = next((u for u in _auth_users() if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    events = _attach_emails(
        [e for e in _all_events() if str(e.get("user_id")) == user_id],
        {user_id: user["email"]},
    )
    s = _user_stats().get(user_id, {})

    return {
        "user": {
            **user,
            "total_searches": s.get("searches", 0),
            "total_chat_requests": s.get("chats", 0),
            "total_uploads": s.get("uploads", 0),
        },
        "recent_activity": events[:50],
    }


class DeleteUserRequest(BaseModel):
    confirm_email: str


@router.delete("/users/{user_id}")
def delete_user(user_id: str, body: DeleteUserRequest, admin=Depends(get_current_admin)):
    """
    Permanently delete a user and the data the app stores for them.
    Order follows the foreign keys: chunks → documents → image rows →
    files → messages → conversations → activity → auth account.
    """

    user = next((u for u in _auth_users() if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user_id == str(admin.id) or user["role"] == "ADMIN":
        raise HTTPException(status_code=400, detail="Admin accounts cannot be deleted from the admin panel.")

    if body.confirm_email.strip().lower() != user["email"].lower():
        raise HTTPException(status_code=400, detail="The confirmation email does not match this user.")

    removed = {"documents": 0, "images": 0, "messages": 0, "conversations": 0, "local_files": 0}
    step = "start"

    try:
        step = "documents"
        for d in _fetch_all("documents", "id,file_path", apply=lambda q: q.eq("user_id", user_id)):
            delete_document(d["id"])
            removed["documents"] += 1
            removed["local_files"] += delete_stored_file(d.get("file_path"))

        step = "files"
        files = _fetch_all("files", "id,storage_path", apply=lambda q: q.eq("user_id", user_id))
        file_ids = [f["id"] for f in files]
        if file_ids:
            supabase.table("images").delete().in_("file_id", file_ids).execute()
            supabase.table("audio_files").delete().in_("file_id", file_ids).execute()
            supabase.table("files").delete().in_("id", file_ids).execute()
        for f in files:
            removed["images"] += 1
            removed["local_files"] += delete_stored_file(f.get("storage_path"))

        step = "messages"
        removed["messages"] = len(
            supabase.table("messages").delete().eq("user_id", user_id).execute().data or []
        )

        step = "conversations"
        removed["conversations"] = len(
            supabase.table("conversations").delete().eq("user_id", user_id).execute().data or []
        )

        step = "activity"
        if table_available("activity_logs"):
            supabase.table("activity_logs").delete().eq("user_id", user_id).execute()

        step = "auth"
        supabase.auth.admin.delete_user(user_id)

    except Exception as e:
        log_agent("ADMIN", f"Delete user failed at {step}: {e!r}")
        log_admin_action(admin, "DELETE_USER", "user", user_id, "FAILED",
                         {"email": user["email"], "failed_step": step, **removed})
        raise HTTPException(
            status_code=500,
            detail=f"Deleting the user stopped at step '{step}'. Some data may already be removed; see the audit log.",
        )

    log_admin_action(admin, "DELETE_USER", "user", user_id, "SUCCESS", {"email": user["email"], **removed})

    return {"success": True, "removed": removed}


# ============================================================
# ACTIVITY
# ============================================================

ACTIVITY_TYPES = {"CHAT", "SEARCH", "PDF_UPLOAD", "IMAGE_UPLOAD", "VOICE", "ERROR"}


def _activity_response(types, user, status, date_from, date_to, q, page, page_size):
    emails = {u["id"]: u["email"] for u in _auth_users()}
    events = _filter_events(_all_events(), emails, types, user, status, date_from, date_to, q)
    result = _page(events, page, page_size)
    result["activity_logging_enabled"] = table_available("activity_logs")
    return result


@router.get("/activity")
def activity(
    type: str | None = None,
    user: str | None = None,
    status: str | None = Query(default=None, pattern="^(SUCCESS|FAILED|PENDING)$"),
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
    admin=Depends(get_current_admin),
):
    types = {t.strip().upper() for t in type.split(",")} & ACTIVITY_TYPES if type else None
    return _activity_response(types, user, status, date_from, date_to, q, page, page_size)


@router.get("/searches")
def searches(
    user: str | None = None,
    status: str | None = Query(default=None, pattern="^(SUCCESS|FAILED)$"),
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
    admin=Depends(get_current_admin),
):
    return _activity_response({"SEARCH"}, user, status, date_from, date_to, q, page, page_size)


@router.get("/chat-activity")
def chat_activity(
    user: str | None = None,
    status: str | None = Query(default=None, pattern="^(SUCCESS|FAILED)$"),
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
    admin=Depends(get_current_admin),
):
    return _activity_response({"CHAT", "SEARCH"}, user, status, date_from, date_to, q, page, page_size)


@router.delete("/activity/{log_id}")
def delete_activity(log_id: str, admin=Depends(get_current_admin)):
    """Only activity_logs rows can be deleted; chats and uploads have their own delete actions."""

    if not table_available("activity_logs"):
        raise HTTPException(status_code=404, detail="Activity logging is not enabled.")

    try:
        deleted = supabase.table("activity_logs").delete().eq("id", log_id).execute().data or []
    except Exception as e:
        log_agent("ADMIN", f"Delete activity failed: {e!r}")
        deleted = None

    if deleted is None:
        log_admin_action(admin, "DELETE_ACTIVITY", "activity_log", log_id, "FAILED")
        raise HTTPException(status_code=500, detail="The activity record could not be deleted.")

    if not deleted:
        raise HTTPException(status_code=404, detail="Activity record not found.")

    log_admin_action(admin, "DELETE_ACTIVITY", "activity_log", log_id, "SUCCESS")
    return {"success": True}


@router.get("/audit-log")
def audit_log(page: int = 1, page_size: int = 25, admin=Depends(get_current_admin)):
    if not table_available("admin_audit_log"):
        return {**_page([], page, page_size), "enabled": False}

    rows = _fetch_all(
        "admin_audit_log",
        "id,admin_email,action,target_type,target_id,status,details,created_at",
        order="created_at",
    )
    return {**_page(rows, page, page_size), "enabled": True}


# ============================================================
# DOCUMENTS
# ============================================================

@router.get("/documents")
def documents(
    search: str | None = None,
    owner: str | None = Query(default=None, pattern="^(owned|unowned)$"),
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
    admin=Depends(get_current_admin),
):
    emails = {u["id"]: u["email"] for u in _auth_users()}
    counts = _chunk_counts()
    needle = (search or "").strip().lower()

    items = []
    for d in _load_documents():
        email = emails.get(str(d.get("user_id"))) if d.get("user_id") else None
        item = {
            "id": d["id"],
            "filename": d.get("filename"),
            "file_size": d.get("file_size"),
            "uploaded_at": _iso(d.get("uploaded_at")),
            "user_id": d.get("user_id"),
            "user_email": email,
            "processing_status": d.get("processing_status"),
            "chunks": counts.get(d["id"], 0),
            "pages": None,  # page count is not stored
            "owned": bool(d.get("user_id")),
        }
        if needle and needle not in f"{item['filename']} {email or ''} {d['id']}".lower():
            continue
        if owner == "owned" and not item["owned"]:
            continue
        if owner == "unowned" and item["owned"]:
            continue
        if status and (item["processing_status"] or "").upper() != status.upper():
            continue
        items.append(item)

    return _page(items, page, page_size)


@router.delete("/documents/{document_id}")
def remove_document(document_id: str, admin=Depends(get_current_admin)):
    rows = supabase.table("documents").select("id,filename,file_path,user_id").eq("id", document_id).limit(1).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Document not found.")

    doc = rows[0]

    try:
        delete_document(document_id)
    except Exception as e:
        log_agent("ADMIN", f"Delete document failed: {e!r}")
        log_admin_action(admin, "DELETE_DOCUMENT", "document", document_id, "FAILED", {"filename": doc.get("filename")})
        raise HTTPException(status_code=500, detail="The document could not be deleted.")

    file_removed = delete_stored_file(doc.get("file_path"))

    log_admin_action(admin, "DELETE_DOCUMENT", "document", document_id, "SUCCESS", {
        "filename": doc.get("filename"),
        "owner": doc.get("user_id"),
        "local_file_removed": file_removed,
    })
    return {"success": True, "local_file_removed": file_removed}


# ============================================================
# IMAGES
# ============================================================

@router.get("/images")
def images(
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    admin=Depends(get_current_admin),
):
    emails = {u["id"]: u["email"] for u in _auth_users()}
    needle = (search or "").strip().lower()
    items = []

    for f in _load_images():
        email = emails.get(str(f.get("user_id"))) if f.get("user_id") else None
        if needle and needle not in f"{f.get('filename')} {email or ''}".lower():
            continue
        items.append({
            "id": f["id"],
            "filename": f.get("filename"),
            "file_size": f.get("file_size"),
            "mime_type": f.get("mime_type"),
            "uploaded_at": _iso(f.get("uploaded_at")),
            "user_id": f.get("user_id"),
            "user_email": email,
            "ocr_status": f.get("ocr_status"),
            "vision_status": f.get("vision_status"),
        })

    return {**_page(items, page, page_size), "note": "Image uploads are recorded from v1.1 onward."}


@router.delete("/images/{file_id}")
def remove_image(file_id: str, admin=Depends(get_current_admin)):
    rows = (
        supabase.table("files").select("id,filename,storage_path,user_id")
        .eq("id", file_id).eq("file_type", "IMAGE").limit(1).execute().data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Image not found.")

    image = rows[0]

    try:
        supabase.table("images").delete().eq("file_id", file_id).execute()
        supabase.table("files").delete().eq("id", file_id).execute()
    except Exception as e:
        log_agent("ADMIN", f"Delete image failed: {e!r}")
        log_admin_action(admin, "DELETE_IMAGE", "image", file_id, "FAILED", {"filename": image.get("filename")})
        raise HTTPException(status_code=500, detail="The image could not be deleted.")

    file_removed = delete_stored_file(image.get("storage_path"))

    log_admin_action(admin, "DELETE_IMAGE", "image", file_id, "SUCCESS", {
        "filename": image.get("filename"),
        "owner": image.get("user_id"),
        "local_file_removed": file_removed,
    })
    return {"success": True, "local_file_removed": file_removed}


# ============================================================
# SYSTEM HEALTH / SETTINGS
# ============================================================

def _timed(check) -> dict:
    started = time.perf_counter()
    try:
        detail = check()
        return {"status": "ok", "latency_ms": round(1000 * (time.perf_counter() - started)), "detail": detail}
    except Exception as e:
        log_agent("HEALTH", f"Health check failed: {e!r}")
        return {"status": "error", "latency_ms": round(1000 * (time.perf_counter() - started)),
                "detail": "Check failed (see server log)."}


@router.get("/system-health")
def system_health(admin=Depends(get_current_admin)):

    def database():
        supabase.table("conversations").select("id").limit(1).execute()
        return "Supabase Postgres reachable"

    def auth():
        supabase.auth.admin.list_users(page=1, per_page=1)
        return "Supabase Auth reachable"

    def llm():
        from groq import Groq
        Groq(api_key=GROQ_API_KEY, timeout=8).models.list()
        return "Groq API reachable"

    def rag():
        from rag.embeddings.embeddings import MODEL_NAME
        chunks = supabase.table("document_chunks").select("id", count="exact").limit(1).execute().count
        return f"{MODEL_NAME} (384-dim) · {chunks} stored chunks"

    def storage():
        probe = UPLOAD_ROOT / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        pdfs, images = directory_usage(PDF_UPLOAD_DIR), directory_usage(IMAGE_UPLOAD_DIR)
        return (
            f"Writable · PDFs {pdfs['files']} files ({pdfs['bytes'] / 1e6:.1f} MB) · "
            f"images {images['files']} files ({images['bytes'] / 1e6:.1f} MB) · local disk"
        )

    def logging_tables():
        missing = [t for t in ("activity_logs", "admin_audit_log") if not table_available(t)]
        if missing:
            raise RuntimeError("missing tables: " + ", ".join(missing))
        return "activity_logs and admin_audit_log present"

    checks = {
        "backend": {"status": "ok", "latency_ms": 0, "detail": f"Up {int(time.time() - STARTED_AT)} s"},
        "database": _timed(database),
        "auth": _timed(auth),
        "llm": _timed(llm),
        "rag": _timed(rag),
        "storage": _timed(storage),
        "activity_logging": _timed(logging_tables),
    }

    if checks["activity_logging"]["status"] == "error":
        checks["activity_logging"]["detail"] = "Not enabled: run supabase/migrations/20261008_activity_logging.sql"

    return {
        "environment": APP_ENV,
        "version": APP_VERSION,
        "checks": checks,
        "overall": "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded",
    }


@router.get("/settings")
def admin_settings(admin=Depends(get_current_admin)):
    """Read-only, non-secret configuration."""

    from agents.llm_agent import llm
    from rag.embeddings.embeddings import MODEL_NAME
    from tools.speech_to_text_tool import SPEECH_MODEL
    from tools.vision_tool import VISION_MODEL

    return {
        "environment": APP_ENV,
        "version": APP_VERSION,
        "admin_accounts": len(ADMIN_EMAILS),
        "allowed_origins": ALLOWED_ORIGINS or ["same origin only"],
        "upload_limits_mb": {"pdf": MAX_PDF_MB, "image": MAX_IMAGE_MB, "audio": MAX_AUDIO_MB},
        "models": {
            "llm": llm.model_name,
            "vision": VISION_MODEL,
            "speech": SPEECH_MODEL,
            "embedding": MODEL_NAME,
        },
        "supabase_publishable_key_configured": bool(SUPABASE_PUBLISHABLE_KEY),
    }
