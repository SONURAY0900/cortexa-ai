"""
Signed-in user's own account: profile (public.profiles + Supabase Auth)
and usage counts taken from existing tables.

Users can change their display name only. The role is never accepted
from the client (the request model forbids unknown fields) and admin
rights stay in app_metadata, which only the server key can write.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth.supabase_auth import get_current_user, is_admin
from database.connection import supabase
from services.files import FileStatus, FileType
from utils.logger import log_agent


router = APIRouter(tags=["account"])


def _metadata_name(user) -> str | None:
    meta = getattr(user, "user_metadata", None) or {}
    return meta.get("full_name") or meta.get("name")


def _metadata_avatar(user) -> str | None:
    meta = getattr(user, "user_metadata", None) or {}
    url = meta.get("avatar_url") or meta.get("picture")
    return url if isinstance(url, str) and url.startswith("https://") else None


def _profile_row(user_id: str) -> tuple[dict | None, bool]:
    """(row, available). available=False when the backend cannot read profiles."""
    try:
        rows = (
            supabase.table("profiles")
            .select("full_name,avatar_url,role,created_at,updated_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
        )
        return (rows[0] if rows else None), True
    except Exception as e:
        log_agent("PROFILE", f"Profile read failed: {e}")
        return None, False


def _count(table: str, apply) -> int | None:
    try:
        return apply(supabase.table(table).select("id", count="exact").limit(1)).execute().count
    except Exception as e:
        log_agent("PROFILE", f"Usage count failed for {table}: {e}")
        return None


def _usage(user_id: str) -> dict:
    return {
        "chats": _count("conversations", lambda q: q.eq("user_id", user_id).eq("is_archived", False)),
        "messages": _count("messages", lambda q: q.eq("user_id", user_id).eq("role", "USER")),
        "pdfs": _count("documents", lambda q: q.eq("user_id", user_id).eq("processing_status", "COMPLETED")),
        "images": _count("files", lambda q: q.eq("user_id", user_id).eq("file_type", FileType.IMAGE)
                         .neq("status", FileStatus.DELETED)),
    }


def _profile_payload(user, row: dict | None, available: bool, include_usage: bool = True) -> dict:
    app_meta = getattr(user, "app_metadata", None) or {}
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": (row or {}).get("full_name") or _metadata_name(user),
        "avatar_url": (row or {}).get("avatar_url") or _metadata_avatar(user),
        "role": "ADMIN" if is_admin(user) else "USER",
        "provider": app_meta.get("provider", "email"),
        "providers": app_meta.get("providers") or [app_meta.get("provider", "email")],
        "created_at": user.created_at,
        "last_sign_in_at": getattr(user, "last_sign_in_at", None),
        "email_confirmed": bool(getattr(user, "email_confirmed_at", None)),
        "profile_editable": available,
        **({"usage": _usage(str(user.id))} if include_usage else {}),
    }


@router.get("/profile/me")
def get_profile(user=Depends(get_current_user)):
    row, available = _profile_row(str(user.id))
    return _profile_payload(user, row, available)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")   # e.g. "role" is rejected

    full_name: str = Field(min_length=1, max_length=80)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join("".join(ch for ch in value if ch.isprintable()).split())
        if not value:
            raise ValueError("empty")
        return value


@router.patch("/profile/me")
def update_profile(body: ProfileUpdate, user=Depends(get_current_user)):
    user_id = str(user.id)
    row, available = _profile_row(user_id)

    if not available:
        raise HTTPException(status_code=503, detail="Profile editing is temporarily unavailable.")

    try:
        values = {"full_name": body.full_name, "updated_at": datetime.now(timezone.utc).isoformat()}
        if row:
            supabase.table("profiles").update(values).eq("id", user_id).execute()
        else:
            # New row: role keeps its database default (USER).
            supabase.table("profiles").insert({"id": user_id, **values}).execute()
    except Exception as e:
        log_agent("PROFILE", f"Profile update failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to save your profile. Please try again.")

    row, available = _profile_row(user_id)
    return _profile_payload(user, row, available, include_usage=False)
