from fastapi import Header, HTTPException
from supabase import create_client, Client

from config.settings import SUPABASE_URL, SUPABASE_KEY


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
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
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {str(e)}"
        )