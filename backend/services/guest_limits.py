"""
Rate limit for guest chat (visitors without an account).

In memory, per server process: a rolling one-hour window counted per
guest id and per client IP. A restart resets the counts; that is fine for
its purpose (stopping one anonymous visitor from using up the shared Groq
quota), and it needs no database table.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from config import settings

WINDOW_S = 3600

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


def allow(guest_id: str, ip: str, now: float | None = None) -> bool:
    """Record one guest message; False if the guest or IP is over the limit."""

    limit = settings.GUEST_MESSAGES_PER_HOUR
    if limit <= 0:
        return False

    now = time.monotonic() if now is None else now
    keys = (f"guest:{guest_id}", f"ip:{ip}")

    with _lock:
        for key in keys:
            window = _hits[key]
            while window and now - window[0] >= WINDOW_S:
                window.popleft()
            if len(window) >= limit:
                return False
        for key in keys:
            _hits[key].append(now)
    return True


def reset() -> None:
    """Forget all counts (tests)."""
    with _lock:
        _hits.clear()
