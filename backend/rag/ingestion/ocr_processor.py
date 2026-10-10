"""
OCR of PDF pages / images with OCR.space.

Reliability rules
-----------------
* Every response is validated: HTTP status, IsErroredOnProcessing,
  OCRExitCode, ParsedResults and each result's FileParseExitCode.
* Failures are classified. Temporary ones (timeouts, connection errors,
  HTTP 429/5xx, OCR.space E5xx such as "E502: Error during OCR",
  malformed responses, per-page timeouts) are retried; permanent ones
  (invalid API key, unsupported file, validation errors) are not.
* Retries are bounded, use exponential backoff with jitter and honour
  Retry-After. They alternate between the primary and the fallback
  engine: OCR.space engine errors are often engine specific (a page that
  times out on engine 2 is usually read by engine 3 in seconds).
* Requests are spaced by a small minimum interval to respect the
  provider's rate limits.
* A page is reported as "success", "empty" (OCR worked, no text found)
  or "failed" (no attempt succeeded). Failures are never turned into
  empty successes.

Engine strategy (unchanged): try the primary engine first; if it returns
too little text, text that looks corrupted, or text mostly in scripts
outside the primary engine's documented auto-detection scope, the page
is also read with the fallback engine and the better text is kept. No
language is assumed.

OCR.space rejects uploads above the plan's file-size limit (1 MB on the
free plan); page images are compressed to fit. Settings are read at
call time so values loaded from .env after import are respected.
"""

import os
import random
import re
import threading
import time
from dataclasses import dataclass, field

import pymupdf
import requests

from rag.ingestion.text_quality import script_share, suspicious_ratio
from utils.logger import log_agent


OCR_API_URL = "https://api.ocr.space/parse/image"

MIN_OCR_TEXT_CHARS = 30
SUSPICIOUS_OCR_RATIO = 0.05


def _setting(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _max_upload_bytes() -> int:
    return int(_setting("OCR_SPACE_MAX_UPLOAD_BYTES", "1000000"))


def _float_setting(name: str, default: float) -> float:
    try:
        return float(_setting(name, str(default)))
    except ValueError:
        return default


# ==========================================================
# ERRORS AND RESULTS
# ==========================================================

class OcrError(RuntimeError):
    """
    One failed OCR request. `code` is a short, safe description
    (e.g. "E502", "HTTP 503", "timeout") suitable for logs and summaries.
    """

    def __init__(self, message: str, *, code: str, retryable: bool, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class OcrResult:
    text: str = ""
    status: str = "failed"          # "success" | "empty" | "failed"
    engine: str | None = None
    attempts: int = 0
    error: str | None = None        # short code of the last failure
    errors: list[str] = field(default_factory=list)


# ==========================================================
# RATE LIMITING (process-wide minimum spacing between requests)
# ==========================================================

_rate_lock = threading.Lock()
_last_request_at = 0.0


def _respect_rate_limit() -> None:
    global _last_request_at
    interval = _float_setting("OCR_SPACE_MIN_INTERVAL_S", 0.5)
    with _rate_lock:
        wait = _last_request_at + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


# ==========================================================
# PAGE RENDERING
# ==========================================================

def _render_page_image(
    file_path: str,
    page_number: int,
) -> tuple[str, bytes, str]:
    """
    Render one PDF page to an image that fits within the upload limit.

    PNG at OCR_RENDER_DPI (default 150) is kept when it fits. Otherwise
    JPEG is used, lowering resolution step by step until it fits. The
    page's own /Rotate is applied by the renderer; OCR.space is also
    asked to detect orientation.
    """

    dpi = int(_float_setting("OCR_RENDER_DPI", 150))

    with pymupdf.open(file_path) as document:

        if page_number < 1 or page_number > len(document):
            raise ValueError(f"Invalid page number: {page_number}")

        page = document[page_number - 1]

        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
        image_bytes = pixmap.tobytes("png")

        if len(image_bytes) <= _max_upload_bytes():
            return f"page_{page_number}.png", image_bytes, "image/png"

        for step in (dpi, 120, 100, 80, 60):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(step / 72, step / 72), alpha=False)
            image_bytes = pixmap.tobytes("jpg", jpg_quality=80)
            if len(image_bytes) <= _max_upload_bytes():
                return f"page_{page_number}.jpg", image_bytes, "image/jpeg"

        return f"page_{page_number}.jpg", image_bytes, "image/jpeg"


# ==========================================================
# ONE REQUEST
# ==========================================================

_PERMANENT_HINTS = ("api key", "apikey", "unauthor", "forbidden", "file type", "not supported",
                    "file size", "too large", "invalid parameter", "validation")
_RATE_HINTS = ("rate limit", "too many", "maximum", "times within", "quota")

# FileParseExitCode: 1 success, 0 file not found, -10 OCR engine parse
# error, -20 timeout, -30 validation error, -99 unknown error.
_PAGE_EXIT_RETRYABLE = {-10, -20, -99}


def _error_message(result: dict) -> str:
    message = result.get("ErrorMessage") or result.get("ErrorDetails") or ""
    if isinstance(message, list):
        message = " ".join(str(m) for m in message)
    return str(message).strip()


def _classify_processing_error(message: str) -> OcrError:
    lowered = message.lower()
    code_match = re.search(r"\bE(\d{3})\b", message)
    code = f"E{code_match.group(1)}" if code_match else "processing error"

    if any(h in lowered for h in _RATE_HINTS):
        return OcrError(message, code="rate limited", retryable=True, retry_after=10.0)
    if any(h in lowered for h in _PERMANENT_HINTS):
        return OcrError(message, code=code, retryable=False)
    if code_match and code_match.group(1).startswith("5"):
        return OcrError(message, code=code, retryable=True)          # e.g. E502
    if "timed out" in lowered or "timeout" in lowered:
        return OcrError(message, code="provider timeout", retryable=True)
    return OcrError(message or "Unknown OCR processing error.", code=code, retryable=True)


def _ocr_request(api_key: str, upload: tuple, engine: str, language: str) -> str:
    """
    Send one image to OCR.space. Returns the text (possibly empty when
    the page genuinely has none) or raises OcrError.
    """

    data = {
        "apikey": api_key,
        "language": language,
        "isOverlayRequired": "false",
        "detectOrientation": "true",
        "scale": "true",
        "OCREngine": engine,
    }
    timeout = (_float_setting("OCR_CONNECT_TIMEOUT_S", 10.0), _float_setting("OCR_READ_TIMEOUT_S", 90.0))

    _respect_rate_limit()

    try:
        response = requests.post(OCR_API_URL, files={"file": upload}, data=data, timeout=timeout)
    except requests.Timeout as e:
        raise OcrError(f"Request timed out: {e.__class__.__name__}", code="timeout", retryable=True)
    except requests.ConnectionError as e:
        raise OcrError(f"Connection failed: {e.__class__.__name__}", code="connection error", retryable=True)
    except requests.RequestException as e:
        raise OcrError(f"Request failed: {e.__class__.__name__}", code="request error", retryable=True)

    status = response.status_code
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        raise OcrError("Rate limited", code="HTTP 429", retryable=True,
                       retry_after=float(retry_after) if retry_after and retry_after.isdigit() else 10.0)
    if status in (401, 403):
        raise OcrError("OCR.space rejected the API key", code=f"HTTP {status}", retryable=False)
    if status >= 500:
        raise OcrError("OCR.space server error", code=f"HTTP {status}", retryable=True)
    if status != 200:
        raise OcrError("Unexpected OCR.space response", code=f"HTTP {status}", retryable=False)

    try:
        result = response.json()
    except ValueError:
        raise OcrError("Malformed OCR response (not JSON)", code="malformed response", retryable=True)

    if not isinstance(result, dict):
        raise OcrError("Malformed OCR response", code="malformed response", retryable=True)

    if result.get("IsErroredOnProcessing") or result.get("OCRExitCode") in (3, 4):
        raise _classify_processing_error(_error_message(result))

    parsed_results = result.get("ParsedResults")
    if not isinstance(parsed_results, list) or not parsed_results:
        raise OcrError("OCR response contained no results", code="malformed response", retryable=True)

    text_parts = []
    for parsed in parsed_results:
        if not isinstance(parsed, dict):
            raise OcrError("Malformed parsed result", code="malformed response", retryable=True)
        exit_code = parsed.get("FileParseExitCode", 1)
        try:
            exit_code = int(exit_code)
        except (TypeError, ValueError):
            exit_code = -99
        if exit_code != 1:
            message = parsed.get("ErrorMessage") or parsed.get("ErrorDetails") or f"page exit code {exit_code}"
            raise OcrError(str(message), code=f"page exit {exit_code}", retryable=exit_code in _PAGE_EXIT_RETRYABLE)
        text = parsed.get("ParsedText") or ""
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())

    return "\n\n".join(text_parts).strip()


# ==========================================================
# ENGINE SELECTION / QUALITY
# ==========================================================

def _primary_scripts() -> set[str]:
    """
    Scripts the primary engine is documented to auto-detect.
    OCR.space documents Engine 2 auto-detection for Latin-script
    languages and Chinese. Text in other scripts from the primary engine
    is double-checked with the fallback engine.
    """

    value = _setting("OCR_SPACE_PRIMARY_SCRIPTS", "LATIN,CJK")

    return {name.strip().upper() for name in value.split(",") if name.strip()}


def _outside_primary_scope(text: str) -> bool:
    scripts = _primary_scripts()
    return bool(scripts) and script_share(text, scripts) < 0.5


def _needs_fallback(text: str) -> bool:
    return (
        len(text.strip()) < MIN_OCR_TEXT_CHARS
        or suspicious_ratio(text) > SUSPICIOUS_OCR_RATIO
        or _outside_primary_scope(text)
    )


def _better(primary_text: str, fallback_text: str) -> str:
    if len(fallback_text.strip()) >= MIN_OCR_TEXT_CHARS and (
        len(primary_text.strip()) < MIN_OCR_TEXT_CHARS
        or _outside_primary_scope(primary_text)
        or suspicious_ratio(fallback_text) < suspicious_ratio(primary_text)
    ):
        return fallback_text
    return primary_text


def _backoff(attempt: int, retry_after: float | None) -> float:
    base = _float_setting("OCR_RETRY_BASE_DELAY_S", 1.0)
    cap = _float_setting("OCR_RETRY_MAX_DELAY_S", 8.0)
    delay = min(cap, base * (2 ** (attempt - 1)))
    delay = delay * (0.5 + random.random() / 2)        # jitter: 50–100%
    return max(delay, retry_after or 0.0)


# ==========================================================
# PUBLIC API
# ==========================================================

def ocr_upload(upload: tuple, language: str | None = None, label: str = "image") -> OcrResult:
    """
    OCR one prepared upload (filename, bytes, mime) with retries and
    engine fallback. Never raises for OCR failures; see OcrResult.
    """

    api_key = os.getenv("OCR_SPACE_API_KEY")
    if not api_key:
        return OcrResult(status="failed", error="not configured", errors=["not configured"])

    primary = _setting("OCR_SPACE_ENGINE", "2")
    primary_language = language or _setting("OCR_SPACE_LANGUAGE", "auto")
    fallback = _setting("OCR_SPACE_FALLBACK_ENGINE", "3")
    fallback_language = _setting("OCR_SPACE_FALLBACK_LANGUAGE", "auto")

    engines = [(primary, primary_language)]
    if fallback and fallback != primary:
        engines.append((fallback, fallback_language))

    max_attempts = max(1, int(_float_setting("OCR_MAX_ATTEMPTS", 4)))
    result = OcrResult()
    texts: dict[str, str] = {}          # engine -> text from a successful call
    attempt = 0

    while attempt < max_attempts:
        # Primary first; after a failure (or poor text) try the other engine.
        untried = [e for e in engines if e[0] not in texts]
        engine, lang = untried[attempt % len(untried)] if untried else engines[0]
        attempt += 1
        result.attempts = attempt

        try:
            text = _ocr_request(api_key, upload, engine, lang)
        except OcrError as error:
            result.error = error.code
            result.errors.append(f"engine {engine}: {error.code}")
            log_agent("OCR", f"{label}: engine {engine} attempt {attempt}/{max_attempts} failed ({error.code}); "
                             f"{'retrying' if error.retryable and attempt < max_attempts else 'giving up'}")
            if texts:
                break               # we already have text; the extra quality check failed
            if not error.retryable:
                if len(engines) > 1 and attempt < max_attempts and engine == primary:
                    continue        # a permanent error on one engine may not apply to the other
                break
            if attempt < max_attempts:
                time.sleep(_backoff(attempt, error.retry_after))
            continue

        texts[engine] = text
        result.engine = engine

        # Good text from the primary engine, or the fallback already had its turn.
        if not _needs_fallback(text) or len(texts) == len(engines):
            break

    if not texts:
        result.status = "failed"
        return result

    if primary in texts and len(texts) > 1:
        fallback_engine = engines[1][0]
        text = _better(texts[primary], texts[fallback_engine])
        result.engine = primary if text is texts[primary] else fallback_engine
    else:
        result.engine, text = next(iter(texts.items()))

    result.text = text.strip()
    result.status = "success" if result.text else "empty"
    result.error = None if result.text else result.error
    return result


def ocr_pdf_page(file_path: str, page_number: int, language: str | None = None) -> OcrResult:
    """Render one PDF page and OCR it (see ocr_upload)."""
    try:
        upload = _render_page_image(file_path, page_number)
    except Exception as e:
        log_agent("OCR", f"Page {page_number}: rendering failed: {e.__class__.__name__}")
        return OcrResult(status="failed", error="render error", errors=["render error"])
    return ocr_upload(upload, language, label=f"Page {page_number}")


def extract_text_with_ocr(
    file_path: str,
    page_number: int | None = None,
    language: str | None = None
) -> str:
    """
    Backward-compatible helper: OCR a PDF page (or a whole file) and
    return its text. Raises RuntimeError if OCR failed.
    """

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    if page_number is not None:
        result = ocr_pdf_page(file_path, page_number, language)
    else:
        with open(file_path, "rb") as file:
            result = ocr_upload((os.path.basename(file_path), file.read()), language)

    if result.status == "failed":
        raise RuntimeError(f"OCR failed: {result.error}")

    return result.text
