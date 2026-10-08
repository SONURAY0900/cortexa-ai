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


def search_tavily(query: str) -> dict:
    """
    Search Tavily and return content and source information.

    If Tavily cannot be loaded, return a controlled error
    instead of crashing the entire application.
    """

    if not TAVILY_AVAILABLE:
        return {
            "content": (
                "Tavily search is currently unavailable. "
                f"Reason: {TAVILY_IMPORT_ERROR}"
            ),
            "sources": []
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
            content = result.get("content", "")
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
            "content": f"Tavily search failed: {str(e)}",
            "sources": []
        }