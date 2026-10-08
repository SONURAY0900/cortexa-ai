from fastapi import Depends, Header, HTTPException

from config.settings import ADMIN_EMAILS
from database.connection import supabase
from utils.logger import log_agent


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

    try:
        response = supabase.auth.get_user(token)

        if not response or not response.user:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired session."
            )

        return response.user

    except HTTPException:
        raise

    except Exception as e:
        log_agent("AUTH", f"Token verification failed: {e}")
        raise HTTPException(
            status_code=401,
            detail="Your session has expired. Please log in again."
        )


def is_admin(user) -> bool:
    """
    Admin = confirmed email on the ADMIN_EMAIL allowlist AND
    app_metadata.role == "admin". app_metadata can only be written
    with the service key, so users cannot grant it to themselves.
    """

    email = (getattr(user, "email", "") or "").lower()
    app_metadata = getattr(user, "app_metadata", None) or {}

    return bool(
        email
        and email in ADMIN_EMAILS
        and getattr(user, "email_confirmed_at", None)
        and app_metadata.get("role") == "admin"
    )


def get_current_admin(user=Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this page."
        )

    return user
