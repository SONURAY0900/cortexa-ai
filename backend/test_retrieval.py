from rag.retriever import retrieve_chunks


DOCUMENT_ID = "deb9dbe5-9d0e-4e3a-aed2-e367d709a9bb"


queries = [
    "What is this PDF about?",
    "What is the candidate's education?",
    "What is the candidate's CGPA?",
    "Where did the candidate complete internships?",
    "What technical skills are mentioned?",
]


for query in queries:

    print("\n" + "=" * 80)
    print("QUERY:", query)
    print("=" * 80)

    results = retrieve_chunks(
        query=query,
        match_count=5,
        document_id=DOCUMENT_ID
    )

    if not results:
        print("NO RESULTS")
        continue

    for i, result in enumerate(results, start=1):

        print(f"\n--- RESULT {i} ---")

        print(
            "Similarity:",
            result.get("similarity")
        )

        print(
            "Vector Score:",
            result.get("vector_score")
        )

        print(
            "Keyword Score:",
            result.get("keyword_score")
        )

        print(
            "Hybrid Score:",
            result.get("hybrid_score")
        )

        print(
            "Page:",
            result.get("page_number")
        )

        print(
            "Chunk Index:",
            result.get("chunk_index")
        )

        print("\nCONTENT:")
        print(result.get("content"))