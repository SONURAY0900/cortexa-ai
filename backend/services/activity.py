"""
Minimal activity logging.

Writes only events that no other table records (voice, failures).
Chat queries are already stored in `messages`, PDF uploads in
`documents`, image uploads in `files` / `images`.

Logging never breaks a user request: if the table is missing (the
migration has not been run yet) or the insert fails, the event is
skipped and the failure is printed once.
"""

from database.connection import supabase
from utils.logger import log_agent


ACTION_MAX_CHARS = 500

_table_state: dict[str, bool] = {}


def table_available(table: str, column: str = "id") -> bool:
    """
    Cached check that a table exists and is readable by the backend.
    A missing table is re-checked on the next call so running the
    migration takes effect without a restart.
    """

    if _table_state.get(table):
        return True

    try:
        supabase.table(table).select(column).limit(1).execute()
        _table_state[table] = True
    except Exception:
        if table not in _table_state:
            log_agent("ACTIVITY", f"Table '{table}' is not available; run supabase/migrations.")
        _table_state[table] = False

    return _table_state[table]


def log_activity(
    user_id: str | None,
    activity_type: str,
    action: str = "",
    status: str = "SUCCESS",
    route: str | None = None,
    document_id: str | None = None,
    file_name: str | None = None,
    metadata: dict | None = None,
) -> None:

    if not table_available("activity_logs"):
        return

    try:
        supabase.table("activity_logs").insert({
            "user_id": user_id,
            "activity_type": activity_type,
            "action": (action or "")[:ACTION_MAX_CHARS],
            "status": status,
            "route": route,
            "document_id": document_id,
            "file_name": file_name,
            "metadata": metadata or {},
        }).execute()
    except Exception as e:
        log_agent("ACTIVITY", f"Activity log insert failed: {e}")


def log_admin_action(
    admin,
    action: str,
    target_type: str,
    target_id: str,
    status: str,
    details: dict | None = None,
) -> None:

    if not table_available("admin_audit_log"):
        return

    try:
        supabase.table("admin_audit_log").insert({
            "admin_id": str(admin.id),
            "admin_email": admin.email,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id),
            "status": status,
            "details": details or {},
        }).execute()
    except Exception as e:
        log_agent("ACTIVITY", f"Admin audit insert failed: {e}")
