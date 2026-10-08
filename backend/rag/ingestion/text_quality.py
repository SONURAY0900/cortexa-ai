"""
Language-agnostic text quality checks for extracted PDF / OCR text.

The checks use only Unicode character properties, so they work for any
script and never depend on a particular language, document or word list.

Signals of broken extraction:

1. Words that start with a combining mark (Unicode category M*).
   No script writes a word that begins with a vowel sign / virama /
   diacritic. Broken font mappings (for example legacy Indic fonts) move
   these marks to the start of words.

2. Replacement characters (U+FFFD) and private-use characters, which
   appear when a font has no usable Unicode mapping.

3. Words that mix letters from more than one script, which is typical
   of garbled extraction or OCR noise (a normal word uses one script).
"""

import unicodedata


def _script(char: str) -> str | None:
    """
    Coarse script name of a letter or mark, taken from its Unicode name
    (e.g. "LATIN", "DEVANAGARI", "ORIYA", "ARABIC", "CJK").
    Returns None for characters that are not letters or marks.
    """

    category = unicodedata.category(char)

    if not (category.startswith("L") or category.startswith("M")):
        return None

    name = unicodedata.name(char, "")

    if not name:
        return None

    return name.split(" ")[0]


def quality_report(text: str) -> dict:
    """
    Measure how suspicious a piece of extracted text looks.

    Returns counts and a single `suspicious_ratio` in [0, 1]:
    the share of words showing at least one corruption signal, plus the
    share of characters that are replacement / private-use characters.
    """

    words = text.split() if text else []

    leading_mark_words = 0
    mixed_script_words = 0
    flagged_words = 0

    for word in words:

        first = word[0]
        leading_mark = unicodedata.category(first).startswith("M")

        scripts = {
            script
            for script in (_script(char) for char in word)
            if script
        }
        mixed_script = len(scripts) > 1

        leading_mark_words += leading_mark
        mixed_script_words += mixed_script
        flagged_words += leading_mark or mixed_script

    bad_chars = sum(
        1
        for char in (text or "")
        if char == "�" or unicodedata.category(char) == "Co"
    )

    word_count = len(words)
    char_count = len(text or "")

    word_ratio = flagged_words / word_count if word_count else 0.0
    char_ratio = bad_chars / char_count if char_count else 0.0

    return {
        "words": word_count,
        "leading_mark_words": leading_mark_words,
        "mixed_script_words": mixed_script_words,
        "bad_chars": bad_chars,
        "suspicious_ratio": word_ratio + char_ratio,
    }


def suspicious_ratio(text: str) -> float:
    return quality_report(text)["suspicious_ratio"]


def script_share(text: str, scripts: set[str]) -> float:
    """
    Share of letters in `text` that belong to the given scripts
    (names as returned by _script, e.g. {"LATIN", "CJK"}).
    Returns 1.0 when the text has no letters.
    """

    letters = [
        _script(char)
        for char in (text or "")
        if unicodedata.category(char).startswith("L")
    ]

    if not letters:
        return 1.0

    return sum(1 for script in letters if script in scripts) / len(letters)
