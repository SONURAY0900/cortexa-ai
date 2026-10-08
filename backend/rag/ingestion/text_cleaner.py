import re


def _repair_hyphenation(text: str) -> str:
    """
    Repair words split across lines by PDF extraction.

    Example:

        informa-
        tion

    becomes:

        information
    """

    text = re.sub(
        r"(\w)-\s*\n\s*(\w)",
        r"\1\2",
        text
    )

    return text


def _normalize_line_breaks(text: str) -> str:
    """
    Normalize different line-ending formats.
    """

    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    return text


def _normalize_spaces(text: str) -> str:
    """
    Normalize spaces and tabs while preserving newlines.
    """

    # Replace tabs with spaces
    text = text.replace(
        "\t",
        " "
    )

    # Collapse repeated spaces
    text = re.sub(
        r"[ ]{2,}",
        " ",
        text
    )

    # Remove spaces at beginning/end of lines
    text = re.sub(
        r"[ ]+\n",
        "\n",
        text
    )

    text = re.sub(
        r"\n[ ]+",
        "\n",
        text
    )

    return text


def _normalize_paragraphs(text: str) -> str:
    """
    Normalize excessive blank lines without destroying
    paragraph boundaries.
    """

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def clean_text(text: str) -> str:
    """
    Production-oriented PDF text cleaning pipeline.

    Pipeline:

        Raw PDF text
              ↓
        Normalize line endings
              ↓
        Repair line hyphenation
              ↓
        Normalize spaces
              ↓
        Normalize paragraphs
              ↓
        Final cleaned text

    Parameters
    ----------
    text:
        Raw extracted PDF text.

    Returns
    -------
    str
        Cleaned text.
    """

    if not text:
        return ""

    # --------------------------------------------------
    # 1. Normalize line endings
    # --------------------------------------------------

    text = _normalize_line_breaks(
        text
    )

    # --------------------------------------------------
    # 2. Repair words split across lines
    # --------------------------------------------------

    text = _repair_hyphenation(
        text
    )

    # (A former step replaced a fixed list of split words taken
    # from one evaluation document. It was document-specific and
    # never fired on other PDFs, so it was removed in Phase 14.)

    # --------------------------------------------------
    # 3. Normalize spaces
    # --------------------------------------------------

    text = _normalize_spaces(
        text
    )

    # --------------------------------------------------
    # 4. Normalize paragraphs
    # --------------------------------------------------

    text = _normalize_paragraphs(
        text
    )

    return text