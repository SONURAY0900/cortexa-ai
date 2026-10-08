import re


# ============================================================
# BROAD DOCUMENT QUERY PATTERNS
# ============================================================

BROAD_QUERY_PATTERNS = [
    r"\bwhat is this pdf about\b",
    r"\bwhat is this document about\b",
    r"\bwhat does this pdf contain\b",
    r"\bwhat does this document contain\b",
    r"\bwhat is the document about\b",
    r"\bwhat is the pdf about\b",
    r"\bgive me an overview\b",
    r"\bgive an overview\b",
    r"\bgive me a summary\b",
    r"\bgive a summary\b",
    r"\bprovide an overview\b",
    r"\bprovide a summary\b",
    r"\bsummarize this pdf\b",
    r"\bsummarize this document\b",
    r"\bsummary of this pdf\b",
    r"\bsummary of this document\b",
    r"\boverview of this pdf\b",
    r"\boverview of this document\b",
]


# ============================================================
# LIST QUERY PATTERNS
# ============================================================

LIST_QUERY_PATTERNS = [
    r"^what are\b",
    r"^what .+ are mentioned\b",
    r"^what .+ are listed\b",
    r"^which .+ are mentioned\b",
    r"^which .+ are listed\b",
    r"^which .+ are available\b",
    r"\bwhat .+ has .+ worked on\b",
    r"\bwhat .+ has .+ worked with\b",
    r"\bwhat .+ does .+ have\b",
    r"\blist\b",
    r"\blist all\b",
    r"\ball .+ mentioned\b",
    r"\ball .+ listed\b",
]


# ============================================================
# QUERY STOP WORDS
# ============================================================

QUERY_STOP_WORDS = {
    # Common English/question words
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "give",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "please",
    "tell",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "what",
    "which",
    "who",
    "will",
    "with",
    "would",
    "you",
    "your",

    # Generic document/question words
    "document",
    "pdf",
    "information",
    "content",
    "mentioned",
    "listed",
    "provide",
    "provided",
    "main",
    "important",
    "key",
    "overview",
    "summary",
    "summarize",
    "kind",

    # Low-information conversational words
    "about",
}


# ============================================================
# GENERIC QUERY EXPANSION GROUPS
# ============================================================

QUERY_EXPANSIONS = {

    "technology": [
        "technology",
        "technologies",
        "technical",
        "tools",
        "software",
        "framework",
        "frameworks",
        "library",
        "libraries",
        "programming",
        "programming language",
        "programming languages",
    ],

    "technologies": [
        "technology",
        "technologies",
        "technical",
        "tools",
        "software",
        "framework",
        "frameworks",
        "library",
        "libraries",
        "programming",
        "programming language",
        "programming languages",
    ],

    "technical": [
        "technical",
        "technology",
        "technologies",
        "tools",
        "software",
        "frameworks",
        "libraries",
        "programming",
    ],

    "framework": [
        "framework",
        "frameworks",
        "library",
        "libraries",
        "software",
        "technology",
    ],

    "frameworks": [
        "framework",
        "frameworks",
        "library",
        "libraries",
        "software",
        "technology",
    ],

    "library": [
        "library",
        "libraries",
        "framework",
        "frameworks",
        "software",
        "technology",
    ],

    "libraries": [
        "library",
        "libraries",
        "framework",
        "frameworks",
        "software",
        "technology",
    ],

    "tool": [
        "tool",
        "tools",
        "software",
        "utility",
        "utilities",
    ],

    "tools": [
        "tool",
        "tools",
        "software",
        "utility",
        "utilities",
    ],

    "api": [
        "api",
        "apis",
        "application programming interface",
        "service",
        "services",
        "endpoint",
        "endpoints",
    ],

    "apis": [
        "api",
        "apis",
        "application programming interface",
        "service",
        "services",
        "endpoint",
        "endpoints",
    ],

    "backend": [
        "backend",
        "server",
        "server-side",
        "api",
        "apis",
        "service",
        "services",
        "database",
        "databases",
    ],

    "frontend": [
        "frontend",
        "front-end",
        "client",
        "client-side",
        "interface",
        "user interface",
        "web interface",
    ],

    "database": [
        "database",
        "databases",
        "storage",
        "data storage",
        "sql",
        "query",
    ],

    "databases": [
        "database",
        "databases",
        "storage",
        "data storage",
        "sql",
        "query",
    ],

    "programming": [
        "programming",
        "programming language",
        "programming languages",
        "code",
        "coding",
        "software development",
    ],

    "language": [
        "language",
        "languages",
        "programming",
        "programming language",
        "programming languages",
    ],

    "languages": [
        "language",
        "languages",
        "programming",
        "programming language",
        "programming languages",
    ],

    "project": [
        "project",
        "projects",
        "application",
        "applications",
        "system",
        "systems",
        "implementation",
    ],

    "projects": [
        "project",
        "projects",
        "application",
        "applications",
        "system",
        "systems",
        "implementation",
    ],

    "experience": [
        "experience",
        "work",
        "worked",
        "employment",
        "internship",
        "internships",
        "professional experience",
    ],

    "internship": [
        "internship",
        "internships",
        "intern",
        "work experience",
        "professional experience",
    ],

    "internships": [
        "internship",
        "internships",
        "intern",
        "work experience",
        "professional experience",
    ],

    "achievement": [
        "achievement",
        "achievements",
        "award",
        "awards",
        "certification",
        "certifications",
        "recognition",
    ],

    "achievements": [
        "achievement",
        "achievements",
        "award",
        "awards",
        "certification",
        "certifications",
        "recognition",
    ],

    "education": [
        "education",
        "degree",
        "degrees",
        "qualification",
        "qualifications",
        "academic",
        "academics",
        "university",
        "college",
    ],

    "method": [
        "method",
        "methods",
        "methodology",
        "approach",
        "technique",
        "techniques",
        "procedure",
    ],

    "methods": [
        "method",
        "methods",
        "methodology",
        "approach",
        "technique",
        "techniques",
        "procedure",
    ],

    "result": [
        "result",
        "results",
        "finding",
        "findings",
        "outcome",
        "outcomes",
        "conclusion",
        "conclusions",
    ],

    "results": [
        "result",
        "results",
        "finding",
        "findings",
        "outcome",
        "outcomes",
        "conclusion",
        "conclusions",
    ],

    "topic": [
        "topic",
        "topics",
        "subject",
        "subjects",
        "theme",
        "themes",
        "area",
        "areas",
        "section",
        "sections",
    ],

    "topics": [
        "topic",
        "topics",
        "subject",
        "subjects",
        "theme",
        "themes",
        "area",
        "areas",
        "section",
        "sections",
    ],

    "author": [
        "author",
        "authors",
        "writer",
        "researcher",
        "researchers",
    ],

    "date": [
        "date",
        "year",
        "period",
        "time",
        "timeline",
    ],
}


# ============================================================
# GENERIC BROAD DOCUMENT TERMS
# ============================================================

BROAD_GENERIC_TERMS = [
    "summary",
    "overview",
    "topic",
    "subject",
    "purpose",
    "information",
    "sections",
]


# ============================================================
# NORMALIZE QUERY
# ============================================================

def normalize_query(
    query: str,
) -> str:
    """
    Normalize a query for classification and expansion.

    The original user query is never modified by this
    function when returned to the caller.
    """

    if not query:
        return ""

    query = query.strip()

    # Normalize Unicode apostrophe.
    query = query.replace(
        "’",
        "'",
    )

    # Normalize different dash characters.
    query = re.sub(
        r"[-–—]+",
        " ",
        query,
    )

    # Normalize whitespace.
    query = re.sub(
        r"\s+",
        " ",
        query,
    )

    return query.lower()


# ============================================================
# EXTRACT IMPORTANT QUERY TERMS
# ============================================================

def extract_query_terms(
    query: str,
) -> list[str]:
    """
    Extract meaningful terms from the user's query.

    Example:

        What backend technologies are mentioned?

    becomes:

        ["backend", "technologies"]
    """

    normalized_query = normalize_query(
        query
    )

    if not normalized_query:
        return []

    tokens = re.findall(
        r"[a-zA-Z0-9]+",
        normalized_query,
    )

    meaningful = []

    for token in tokens:

        if token in QUERY_STOP_WORDS:
            continue

        if len(token) <= 1:
            continue

        if token not in meaningful:
            meaningful.append(
                token
            )

    return meaningful


# ============================================================
# BROAD DOCUMENT QUERY
# ============================================================

def is_broad_document_query(
    query: str,
) -> bool:
    """
    Check whether the query is asking for a general
    document overview or summary.
    """

    normalized_query = normalize_query(
        query
    )

    if not normalized_query:
        return False

    for pattern in BROAD_QUERY_PATTERNS:

        if re.search(
            pattern,
            normalized_query,
        ):
            return True

    return False


# ============================================================
# LIST QUERY
# ============================================================

def is_list_query(
    query: str,
) -> bool:
    """
    Check whether the query is likely to require
    multiple pieces of information.
    """

    normalized_query = normalize_query(
        query
    )

    if not normalized_query:
        return False

    for pattern in LIST_QUERY_PATTERNS:

        if re.search(
            pattern,
            normalized_query,
        ):
            return True

    return False


# ============================================================
# BUILD BROAD QUERIES
# ============================================================

def build_broad_queries(
    query: str,
) -> list[str]:
    """
    Build information-preserving retrieval queries
    for broad document questions.

    IMPORTANT:

    The original query is always preserved.

    Generic broad terms are added only as supporting
    retrieval signals.

    Query-specific meaningful terms are also preserved.
    """

    original_query = query.strip()

    if not original_query:
        return []

    queries = [
        original_query,
    ]

    query_terms = extract_query_terms(
        original_query
    )

    # --------------------------------------------------------
    # CASE 1:
    # Completely generic document query.
    #
    # Example:
    #
    # What is this document about?
    #
    # There are no useful document-specific terms.
    # --------------------------------------------------------

    if not query_terms:

        generic_query = " ".join(
            BROAD_GENERIC_TERMS
        )

        if generic_query:
            queries.append(
                generic_query
            )

        return queries

    # --------------------------------------------------------
    # CASE 2:
    # Query contains meaningful concepts.
    #
    # Example:
    #
    # Give an overview of the candidate's
    # technical background.
    #
    # Preserve:
    #
    # candidate
    # technical
    # background
    #
    # Then add related concepts.
    # --------------------------------------------------------

    focused_terms = []

    for term in query_terms:

        if term not in focused_terms:

            focused_terms.append(
                term
            )

        if term in QUERY_EXPANSIONS:

            for expanded_term in QUERY_EXPANSIONS[term]:

                if expanded_term not in focused_terms:

                    focused_terms.append(
                        expanded_term
                    )

    focused_query = " ".join(
        focused_terms
    )

    if focused_query:

        broad_terms = " ".join(
            BROAD_GENERIC_TERMS
        )

        queries.append(
            focused_query
            + " "
            + broad_terms
        )

    # --------------------------------------------------------
    # Compact query.
    #
    # Keep the original concepts and only a small number
    # of related terms.
    # --------------------------------------------------------

    compact_terms = list(
        query_terms
    )

    for term in query_terms:

        if term not in QUERY_EXPANSIONS:
            continue

        for expanded_term in QUERY_EXPANSIONS[term][:3]:

            if expanded_term not in compact_terms:

                compact_terms.append(
                    expanded_term
                )

    if compact_terms:

        compact_query = " ".join(
            compact_terms
        )

        queries.append(
            compact_query
            + " summary overview"
        )

    # --------------------------------------------------------
    # Remove duplicates while preserving order.
    # --------------------------------------------------------

    unique_queries = []

    seen = set()

    for retrieval_query in queries:

        normalized = normalize_query(
            retrieval_query
        )

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(
            normalized
        )

        unique_queries.append(
            retrieval_query.strip()
        )

    return unique_queries


# ============================================================
# BUILD TARGETED EXPANSION
# ============================================================

def build_targeted_expansion(
    query: str,
) -> str:
    """
    Build a generic semantic expansion while preserving
    the important concepts from the user's query.

    The expansion is based only on generic query concepts.

    No document-specific information is used.
    """

    query_terms = extract_query_terms(
        query
    )

    if not query_terms:
        return ""

    expansion_terms = []

    for term in query_terms:

        if term in QUERY_EXPANSIONS:

            for expanded_term in QUERY_EXPANSIONS[term]:

                if expanded_term not in expansion_terms:

                    expansion_terms.append(
                        expanded_term
                    )

        # ----------------------------------------------------
        # Generic singular/plural normalization.
        # ----------------------------------------------------

        if term.endswith("ies"):

            singular = (
                term[:-3]
                + "y"
            )

            if singular not in expansion_terms:

                expansion_terms.append(
                    singular
                )

        elif term.endswith("s"):

            singular = term[:-1]

            if (
                len(singular) > 2
                and singular not in expansion_terms
            ):

                expansion_terms.append(
                    singular
                )

    return " ".join(
        expansion_terms
    )


# ============================================================
# BUILD SPECIFIC QUERIES
# ============================================================

def build_specific_queries(
    query: str,
) -> list[str]:
    """
    Generate generic retrieval queries.

    The original user query is always preserved.

    The expansion is an additional retrieval query.
    It never replaces the original query.
    """

    original_query = query.strip()

    if not original_query:
        return []

    queries = [
        original_query,
    ]

    expanded_query = build_targeted_expansion(
        original_query
    )

    if expanded_query:

        normalized_expanded = normalize_query(
            expanded_query
        )

        normalized_original = normalize_query(
            original_query
        )

        if (
            normalized_expanded
            != normalized_original
        ):

            queries.append(
                expanded_query
            )

    return queries


# ============================================================
# QUERY TRANSFORMATION
# ============================================================

def transform_query(
    query: str,
) -> dict:
    """
    Transform a user query into one or more
    generic retrieval queries.

    Priority:

        1. broad_document
        2. list
        3. specific

    The original query is always preserved.
    """

    if not query or not query.strip():

        raise ValueError(
            "Query cannot be empty."
        )

    # --------------------------------------------------------
    # Preserve the user's original query.
    # --------------------------------------------------------

    original_query = query.strip()

    # ========================================================
    # BROAD DOCUMENT
    # ========================================================

    if is_broad_document_query(
        original_query
    ):

        retrieval_queries = (
            build_broad_queries(
                original_query
            )
        )

        return {
            "original_query": original_query,
            "retrieval_queries": retrieval_queries,
            "query_type": "broad_document",
        }

    # ========================================================
    # LIST QUERY
    # ========================================================

    if is_list_query(
        original_query
    ):

        retrieval_queries = (
            build_specific_queries(
                original_query
            )
        )

        return {
            "original_query": original_query,
            "retrieval_queries": retrieval_queries,
            "query_type": "list",
        }

    # ========================================================
    # SPECIFIC QUERY
    # ========================================================

    retrieval_queries = (
        build_specific_queries(
            original_query
        )
    )

    return {
        "original_query": original_query,
        "retrieval_queries": retrieval_queries,
        "query_type": "specific",
    }