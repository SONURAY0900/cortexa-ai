"""
Start the app locally, also on Windows PCs where Smart App Control blocks
two small native modules.

    cd backend
    python -m scripts.run_local          # http://127.0.0.1:8000

Smart App Control can refuse to load the compiled parts of:

  * mmh3        imported by FastEmbed, but only used by its sparse models
                (BM25/BM42), which this app does not use;
  * uuid_utils  used by LangChain/LangSmith only to create trace ids
                (uuid7).

If importing one of them fails, a pure-Python stand-in is registered
instead: mmh3 raises if anything actually calls it, and uuid7 is built
from Python's own uuid module. Windows settings and installed packages are
not changed. Where the real modules load (Linux, Render) they are used and
this behaves exactly like `uvicorn main:app`.

Auto-reload is off: the stand-ins exist only in this process.
"""

import os
import sys
import time
import types
import uuid


def _shim_mmh3() -> bool:
    try:
        import mmh3  # noqa: F401
        return False
    except ImportError:
        pass

    class _Unavailable(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            raise RuntimeError(
                "mmh3 is blocked on this machine. Only FastEmbed's sparse "
                "models need it, and this app does not use them."
            )

    sys.modules["mmh3"] = _Unavailable("mmh3")
    return True


def _uuid7(*_args, **_kwargs) -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit Unix milliseconds, version 7, random bits."""
    millis = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (millis << 80) | (0x7 << 76) | (((rand >> 64) & 0xFFF) << 64) | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return uuid.UUID(int=value)


def _shim_uuid_utils() -> bool:
    try:
        from uuid_utils.compat import uuid7  # noqa: F401
        return False
    except ImportError:
        pass

    compat = types.ModuleType("uuid_utils.compat")
    compat.uuid7 = _uuid7
    package = types.ModuleType("uuid_utils")
    package.compat = compat
    package.uuid7 = _uuid7
    package.UUID = uuid.UUID
    sys.modules["uuid_utils"] = package
    sys.modules["uuid_utils.compat"] = compat
    return True


def main() -> None:
    shimmed = [name for name, applied in (("mmh3", _shim_mmh3()), ("uuid_utils", _shim_uuid_utils())) if applied]
    if shimmed:
        print(f"[run_local] Windows blocked {', '.join(shimmed)}; using a pure-Python stand-in.")

    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(
        "[run_local] Loading the app and the embedding model. This can take a minute "
        "on Windows; wait for 'Application startup complete' before opening "
        f"http://{host}:{port} (don't press Ctrl+C before then).",
        flush=True,
    )
    uvicorn.run("main:app", host=host, port=port)


if __name__ == "__main__":
    main()
