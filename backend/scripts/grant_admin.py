"""
Grant (or revoke) the admin role for the accounts listed in ADMIN_EMAIL.

    python -m scripts.grant_admin            # show current state, change nothing
    python -m scripts.grant_admin --grant    # set public.profiles.role = 'ADMIN'
    python -m scripts.grant_admin --revoke   # set public.profiles.role = 'USER'

Run it only on a trusted machine: it uses the server-side secret key
from backend/.env. It only touches accounts whose email is listed in
ADMIN_EMAIL, and only if the Supabase account already exists, its email
is confirmed and it has a profile row. It never creates accounts, never
creates profile rows and never reads or changes passwords.

The same change can be made in the Supabase Dashboard (SQL Editor):

    update public.profiles set role = 'ADMIN'
    where id = (select id from auth.users where email = '<admin email>');
"""

import sys

from config.settings import ADMIN_EMAILS
from database.connection import supabase


def _all_users() -> list:
    users, page = [], 1
    while True:
        batch = supabase.auth.admin.list_users(page=page, per_page=200)
        users.extend(batch)
        if len(batch) < 200:
            return users
        page += 1


def main() -> None:
    grant = "--grant" in sys.argv
    revoke = "--revoke" in sys.argv

    if grant and revoke:
        raise SystemExit("Use either --grant or --revoke, not both.")

    if not ADMIN_EMAILS:
        raise SystemExit("ADMIN_EMAIL is not set in backend/.env")

    by_email = {(u.email or "").lower(): u for u in _all_users()}

    for email in ADMIN_EMAILS:
        user = by_email.get(email)

        if not user:
            print(f"{email}: no Supabase account found (sign up through the app first)")
            continue

        if not user.email_confirmed_at:
            print(f"{email}: email not confirmed yet")
            continue

        rows = supabase.table("profiles").select("role").eq("id", str(user.id)).execute().data

        if not rows:
            print(f"{email}: no row in public.profiles (create it in the Dashboard first)")
            continue

        current = rows[0].get("role")

        if not (grant or revoke):
            print(f"{email}: profiles.role = {current}")
            continue

        role = "ADMIN" if grant else "USER"
        supabase.table("profiles").update({"role": role}).eq("id", str(user.id)).execute()
        print(f"{email}: profiles.role {current} -> {role}")


if __name__ == "__main__":
    main()
