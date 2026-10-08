"""
Language-agnostic word tokenization for lexical retrieval signals.

A token is a maximal run of Unicode letters (L*), combining marks (M*)
and digits (N*). Combining marks must be included: in many scripts
(Devanagari, Odia, Bengali, Tamil, Thai, ...) vowel signs and viramas are
marks, and dropping them would cut words into meaningless pieces.

For ASCII text this produces exactly the same tokens as the previous
pattern [a-zA-Z0-9]+ (after lowercasing). Text is NFKC-normalized first
so that compatibility forms (ligatures such as "ﬁ", full-width letters,
decomposed accents) compare equal to their plain forms.
"""

import unicodedata


def _is_word_char(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] in ("L", "M", "N")


def unicode_tokens(text: str, extra_chars: str = "") -> list[str]:
    """
    Lowercased word tokens for any script.

    `extra_chars` lists additional characters that count as part of a
    token (e.g. "_+#.-" keeps "c++", "c#" and "node.js" whole).

    Examples:

        "FastAPI-based REST API"  -> ["fastapi", "based", "rest", "api"]
        "LLaMA 3.1"               -> ["llama", "3", "1"]
        "किरन कहाँ जाती है?"        -> ["किरन", "कहाँ", "जाती", "है"]
    """

    if not text:
        return []

    text = unicodedata.normalize("NFKC", text).lower()

    tokens = []
    current = []

    for char in text:

        if _is_word_char(char) or char in extra_chars:
            current.append(char)
            continue

        if current:
            tokens.append("".join(current))
            current = []

    if current:
        tokens.append("".join(current))

    return tokens
