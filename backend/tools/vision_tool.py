import base64
import mimetypes
import os

from groq import Groq


VISION_MODEL = "qwen/qwen3.8-27b"


def encode_image(image_path: str) -> tuple[str, str]:

    if not os.path.exists(image_path):
        raise FileNotFoundError(
            f"Image file not found: {image_path}"
        )

    mime_type, _ = mimetypes.guess_type(image_path)

    if not mime_type:
        mime_type = "image/jpeg"

    if not mime_type.startswith("image/"):
        raise ValueError(
            f"Unsupported image type: {mime_type}"
        )

    with open(image_path, "rb") as image_file:
        image_base64 = base64.b64encode(
            image_file.read()
        ).decode("utf-8")

    return image_base64, mime_type


def analyze_image(
    image_path: str,
    query: str
) -> str:

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not configured."
        )

    image_base64, mime_type = encode_image(
        image_path
    )

    client = Groq(
        api_key=api_key
    )

    response = client.chat.completions.create(

        model=VISION_MODEL,

        messages=[
            {
                "role": "user",
                "content": [

                    {
                        "type": "text",
                        "text": query
                    },

                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{mime_type};base64,"
                                f"{image_base64}"
                            )
                        }
                    }

                ]
            }
        ],

        temperature=0.2,

        max_completion_tokens=1024
    )

    return response.choices[0].message.content