"""
Grant (or revoke) the admin role for the accounts listed in ADMIN_EMAIL.

    python -m scripts.grant_admin            # grant
    python -m scripts.grant_admin --revoke   # revoke

Sets app_metadata.role = "admin" through the Supabase Admin API. That
field can only be written with the server-side secret key, so users
can never grant it to themselves. The account must already exist and
be confirmed; this script never creates accounts or touches passwords.
"""

import sys

from config.settings import ADMIN_EMAILS
from database.connection import supabase


def main() -> None:
    revoke = "--revoke" in sys.argv

    if not ADMIN_EMAILS:
        raise SystemExit("ADMIN_EMAIL is not set in .env")

    users, page = [], 1
    while True:
        batch = supabase.auth.admin.list_users(page=page, per_page=200)
        users.extend(batch)
        if len(batch) < 200:
            break
        page += 1

    by_email = {(u.email or "").lower(): u for u in users}

    for email in ADMIN_EMAILS:
        user = by_email.get(email)

        if not user:
            print(f"{email}: no Supabase account found (sign up first)")
            continue

        if not user.email_confirmed_at:
            print(f"{email}: email not confirmed yet")
            continue

        app_metadata = dict(user.app_metadata or {})

        if revoke:
            app_metadata.pop("role", None)
        else:
            app_metadata["role"] = "admin"

        supabase.auth.admin.update_user_by_id(str(user.id), {"app_metadata": app_metadata})
        print(f"{email}: admin role {'revoked' if revoke else 'granted'}")


if __name__ == "__main__":
    main()
