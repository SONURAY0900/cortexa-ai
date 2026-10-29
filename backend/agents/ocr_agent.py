from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_REASONING_EFFORT, GROQ_TIMEOUT_S
from prompts.ocr_prompt import OCR_PROMPT
from tools.ocr_tool import extract_text_from_image


ocr_llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.2,
    api_key=GROQ_API_KEY,
    timeout=GROQ_TIMEOUT_S,
    max_retries=GROQ_MAX_RETRIES,
    reasoning_effort=GROQ_REASONING_EFFORT,
)


def ocr_agent(
    image_path: str,
    query: str
) -> dict:

    try:

        ocr_text = extract_text_from_image(
            image_path=image_path
        )

        if not ocr_text:

            return {
                "answer": (
                    "I could not detect readable text "
                    "in the uploaded image."
                ),
                "ocr_text": "",
                "error": ""
            }

        prompt = OCR_PROMPT.format(
            ocr_text=ocr_text,
            query=query
        )

        response = ocr_llm.invoke(
            prompt
        )

        return {
            "answer": response.content,
            "ocr_text": ocr_text,
            "error": ""
        }

    except Exception as e:

        return {
            "answer": "",
            "ocr_text": "",
            "error": str(e)
        }