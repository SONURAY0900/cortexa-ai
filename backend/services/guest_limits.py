"""
Rate limits, in memory, per server process: rolling one-hour windows.

* Guest chat (visitors without an account): counted per guest id and per
  client IP, so anonymous use cannot exhaust the shared Groq quota.
* Signed-in users: chat, PDF uploads, image uploads and voice, counted
  per account (see allow_user).

A restart resets the counts. That is fine for their purpose (stopping one
person or script from using up shared quota and memory) and needs no
database table.
shortcut: counts live in this process; move them to Redis when the app
runs more than one instance.
"""

import math
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from config import settings

WINDOW_S = 3600

# Action -> settings name of its per-hour limit (read on each call, so a
# changed setting applies without a restart in tests).
USER_LIMITS = {
    "chat": "CHAT_MESSAGES_PER_HOUR",
    "pdf": "PDF_UPLOADS_PER_HOUR",
    "image": "IMAGE_UPLOADS_PER_HOUR",
    "voice": "VOICE_REQUESTS_PER_HOUR",
}

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """
    The visitor's IP. Render sits behind Cloudflare, which sets
    CF-Connecting-IP; otherwise the last proxy hop, then the socket.
    """
    ip = request.headers.get("cf-connecting-ip")
    if ip:
        return ip.strip()
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if forwarded:
        return forwarded[-1]
    return request.client.host if request.client else "unknown"


def _window(key: str, now: float) -> deque:
    """The key's timestamps within the last hour (caller holds _lock)."""
    window = _hits[key]
    while window and now - window[0] >= WINDOW_S:
        window.popleft()
    return window


def allow(guest_id: str, ip: str, now: float | None = None) -> bool:
    """Record one guest message; False if the guest or IP is over the limit."""

    limit = settings.GUEST_MESSAGES_PER_HOUR
    if limit <= 0:
        return False

    now = time.monotonic() if now is None else now
    keys = (f"guest:{guest_id}", f"ip:{ip}")

    with _lock:
        if any(len(_window(key, now)) >= limit for key in keys):
            return False
        for key in keys:
            _hits[key].append(now)
    return True


def allow_user(action: str, user_id: str, now: float | None = None) -> int:
    """
    Record one `action` ("chat", "pdf", "image", "voice") for a signed-in
    user. Returns 0 when allowed, otherwise the seconds until the oldest
    counted action leaves the window (for Retry-After). A limit of 0 means
    no limit.
    """

    limit = getattr(settings, USER_LIMITS[action])
    if limit <= 0:
        return 0

    now = time.monotonic() if now is None else now

    with _lock:
        window = _window(f"{action}:{user_id}", now)
        if len(window) >= limit:
            return max(1, math.ceil(WINDOW_S - (now - window[0])))
        window.append(now)
    return 0


def reset() -> None:
    """Forget all counts (tests)."""
    with _lock:
        _hits.clear()
