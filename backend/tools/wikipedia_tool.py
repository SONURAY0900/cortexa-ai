import requests


def search_wikipedia(query: str) -> dict:
    try:
        url = "https://en.wikipedia.org/w/api.php"

        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": 3,
            "utf8": 1
        }

        headers = {
            "User-Agent": "AgenticAIChatbot/1.0"
        }

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        results = data.get("query", {}).get("search", [])

        if not results:
            return {
                "content": "No Wikipedia results found.",
                "sources": []
            }

        formatted_results = []
        sources = []

        for result in results:

            title = result.get("title", "")
            snippet = result.get("snippet", "")

            page_url = (
                "https://en.wikipedia.org/wiki/"
                + title.replace(" ", "_")
            )

            formatted_results.append(
                f"Title: {title}\n"
                f"Content: {snippet}\n"
                f"URL: {page_url}"
            )

            sources.append({
                "title": title,
                "url": page_url
            })

        return {
            "content": "\n\n".join(formatted_results),
            "sources": sources
        }

    except requests.exceptions.RequestException as e:

        return {
            "content": "",
            "sources": [],
            "error": f"Wikipedia request failed: {str(e)}"
        }

    except ValueError as e:

        return {
            "content": "",
            "sources": [],
            "error": f"Wikipedia returned invalid JSON: {str(e)}"
        }

    except Exception as e:

        return {
            "content": "",
            "sources": [],
            "error": f"Wikipedia search failed: {str(e)}"
        }