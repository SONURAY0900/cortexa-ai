from tools.tavily_tool import search_tavily
from utils.retry import retry_tool


def tavily_agent(query: str) -> dict:

    result = retry_tool(
        tool_function=search_tavily,
        query=query,
        agent_name="TAVILY",
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