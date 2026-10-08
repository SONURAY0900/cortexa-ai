from tools.wikipedia_tool import search_wikipedia
from utils.retry import retry_tool


def wikipedia_agent(query: str) -> dict:

    result = retry_tool(
        tool_function=search_wikipedia,
        query=query,
        agent_name="WIKIPEDIA",
        max_retries=2,
        delay=1.0
    )

    return {
        "content": result.get(
            "content",
            ""
        ),
        "sources": result.get(
            "sources",
            []
        ),
        "error": result.get(
            "error",
            ""
        )
    }