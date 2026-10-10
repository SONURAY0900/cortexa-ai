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


def build_prompt(
    query: str,
    tool_result: str = "",
    chat_history: list = None
) -> str:
    """
    Build the final LLM prompt with conversation history.
    """

    if chat_history is None:
        chat_history = []

    history_text = ""

    for message in chat_history:
        role = message.get("role", "")
        content = message.get("content", "")

        history_text += f"{role.upper()}: {content}\n"

    prompt = LLM_PROMPT.format(
        query=query,
        tool_result=tool_result
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