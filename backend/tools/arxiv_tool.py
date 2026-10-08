import arxiv


def search_arxiv(query: str) -> dict:
    try:
        client = arxiv.Client()

        search = arxiv.Search(
            query=query,
            max_results=5,
            sort_by=arxiv.SortCriterion.Relevance
        )

        results = client.results(search)

        formatted_results = []
        sources = []

        for result in results:
            title = result.title.strip()
            authors = ", ".join(
                author.name for author in result.authors
            )

            abstract = result.summary.strip()
            url = result.entry_id

            formatted_results.append(
                f"Title: {title}\n"
                f"Authors: {authors}\n"
                f"Published: {result.published}\n"
                f"Abstract: {abstract}\n"
                f"URL: {url}"
            )

            sources.append({
                "title": title,
                "url": url
            })

        if not formatted_results:
            return {
                "content": "No arXiv papers found.",
                "sources": []
            }

        return {
            "content": "\n\n".join(formatted_results),
            "sources": sources
        }

    except Exception as e:
        return {
            "content": "",
            "sources": [],
            "error": f"arXiv search failed: {str(e)}"
        }