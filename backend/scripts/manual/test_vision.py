from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents.vision_agent import vision_agent


# ============================================================
# IMAGE
# ============================================================

BACKEND_DIR = Path(__file__).resolve().parents[2]

IMAGE_PATH = (
    BACKEND_DIR
    / "uploads"
    / "test_ocr.png"
)


# ============================================================
# QUESTION
# ============================================================

query = (
    "Describe the main elements visible "
    "in this image."
)


# ============================================================
# TEST
# ============================================================

try:

    print("\n==============================")
    print("VISION AGENT TEST")
    print("==============================")

    print(
        f"Image path: {IMAGE_PATH}"
    )

    print(
        f"Image exists: "
        f"{IMAGE_PATH.exists()}"
    )

    if not IMAGE_PATH.exists():

        raise FileNotFoundError(
            f"Image not found:\n{IMAGE_PATH}"
        )

    result = vision_agent(
        image_path=str(IMAGE_PATH),
        query=query
    )

    print("\nANSWER:")
    print("------------------------------")

    print(
        result.get(
            "answer",
            ""
        )
    )

    print("\nERROR:")
    print("------------------------------")

    print(
        result.get(
            "error",
            ""
        )
    )

    print("\n==============================")

except Exception as e:

    print("\n==============================")
    print("VISION TEST FAILED")
    print("==============================")

    print(e)

    print("==============================")