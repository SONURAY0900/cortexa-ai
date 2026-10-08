from tools.arxiv_tool import search_arxiv
from utils.retry import retry_tool


def arxiv_agent(query: str) -> dict:

    result = retry_tool(
        tool_function=search_arxiv,
        query=query,
        agent_name="ARXIV",
        max_retries=2,
        delay=1.0
    )

    return {
        "content": result.get("content", ""),
        "sources": result.get("sources", []),
        "error": result.get("error", "")
    }