from config.settings import TAVILY_API_KEY


# Tavily is optional.
# If Windows blocks tiktoken, the application should still start.
try:
    from tavily import TavilyClient

    tavily_client = TavilyClient(
        api_key=TAVILY_API_KEY
    )

    TAVILY_AVAILABLE = True
    TAVILY_IMPORT_ERROR = None

except Exception as e:
    tavily_client = None
    TAVILY_AVAILABLE = False
    TAVILY_IMPORT_ERROR = str(e)


# Characters kept from each search result's extract.
RESULT_CHARS = 900


def search_tavily(query: str) -> dict:
    """
    Search Tavily and return content and source information.

    If Tavily cannot be loaded, return a controlled error
    instead of crashing the entire application.
    """

    if not TAVILY_AVAILABLE:
        return {
            "content": "",
            "sources": [],
            "error": (
                "Tavily search is currently unavailable. "
                f"Reason: {TAVILY_IMPORT_ERROR}"
            )
        }

    try:
        response = tavily_client.search(
            query=query,
            search_depth="advanced",
            max_results=5
        )

        results = response.get("results", [])

        if not results:
            return {
                "content": "No search results found.",
                "sources": []
            }

        formatted_results = []
        sources = []

        for result in results:
            title = result.get("title", "")
            # Advanced search can return long extracts; five full ones
            # pushed prompts past Groq's per-request token limit.
            content = (result.get("content", "") or "")[:RESULT_CHARS]
            url = result.get("url", "")

            formatted_results.append(
                f"Title: {title}\n"
                f"Content: {content}\n"
                f"URL: {url}"
            )

            if url:
                sources.append({
                    "title": title,
                    "url": url
                })

        return {
            "content": "\n\n".join(formatted_results),
            "sources": sources
        }

    except Exception as e:
        return {
            "content": "",
            "sources": [],
            "error": f"Tavily search failed: {str(e)}"
        }