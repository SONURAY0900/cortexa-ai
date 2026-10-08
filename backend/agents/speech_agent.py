from tools.speech_to_text_tool import transcribe_audio


def speech_to_text_agent(
    audio_bytes: bytes,
    filename: str,
    language: str | None = None
) -> dict:
    """
    Agent wrapper for speech-to-text processing.
    """

    try:
        text = transcribe_audio(
            audio_bytes=audio_bytes,
            filename=filename,
            language=language
        )

        return {
            "text": text,
            "error": ""
        }

    except Exception as e:
        return {
            "text": "",
            "error": str(e)
        }
