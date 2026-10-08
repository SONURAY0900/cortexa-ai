import os
import io

import requests
from PIL import Image


OCR_API_URL = "https://api.ocr.space/parse/image"

# Maximum image dimensions sent to OCR.space
MAX_WIDTH = 2000
MAX_HEIGHT = 2000

# JPEG quality
JPEG_QUALITY = 85


def prepare_image(
    image_path: str
) -> bytes:
    """
    Resize and compress the image before
    sending it to OCR.space.
    """

    if not os.path.exists(image_path):

        raise FileNotFoundError(
            f"Image file not found: {image_path}"
        )

    try:

        image = Image.open(image_path)

        print(
            f"[OCR] Original image size: "
            f"{image.width}x{image.height}"
        )

        # Convert image to RGB
        # This also handles PNG transparency.
        if image.mode != "RGB":

            image = image.convert("RGB")

        # Resize only if image is too large.
        image.thumbnail(
            (
                MAX_WIDTH,
                MAX_HEIGHT
            ),
            Image.Resampling.LANCZOS
        )

        print(
            f"[OCR] Processed image size: "
            f"{image.width}x{image.height}"
        )

        # Store compressed JPEG in memory.
        image_buffer = io.BytesIO()

        image.save(
            image_buffer,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True
        )

        image_buffer.seek(0)

        image_bytes = image_buffer.getvalue()

        print(
            f"[OCR] Processed image size: "
            f"{len(image_bytes) / 1024:.2f} KB"
        )

        return image_bytes

    except Exception as e:

        raise RuntimeError(
            f"Image preparation failed: {str(e)}"
        )


def extract_text_from_image(
    image_path: str,
    language: str = "eng"
) -> str:
    """
    Extract text from an image using OCR.space.
    """

    api_key = os.getenv(
        "OCR_SPACE_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "OCR_SPACE_API_KEY is not configured."
        )

    # ============================================================
    # PREPARE IMAGE
    # ============================================================

    image_bytes = prepare_image(
        image_path
    )

    # ============================================================
    # SEND IMAGE TO OCR.SPACE
    # ============================================================

    try:

        files = {
            "file": (
                "image.jpg",
                image_bytes,
                "image/jpeg"
            )
        }

        data = {
            "apikey": api_key,
            "language": language,
            "isOverlayRequired": "false",
            "detectOrientation": "true",
            "scale": "true",
            "OCREngine": "2"
        }

        print(
            "[OCR] Sending image to OCR.space..."
        )

        response = requests.post(
            OCR_API_URL,
            files=files,
            data=data,
            timeout=120
        )

        print(
            f"[OCR] Response status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "[OCR] OCR.space response:"
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
            f"Invalid OCR response: {str(e)}"
        )

    # ============================================================
    # CHECK OCR ERROR
    # ============================================================

    if result.get(
        "IsErroredOnProcessing"
    ):

        error_message = result.get(
            "ErrorMessage",
            "Unknown OCR processing error."
        )

        if isinstance(
            error_message,
            list
        ):

            error_message = " ".join(
                str(message)
                for message in error_message
            )

        raise RuntimeError(
            f"OCR processing failed: "
            f"{error_message}"
        )

    # ============================================================
    # EXTRACT OCR TEXT
    # ============================================================

    parsed_results = result.get(
        "ParsedResults",
        []
    )

    if not parsed_results:

        return ""

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