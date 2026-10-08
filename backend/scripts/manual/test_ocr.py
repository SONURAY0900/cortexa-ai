from dotenv import load_dotenv

load_dotenv()

from tools.ocr_tool import extract_text_from_image


IMAGE_PATH = "uploads/images/test_ocr.png"


try:

    text = extract_text_from_image(
        image_path=IMAGE_PATH
    )

    print("\n==============================")
    print("OCR RESULT")
    print("==============================\n")

    if text:
        print(text)
    else:
        print("No text detected.")

    print("\n==============================")

except Exception as e:

    print("\nOCR TEST FAILED")
    print(e)