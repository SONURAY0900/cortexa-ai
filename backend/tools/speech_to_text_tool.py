import os

from groq import Groq


SPEECH_MODEL = "whisper-large-v3-turbo"


def transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    language: str | None = None
) -> str:
    """
    Convert audio bytes to text using Groq Whisper Large V3 Turbo.
    """

    if not audio_bytes:
        raise ValueError("Audio data is empty.")

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not configured."
        )

    client = Groq(
        api_key=api_key
    )

    file_payload = (
        filename,
        audio_bytes
    )

    request_data = {
        "file": file_payload,
        "model": SPEECH_MODEL,
        "response_format": "json",
        "temperature": 0.0
    }

    if language:
        request_data["language"] = language

    transcription = client.audio.transcriptions.create(
        **request_data
    )

    return (transcription.text or "").strip()
