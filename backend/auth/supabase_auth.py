import time
import uuid
from dataclasses import dataclass

import httpx
from fastapi import Depends, Header, HTTPException
from supabase_auth.errors import AuthApiError, AuthRetryableError

from config.settings import ADMIN_EMAILS
from database.connection import supabase
from utils.logger import log_agent


# Transport problems (timeouts, dropped connections, Supabase 5xx) are
# retried briefly. They are never treated as "invalid token": answering
# 401 for them made valid sessions look expired and logged users out.
VERIFY_ATTEMPTS = 3
VERIFY_BACKOFF_S = (0.2, 0.6)

SESSION_EXPIRED = "Your session has expired. Please log in again."
VERIFY_UNAVAILABLE = "We couldn't verify your session right now. Please try again in a moment."


def _verify_token(token: str):
    """
    Ask Supabase Auth who owns this token. Returns the user, or raises
    HTTPException 401 (rejected token) / 503 (could not verify).
    """

    for attempt in range(VERIFY_ATTEMPTS):
        try:
            response = supabase.auth.get_user(token)

            if not response or not response.user:
                raise HTTPException(status_code=401, detail=SESSION_EXPIRED)

            return response.user

        except HTTPException:
            raise

        except AuthApiError as e:
            status = getattr(e, "status", None) or 0
            if status == 429 or status >= 500:
                # Rate limited or a Supabase server error: says nothing
                # about the token, so it is handled like an outage.
                log_agent("AUTH", f"Token verification attempt {attempt + 1}/{VERIFY_ATTEMPTS} failed: HTTP {status}")
                if attempt < VERIFY_ATTEMPTS - 1:
                    time.sleep(VERIFY_BACKOFF_S[attempt])
                continue
            # Supabase rejected the token (expired, revoked, malformed).
            log_agent("AUTH", f"Token rejected ({status or '?'}): {getattr(e, 'code', '') or e}")
            raise HTTPException(status_code=401, detail=SESSION_EXPIRED)

        except (httpx.TransportError, AuthRetryableError) as e:
            log_agent("AUTH", f"Token verification attempt {attempt + 1}/{VERIFY_ATTEMPTS} failed: {e!r}")
            if attempt < VERIFY_ATTEMPTS - 1:
                time.sleep(VERIFY_BACKOFF_S[attempt])

        except Exception as e:
            log_agent("AUTH", f"Token verification error: {e!r}")
            break

    raise HTTPException(
        status_code=503,
        detail=VERIFY_UNAVAILABLE,
        headers={"Retry-After": "2"},
    )


def get_current_user(authorization: str | None = Header(default=None)):
    """
    Verify Supabase access token and return authenticated user.
    """

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header is required."
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization format."
        )

    token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Access token is missing."
        )

    return _verify_token(token)


# ============================================================
# GUESTS (chat without an account)
# ============================================================

@dataclass(frozen=True)
class Guest:
    """
    A visitor who chats without an account. guest_id is a random UUID the
    browser generates and keeps; it works like a bearer secret for that
    visitor's guest chats until they sign in and the chats are claimed.
    Guests can only send text: every upload endpoint requires a login.
    """
    guest_id: str
    id = None
    email = None


def is_guest(principal) -> bool:
    return isinstance(principal, Guest)


def get_user_or_guest(
    authorization: str | None = Header(default=None),
    x_guest_session: str | None = Header(default=None),
):
    """
    The signed-in user when a token is sent (an invalid token is still a
    401, never silently downgraded to a guest); otherwise a Guest
    identified by the X-Guest-Session header.
    """

    if authorization:
        return get_current_user(authorization)

    if x_guest_session:
        try:
            guest_id = str(uuid.UUID(x_guest_session.strip()))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid guest session.")
        return Guest(guest_id)

    raise HTTPException(status_code=401, detail="Please log in to continue.")


ROLE_UNAVAILABLE = "We couldn't verify your access right now. Please try again in a moment."


class RoleUnavailable(Exception):
    """public.profiles could not be read, so the role is unknown."""


def _profile_role(user_id: str) -> str | None:
    """
    public.profiles.role ("ADMIN" / "USER"), or None when the user has no
    profile row. Raises RoleUnavailable when the table cannot be read, so
    an outage is never mistaken for "not an admin" (or for "admin").
    """
    try:
        rows = supabase.table("profiles").select("role").eq("id", user_id).limit(1).execute().data
    except Exception as e:
        log_agent("AUTH", f"Profile role lookup failed: {e!r}")
        raise RoleUnavailable() from e
    return (rows[0].get("role") or "").upper() if rows else None


def admin_candidate(user) -> bool:
    """Confirmed email on the ADMIN_EMAIL allowlist (necessary, not sufficient)."""
    email = (getattr(user, "email", "") or "").lower()
    return bool(email and email in ADMIN_EMAILS and getattr(user, "email_confirmed_at", None))


def is_admin(user, role: str | None = None) -> bool:
    """
    Admin = BOTH
      1. a confirmed email listed in ADMIN_EMAIL (server configuration), and
      2. public.profiles.role == "ADMIN" (set by a trusted operator in the
         Supabase Dashboard or with scripts.grant_admin; users cannot
         change their own role).

    A missing profile row means "not an admin"; nobody is promoted
    automatically. Pass `role` when the caller already read the profile.
    Raises RoleUnavailable if the role cannot be read.
    """
    if not admin_candidate(user):
        return False
    if role is None:
        role = _profile_role(str(user.id))
    return (role or "").upper() == "ADMIN"


def get_current_admin(user=Depends(get_current_user)):
    try:
        allowed = is_admin(user)
    except RoleUnavailable:
        raise HTTPException(status_code=503, detail=ROLE_UNAVAILABLE, headers={"Retry-After": "2"})

    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this page."
        )

    return user
