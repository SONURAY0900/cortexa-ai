"""
Local upload storage.

Files are written under backend/uploads/<kind>/ with a server-generated
name, and are only ever read or deleted through paths that resolve
inside UPLOAD_ROOT. Client-supplied paths are never opened.
"""

import uuid
from pathlib import Path

from utils.logger import log_agent


BACKEND_DIR = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = (BACKEND_DIR / "uploads").resolve()

PDF_UPLOAD_DIR = UPLOAD_ROOT / "pdfs"
IMAGE_UPLOAD_DIR = UPLOAD_ROOT / "images"

for directory in (PDF_UPLOAD_DIR, IMAGE_UPLOAD_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def safe_display_name(filename: str, max_chars: int = 120) -> str:
    """
    Original file name for display only: last path component,
    control characters removed, length limited.
    """

    name = Path(filename or "").name
    name = "".join(ch for ch in name if ch.isprintable())

    return name[:max_chars] or "file"


def save_upload(directory: Path, content: bytes, extension: str) -> Path:
    """
    Save bytes under a random server-side name. The original name
    is never part of the path.
    """

    path = directory / f"{uuid.uuid4().hex}{extension.lower()}"
    path.write_bytes(content)

    return path


def resolve_inside_uploads(stored_path: str | None) -> Path | None:
    """
    Return the path only if it points to an existing file inside
    UPLOAD_ROOT, otherwise None.
    """

    if not stored_path:
        return None

    try:
        path = Path(stored_path).resolve()
    except (OSError, ValueError):
        return None

    if UPLOAD_ROOT not in path.parents or not path.is_file():
        return None

    return path


def delete_stored_file(stored_path: str | None) -> bool:
    path = resolve_inside_uploads(stored_path)

    if not path:
        return False

    try:
        path.unlink()
        return True
    except OSError as e:
        log_agent("STORAGE", f"Could not delete {path.name}: {e}")
        return False


def directory_usage(directory: Path) -> dict:
    files = [p for p in directory.rglob("*") if p.is_file()]

    return {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }
