import os
import requests
import pymupdf

from rag.ingestion.text_quality import script_share, suspicious_ratio


OCR_API_URL = "https://api.ocr.space/parse/image"

# ==========================================================
# OCR CONFIGURATION (all overridable through environment)
# ==========================================================
#
# OCR.space documents:
#   - Engine 2: language "auto" enables automatic language detection
#     (documented for Latin-script languages and Chinese; in testing it
#     also read Devanagari but dropped Odia text).
#   - Engine 3: 200+ languages with automatic detection ("auto"),
#     slower, and a separate (smaller) monthly quota on the free plan.
#
# Strategy: try the primary engine first; if it returns too little text,
# text that looks corrupted, or text mostly in scripts outside the
# primary engine's documented auto-detection scope, retry the page once
# with the fallback engine. No language is assumed.

# OCR.space rejects uploads above the plan's file-size limit (1 MB on the
# free plan) and then returns no text. Page images are compressed to fit.
#
# Settings are read at call time so values loaded from .env after import
# are respected.


def _setting(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _max_upload_bytes() -> int:
    return int(_setting("OCR_SPACE_MAX_UPLOAD_BYTES", "1000000"))

MIN_OCR_TEXT_CHARS = 30
SUSPICIOUS_OCR_RATIO = 0.05


def _render_page_image(
    file_path: str,
    page_number: int,
) -> tuple[str, bytes, str]:
    """
    Render one PDF page to an image that fits within the upload limit.

    PNG at 150 DPI is kept when it fits (unchanged behaviour). Otherwise
    JPEG is used, lowering resolution step by step until it fits.
    """

    document = pymupdf.open(file_path)

    try:

        if (
            page_number < 1
            or page_number > len(document)
        ):
            raise ValueError(
                f"Invalid page number: {page_number}"
            )

        page = document[page_number - 1]

        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(150 / 72, 150 / 72),
            alpha=False
        )

        image_bytes = pixmap.tobytes("png")

        if len(image_bytes) <= _max_upload_bytes():
            return (
                f"page_{page_number}.png",
                image_bytes,
                "image/png",
            )

        for dpi in (150, 120, 100, 80, 60):

            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
                alpha=False
            )

            image_bytes = pixmap.tobytes(
                "jpg",
                jpg_quality=80
            )

            if len(image_bytes) <= _max_upload_bytes():
                return (
                    f"page_{page_number}.jpg",
                    image_bytes,
                    "image/jpeg",
                )

        return (
            f"page_{page_number}.jpg",
            image_bytes,
            "image/jpeg",
        )

    finally:

        document.close()


def _ocr_request(
    api_key: str,
    upload: tuple,
    engine: str,
    language: str,
) -> str:
    """
    Send one image to OCR.space and return the parsed text.
    """

    data = {
        "apikey": api_key,
        "language": language,
        "isOverlayRequired": "false",
        "detectOrientation": "true",
        "scale": "true",
        "OCREngine": engine
    }

    try:

        response = requests.post(
            OCR_API_URL,
            files={"file": upload},
            data=data,
            timeout=180
        )

        # ==================================================
        # HTTP ERROR HANDLING
        # ==================================================

        if response.status_code != 200:

            print(
                "\n[OCR] OCR.space response:"
            )

            print(
                response.text
            )

            response.raise_for_status()

        result = response.json()

    except requests.RequestException as e:

        raise RuntimeError(
            f"OCR service request failed: {str(e)}"
        )

    except ValueError as e:

        raise RuntimeError(
            f"OCR processing error: {str(e)}"
        )

    # ======================================================
    # OCR.SPACE PROCESSING ERROR
    # ======================================================

    if result.get("IsErroredOnProcessing"):

        error_message = result.get(
            "ErrorMessage",
            "Unknown OCR processing error."
        )

        if isinstance(error_message, list):

            error_message = " ".join(
                str(message)
                for message in error_message
            )

        raise RuntimeError(
            f"OCR processing failed: {error_message}"
        )

    # ======================================================
    # EXTRACT OCR TEXT
    # ======================================================

    parsed_results = result.get(
        "ParsedResults",
        []
    ) or []

    text_parts = []

    for parsed_result in parsed_results:

        parsed_text = parsed_result.get(
            "ParsedText",
            ""
        )

        if parsed_text:
            text_parts.append(
                parsed_text.strip()
            )

    return "\n\n".join(
        text_parts
    ).strip()


def _primary_scripts() -> set[str]:
    """
    Scripts the primary engine is documented to auto-detect.
    OCR.space documents Engine 2 auto-detection for Latin-script
    languages and Chinese. Text in other scripts from the primary engine
    is double-checked with the fallback engine.
    """

    value = _setting("OCR_SPACE_PRIMARY_SCRIPTS", "LATIN,CJK")

    return {
        name.strip().upper()
        for name in value.split(",")
        if name.strip()
    }


def _outside_primary_scope(text: str) -> bool:
    scripts = _primary_scripts()
    return bool(scripts) and script_share(text, scripts) < 0.5


def _needs_fallback(text: str) -> bool:
    return (
        len(text.strip()) < MIN_OCR_TEXT_CHARS
        or suspicious_ratio(text) > SUSPICIOUS_OCR_RATIO
        or _outside_primary_scope(text)
    )


def extract_text_with_ocr(
    file_path: str,
    page_number: int | None = None,
    language: str | None = None
) -> str:
    """
    Extract text using OCR.space.

    If page_number is provided, the specific PDF page is rendered to an
    image (compressed to fit the upload limit) and sent to OCR.space.

    The primary engine/language come from configuration (default:
    Engine 2 with automatic language detection). If the result is empty
    or looks corrupted, the page is retried once with the fallback engine
    (default: Engine 3, automatic detection, 200+ languages).
    """

    api_key = os.getenv("OCR_SPACE_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OCR_SPACE_API_KEY is not configured."
        )

    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"File not found: {file_path}"
        )

    # ==================================================
    # BUILD UPLOAD
    # ==================================================

    if page_number is not None:

        upload = _render_page_image(
            file_path,
            page_number
        )

    else:

        with open(file_path, "rb") as file:
            upload = (
                os.path.basename(file_path),
                file.read(),
            )

    # ==================================================
    # PRIMARY OCR
    # ==================================================

    engine = _setting("OCR_SPACE_ENGINE", "2")
    primary_language = language or _setting("OCR_SPACE_LANGUAGE", "auto")
    fallback_engine = _setting("OCR_SPACE_FALLBACK_ENGINE", "3")
    fallback_language = _setting("OCR_SPACE_FALLBACK_LANGUAGE", "auto")

    text = _ocr_request(
        api_key,
        upload,
        engine,
        primary_language
    )

    # ==================================================
    # FALLBACK OCR
    # ==================================================

    if (
        fallback_engine
        and fallback_engine != engine
        and _needs_fallback(text)
    ):

        print(
            f"[OCR] Engine {engine} result is empty or "
            f"looks corrupted; retrying with engine "
            f"{fallback_engine}."
        )

        try:

            fallback_text = _ocr_request(
                api_key,
                upload,
                fallback_engine,
                fallback_language
            )

        except RuntimeError as error:

            print(
                f"[OCR] Fallback engine failed: {error}"
            )

            fallback_text = ""

        if len(fallback_text.strip()) >= MIN_OCR_TEXT_CHARS and (
            len(text.strip()) < MIN_OCR_TEXT_CHARS
            or _outside_primary_scope(text)
            or suspicious_ratio(fallback_text) < suspicious_ratio(text)
        ):
            text = fallback_text

    return text
