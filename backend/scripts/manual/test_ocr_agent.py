from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents.ocr_agent import ocr_agent


BACKEND_DIR = Path(__file__).resolve().parents[2]

IMAGE_PATH = (
    BACKEND_DIR
    / "uploads"
    / "test_ocr.png"
)


query = "What is the embedding dimension mentioned in the image?"


try:

    print("\n==============================")
    print("OCR AGENT TEST")
    print("==============================")

    result = ocr_agent(
        image_path=str(IMAGE_PATH),
        query=query
    )

    print("\nOCR TEXT:")
    print("------------------------------")
    print(result.get("ocr_text", ""))

    print("\nANSWER:")
    print("------------------------------")
    print(result.get("answer", ""))

    print("\nERROR:")
    print("------------------------------")
    print(result.get("error", ""))

    print("==============================")

except Exception as e:

    print("\nOCR AGENT TEST FAILED")
    print(e)