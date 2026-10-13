from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_TIMEOUT_S
from prompts.llm_prompt import LLM_PROMPT
from utils.logger import log_agent

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.2,
    api_key=GROQ_API_KEY,
    timeout=GROQ_TIMEOUT_S,
    max_retries=GROQ_MAX_RETRIES,
)


# Groq's free tier rejects any single request larger than its limit of
# 8,000 tokens per minute (HTTP 413), prompt included. At roughly four
# characters per token these caps keep a prompt to about 3,000 tokens.
HISTORY_MESSAGES = 6
HISTORY_MESSAGE_CHARS = 600
TOOL_RESULT_CHARS = 6000


def clip(text: str, limit: int) -> str:
    """Shorten text to at most `limit` characters, marking the cut."""
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def history_lines(chat_history: list | None) -> str:
    """The last few messages, each shortened, as ROLE: text lines."""
    return "".join(
        f"{(m.get('role') or '').upper()}: {clip(m.get('content') or '', HISTORY_MESSAGE_CHARS)}\n"
        for m in (chat_history or [])[-HISTORY_MESSAGES:]
    )


def build_prompt(
    query: str,
    tool_result: str = "",
    chat_history: list = None
) -> str:
    """
    Build the final LLM prompt with (recent, shortened) conversation history.
    """

    history_text = history_lines(chat_history)

    prompt = LLM_PROMPT.format(
        query=query,
        tool_result=clip(tool_result, TOOL_RESULT_CHARS)
    )

    if history_text:
        prompt = f"""
Previous Conversation:
{history_text}

{prompt}
"""

    return prompt


def generate_response(
    query: str,
    tool_result: str = "",
    chat_history: list = None
) -> str:

    log_agent(
        "LLM",
        "Generating response..."
    )

    prompt = build_prompt(
        query=query,
        tool_result=tool_result,
        chat_history=chat_history
    )

    response = llm.invoke(
        prompt
    )

    log_agent(
        "LLM",
        "Response generated successfully"
    )

    return response.content

def stream_response(
    query: str,
    tool_result: str = "",
    chat_history: list = None
):

    log_agent(
        "LLM",
        "Starting response streaming..."
    )

    prompt = build_prompt(
        query=query,
        tool_result=tool_result,
        chat_history=chat_history
    )

    chunk_count = 0

    for chunk in llm.stream(
        prompt
    ):

        if chunk.content:

            chunk_count += 1

            yield chunk.content


    log_agent(
        "LLM",
        f"Streaming completed ({chunk_count} chunks)"
    )