def clean_text(text: str) -> str:
    """
    Clean and normalize user input.
    """
    if not text:
        return ""

    return " ".join(text.strip().split())


def get_text_from_response(response) -> str:
    """
    Convert different response types into plain text.
    """

    if response is None:
        return ""

    if isinstance(response, str):
        return response

    if hasattr(response, "content"):
        return str(response.content)

    return str(response)


def format_tool_result(tool_name: str, result) -> str:
    """
    Format external tool output before sending it to the LLM.
    """

    result_text = get_text_from_response(result)

    return f"""
Tool: {tool_name}

Retrieved Information:
{result_text}
""".strip()