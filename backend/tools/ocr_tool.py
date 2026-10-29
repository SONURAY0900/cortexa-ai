import os
import io

from PIL import Image

from rag.ingestion.ocr_processor import ocr_upload


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
    language: str | None = None
) -> str:
    """
    Extract text from an image with OCR.space.

    Uses the same request logic as scanned PDF pages: automatic language,
    retries, and the fallback engine when engine 2 returns little text or
    text outside the scripts it auto-detects (Latin, Chinese). Engine 2
    alone turned Odia into Thai and Devanagari look-alikes.

    Returns "" when the image has no text; raises RuntimeError when OCR fails.
    """

    image_bytes = prepare_image(image_path)

    result = ocr_upload(("image.jpg", image_bytes, "image/jpeg"), language=language, label="image")

    if result.status == "failed":
        raise RuntimeError(f"OCR processing failed: {result.error or 'unknown error'}")

    return result.text.strip()
