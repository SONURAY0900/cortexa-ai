from prompts.vision_prompt import VISION_PROMPT
from tools.vision_tool import analyze_image


def vision_agent(
    image_path: str,
    query: str
) -> dict:

    try:

        prompt = VISION_PROMPT.format(
            query=query
        )

        answer = analyze_image(
            image_path=image_path,
            query=prompt
        )

        return {
            "answer": answer,
            "error": ""
        }

    except Exception as e:

        return {
            "answer": "",
            "error": str(e)
        }