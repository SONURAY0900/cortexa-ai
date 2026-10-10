"""
Welcome email for newly created accounts.

Supabase Auth only sends its own auth emails (confirmation, codes, password
reset), so the welcome email is sent by this backend through a configured
provider (WELCOME_EMAIL_PROVIDER = smtp | resend | brevo).

Trigger
    /auth/me (called by every page once per signed-in user) schedules
    send_welcome_if_new(user) as a background task. The user comes from a
    token verified by Supabase, never from browser-supplied data, and the
    email must be confirmed, so the address is known to belong to them.
    Password, email-code and Google accounts all pass through here.
    Accounts created while no provider was configured are recorded as
    SKIPPED/not_configured and still get the email once a provider is set
    up, if they are under MAX_ACCOUNT_AGE.

Which accounts are "new"
    public.welcome_emails holds one row per account (primary key user_id).
    The migration adds a SKIPPED row for every account that existed before
    it ran, and accounts older than MAX_ACCOUNT_AGE without a row are
    skipped too, so an existing user who signs in again gets nothing.

Duplicate protection (at most once per account)
    The row is claimed with an INSERT; a second request, worker or server
    loses on the primary key and sends nothing. A failed attempt is
    retried later only when the provider reported that nothing was sent,
    claimed again with a compare-and-set on the attempt counter. A row
    left in SENDING (crash mid-send, unknown outcome) is never resent:
    an account may miss its welcome email, but never gets two.

Logs record the user id and an outcome code, never the address,
credentials or the email body.
"""

import html
import smtplib
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

from config import settings
from database.connection import supabase
from services.activity import table_available
from utils.logger import log_agent


TABLE = "welcome_emails"
SUBJECT = "Welcome to Agentic AI ChatBot"
PROVIDERS = ("smtp", "resend", "brevo")

MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(minutes=10)
MAX_ACCOUNT_AGE = timedelta(days=7)
SEND_TIMEOUT_S = 15

# Accounts already settled in this process; saves a database read on
# every page load.
_settled: set[str] = set()
_lock = threading.Lock()


class EmailError(Exception):
    """Delivery failed before the provider accepted the message."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str
    idempotency_key: str


# ============================================================
# CONTENT
# ============================================================

FEATURES = [
    ("Ask anything", "It picks the right source for each question: a direct answer, web search, Wikipedia or arXiv research papers."),
    ("Chat with your PDFs", "Upload a PDF, including scanned pages read with OCR, and get answers with page citations."),
    ("Understand images", "Share a photo or screenshot to read its text or ask about what it shows."),
    ("Speak your question", "Use the microphone to dictate instead of typing."),
]


def _display_name(user) -> str:
    meta = getattr(user, "user_metadata", None) or {}
    name = meta.get("full_name") or meta.get("name") or ""
    return " ".join(str(name).split())[:80]


def build_email(user) -> Email:
    name = _display_name(user)
    greeting = f"Hi {name}," if name else "Hi there,"
    url = settings.APP_PUBLIC_URL

    support_text, support_html = "", ""
    if settings.SUPPORT_URL:
        support_text = f"Need help? {settings.SUPPORT_URL}"
        support_html = f'Need help? <a href="{html.escape(settings.SUPPORT_URL)}" style="color:#7c3aed;">Contact support</a>.'
    elif settings.SUPPORT_EMAIL:
        support_text = f"Need help? Email {settings.SUPPORT_EMAIL}"
        support_html = f'Need help? Email <a href="mailto:{html.escape(settings.SUPPORT_EMAIL)}" style="color:#7c3aed;">{html.escape(settings.SUPPORT_EMAIL)}</a>.'

    text_lines = [
        greeting,
        "",
        "Welcome to Agentic AI ChatBot, an AI assistant that chooses the right tool for every question.",
        "",
        "What you can do:",
        *[f"- {title}: {body}" for title, body in FEATURES],
        "",
    ]
    if url:
        text_lines += [f"Start chatting: {url}", ""]
    if support_text:
        text_lines += [support_text, ""]
    text_lines += ["You are receiving this email because an account was created with this address."]

    feature_rows = "".join(
        f'<tr><td style="padding:0 0 14px 0;font-size:15px;line-height:22px;color:#334155;">'
        f'<strong style="color:#0f172a;">{html.escape(title)}</strong><br>{html.escape(body)}</td></tr>'
        for title, body in FEATURES
    )
    button = (
        f'<tr><td style="padding:8px 0 24px 0;"><a href="{html.escape(url)}" '
        f'style="display:inline-block;background:#7c3aed;color:#ffffff;text-decoration:none;'
        f'font-weight:600;font-size:15px;padding:12px 24px;border-radius:8px;">Start chatting</a></td></tr>'
        if url else ""
    )
    support_row = (
        f'<tr><td style="padding:0 0 8px 0;font-size:14px;line-height:20px;color:#475569;">{support_html}</td></tr>'
        if support_html else ""
    )

    body_html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(SUBJECT)}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;font-family:Segoe UI,Helvetica,Arial,sans-serif;">
<tr><td style="padding:32px 28px 8px 28px;font-size:22px;line-height:28px;font-weight:700;color:#0f172a;">Welcome to Agentic AI ChatBot</td></tr>
<tr><td style="padding:8px 28px 0 28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:0 0 16px 0;font-size:15px;line-height:22px;color:#334155;">{html.escape(greeting)}<br><br>Thanks for joining. Agentic AI ChatBot is an AI assistant that chooses the right tool for every question.</td></tr>
{feature_rows}
{button}
{support_row}
<tr><td style="padding:8px 0 28px 0;font-size:12px;line-height:18px;color:#94a3b8;">You are receiving this email because an account was created with this address.</td></tr>
</table>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    return Email(
        to=user.email,
        subject=SUBJECT,
        text="\n".join(text_lines),
        html=body_html,
        idempotency_key=f"welcome-{user.id}",
    )


# ============================================================
# PROVIDERS
# ============================================================

def provider_configured() -> bool:
    p = settings.WELCOME_EMAIL_PROVIDER
    if p not in PROVIDERS or not settings.EMAIL_FROM:
        return False
    if p == "smtp":
        return bool(settings.SMTP_HOST)
    if p == "resend":
        return bool(settings.RESEND_API_KEY)
    return bool(settings.BREVO_API_KEY)


def _send_smtp(email: Email) -> None:
    msg = EmailMessage()
    msg["Subject"] = email.subject
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = email.to
    msg.set_content(email.text)
    msg.add_alternative(email.html, subtype="html")

    context = ssl.create_default_context()
    try:
        if settings.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=SEND_TIMEOUT_S, context=context)
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=SEND_TIMEOUT_S)
        with server:
            if settings.SMTP_PORT != 465:
                server.starttls(context=context)
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise EmailError("smtp_auth")
    except smtplib.SMTPRecipientsRefused:
        raise EmailError("recipient_refused")
    except (smtplib.SMTPException, OSError):
        raise EmailError("smtp_unavailable")


def _http_error(response: httpx.Response) -> EmailError:
    if response.status_code in (401, 403):
        return EmailError("provider_auth")
    if response.status_code == 429:
        return EmailError("provider_rate_limited")
    if response.status_code >= 500:
        return EmailError("provider_unavailable")
    return EmailError(f"provider_rejected_{response.status_code}")


def _post(url: str, headers: dict, payload: dict) -> None:
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=SEND_TIMEOUT_S)
    except httpx.HTTPError:
        raise EmailError("provider_unavailable")
    if response.status_code >= 300:
        raise _http_error(response)


def _send_resend(email: Email) -> None:
    _post(
        "https://api.resend.com/emails",
        {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            # Resend drops a repeat with the same key (24 h window).
            "Idempotency-Key": email.idempotency_key,
        },
        {"from": settings.EMAIL_FROM, "to": [email.to], "subject": email.subject, "html": email.html, "text": email.text},
    )


def _send_brevo(email: Email) -> None:
    name, address = parseaddr(settings.EMAIL_FROM)
    sender = {"email": address, **({"name": name} if name else {})}
    _post(
        "https://api.brevo.com/v3/smtp/email",
        {"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
        {"sender": sender, "to": [{"email": email.to}], "subject": email.subject,
         "htmlContent": email.html, "textContent": email.text},
    )


def deliver(email: Email) -> None:
    """Send through the configured provider. Raises EmailError."""
    send = {"smtp": _send_smtp, "resend": _send_resend, "brevo": _send_brevo}[settings.WELCOME_EMAIL_PROVIDER]
    send(email)


# ============================================================
# DELIVERY RECORD (public.welcome_emails, backend only)
# ============================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Store:
    """Thin wrapper so tests can swap the database for an in-memory fake."""

    def get(self, user_id: str) -> dict | None:
        rows = supabase.table(TABLE).select("status,reason,attempts,updated_at").eq("user_id", user_id).limit(1).execute().data
        return rows[0] if rows else None

    def insert(self, user_id: str, status: str, reason: str | None = None) -> bool:
        """False if the account already has a row (someone else claimed it)."""
        row = {"user_id": user_id, "status": status, "attempts": 1 if status == "SENDING" else 0, "reason": reason}
        try:
            supabase.table(TABLE).insert(row).execute()
        except Exception as e:
            if getattr(e, "code", None) == "23505":     # unique_violation
                return False
            raise
        return True

    def claim_retry(self, user_id: str, attempts: int) -> bool:
        """FAILED → SENDING, only if nobody else changed the row meanwhile."""
        rows = (
            supabase.table(TABLE)
            .update({"status": "SENDING", "attempts": attempts + 1, "updated_at": _now().isoformat()})
            .eq("user_id", user_id).eq("status", "FAILED").eq("attempts", attempts)
            .execute().data
        )
        return bool(rows)

    def claim_skipped(self, user_id: str) -> bool:
        """SKIPPED for lack of a provider → SENDING, if still unclaimed."""
        rows = (
            supabase.table(TABLE)
            .update({"status": "SENDING", "attempts": 1, "reason": None, "updated_at": _now().isoformat()})
            .eq("user_id", user_id).eq("status", "SKIPPED").eq("reason", "not_configured")
            .execute().data
        )
        return bool(rows)

    def finish(self, user_id: str, status: str, reason: str | None = None) -> None:
        values = {"status": status, "reason": reason, "updated_at": _now().isoformat()}
        if status == "SENT":
            values["sent_at"] = values["updated_at"]
        supabase.table(TABLE).update(values).eq("user_id", user_id).execute()


store = Store()


# ============================================================
# ENTRY POINT
# ============================================================

def _settle(user_id: str) -> None:
    with _lock:
        _settled.add(user_id)


def send_welcome_if_new(user) -> str:
    """
    Send the welcome email if this account is new and has not had one.
    Never raises; returns an outcome code (used by tests and logs).
    """
    user_id = str(getattr(user, "id", "") or "")
    if not user_id:
        return "no_user"
    with _lock:
        if user_id in _settled:
            return "settled"

    if not getattr(user, "email", None) or not getattr(user, "email_confirmed_at", None):
        return "unconfirmed"                       # try again once confirmed

    try:
        if not table_available(TABLE, "user_id"):
            return "no_table"

        row = store.get(user_id)
        created = _parse(getattr(user, "created_at", None))
        too_old = created is None or _now() - created > MAX_ACCOUNT_AGE

        if row is None:
            if too_old:
                store.insert(user_id, "SKIPPED", "existing_account")
                _settle(user_id)
                return "existing_account"
            if not provider_configured():
                store.insert(user_id, "SKIPPED", "not_configured")
                _settle(user_id)
                log_agent("WELCOME", f"user {user_id[:8]}: skipped, no email provider configured")
                return "not_configured"
            if not store.insert(user_id, "SENDING"):
                return "claimed_elsewhere"
            attempt = 1

        elif row.get("status") == "FAILED" and int(row.get("attempts") or 0) < MAX_ATTEMPTS:
            updated = _parse(row.get("updated_at"))
            if updated and _now() - updated < RETRY_AFTER:
                return "retry_later"
            if not provider_configured():
                return "not_configured"
            attempts = int(row.get("attempts") or 0)
            if not store.claim_retry(user_id, attempts):
                return "claimed_elsewhere"
            attempt = attempts + 1

        elif row.get("status") == "SKIPPED" and row.get("reason") == "not_configured":
            # Signed up while no provider was configured: still new enough
            # once one is, so send now (claimed so only one request does).
            if too_old or not provider_configured():
                _settle(user_id)
                return "not_configured" if not too_old else "existing_account"
            if not store.claim_skipped(user_id):
                return "claimed_elsewhere"
            attempt = 1

        else:
            # SENT, SKIPPED, out of retries, or SENDING with an unknown
            # outcome: never send again.
            if row.get("status") != "FAILED" or int(row.get("attempts") or 0) >= MAX_ATTEMPTS:
                _settle(user_id)
            return "already_handled"

    except Exception as e:
        log_agent("WELCOME", f"user {user_id[:8]}: delivery record unavailable ({type(e).__name__})")
        return "store_error"

    try:
        deliver(build_email(user))
    except EmailError as e:
        _record(user_id, "FAILED", e.code)
        log_agent("WELCOME", f"user {user_id[:8]}: attempt {attempt} failed ({e.code})")
        return "failed"
    except Exception as e:
        # Unexpected: the outcome is unknown, so leave the row in SENDING
        # (never resent) rather than risk a duplicate.
        log_agent("WELCOME", f"user {user_id[:8]}: attempt {attempt} outcome unknown ({type(e).__name__})")
        return "unknown"

    _record(user_id, "SENT")
    _settle(user_id)
    log_agent("WELCOME", f"user {user_id[:8]}: sent")
    return "sent"


def _record(user_id: str, status: str, reason: str | None = None) -> None:
    try:
        store.finish(user_id, status, reason)
    except Exception as e:
        # The row stays SENDING, which is never resent.
        log_agent("WELCOME", f"user {user_id[:8]}: could not record {status} ({type(e).__name__})")
