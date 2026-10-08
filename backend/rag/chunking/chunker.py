import re
import unicodedata


def _normalize_text(text: str) -> str:
    """
    Normalize whitespace while preserving paragraph boundaries.
    """

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# Sentence terminators used across writing systems (Unicode punctuation,
# not words of any language):
#   . ! ?            Latin and many others
#   । ॥              danda / double danda (Devanagari, Odia, Bengali, ...)
#   ؟ ۔              Arabic question mark, Urdu full stop
#   。！？            CJK full-width
#   ።                Ethiopic full stop
SENTENCE_TERMINATORS = ".!?।॥؟۔。！？።"

_SENTENCE_BREAK = re.compile(
    r"(?<=[" + re.escape(SENTENCE_TERMINATORS) + r"])\s+"
)


def _starts_new_sentence(char: str) -> bool:
    """
    Can a sentence start with this character?

    Latin text keeps the original rule (ASCII capital letter or digit),
    so English sentence splitting is unchanged. Scripts without letter
    case (Devanagari, Odia, Arabic, CJK, ...) cannot signal a sentence
    start with a capital, so any caseless letter or decimal digit counts.
    """

    if ("A" <= char <= "Z") or ("0" <= char <= "9"):
        return True

    category = unicodedata.category(char)

    # Lo = letter without case; Nd = decimal digit in any script.
    return (
        not char.isascii()
        and category in ("Lo", "Nd")
    )


def _split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences while preserving sentence content.

    A boundary is whitespace after a sentence terminator from any script,
    followed by a character that can start a sentence.
    """

    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return []

    sentences = []
    start = 0

    for match in _SENTENCE_BREAK.finditer(text):

        next_index = match.end()

        if (
            next_index < len(text)
            and _starts_new_sentence(text[next_index])
        ):
            sentences.append(text[start:match.start()])
            start = next_index

    sentences.append(text[start:])

    return [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]


def _split_large_sentence(
    sentence: str,
    chunk_size: int
) -> list[str]:
    """
    Split an oversized sentence by words.
    """

    words = sentence.split()

    chunks = []
    current_chunk = ""

    for word in words:

        if not current_chunk:
            current_chunk = word
            continue

        candidate = f"{current_chunk} {word}"

        if len(candidate) <= chunk_size:
            current_chunk = candidate

        else:
            chunks.append(
                current_chunk.strip()
            )

            current_chunk = word

    if current_chunk:
        chunks.append(
            current_chunk.strip()
        )

    return chunks


def _build_sentence_chunks(
    sentences: list[str],
    chunk_size: int,
    small_chunk_ratio: float = 0.25
) -> list[str]:
    """
    Build chunks from sentences while respecting chunk_size.
    """

    chunks = []

    current_sentences = []
    current_length = 0

    minimum_size = int(
        chunk_size * small_chunk_ratio
    )

    for sentence in sentences:

        sentence_length = len(sentence)

        # --------------------------------------------------
        # Oversized sentence
        # --------------------------------------------------

        if sentence_length > chunk_size:

            if current_sentences:

                pending = " ".join(
                    current_sentences
                ).strip()

                current_sentences = []
                current_length = 0

                # A short pending sentence would become a tiny
                # standalone chunk (later merging cannot fix it
                # because the next chunk is already full). Keep it
                # with the oversized sentence and split them together
                # by words instead.
                if len(pending) < minimum_size:

                    sentence = f"{pending} {sentence}"

                else:

                    chunks.append(pending)

            chunks.extend(
                _split_large_sentence(
                    sentence,
                    chunk_size
                )
            )

            continue

        # --------------------------------------------------
        # Calculate additional length
        # --------------------------------------------------

        additional_length = (
            sentence_length
            if not current_sentences
            else sentence_length + 1
        )

        # --------------------------------------------------
        # Sentence fits
        # --------------------------------------------------

        if (
            current_length + additional_length
            <= chunk_size
        ):

            current_sentences.append(
                sentence
            )

            current_length += additional_length

        # --------------------------------------------------
        # Sentence does not fit
        # --------------------------------------------------

        else:

            if current_sentences:

                chunks.append(
                    " ".join(
                        current_sentences
                    ).strip()
                )

            current_sentences = [
                sentence
            ]

            current_length = sentence_length

    # --------------------------------------------------
    # Final chunk
    # --------------------------------------------------

    if current_sentences:

        chunks.append(
            " ".join(
                current_sentences
            ).strip()
        )

    return chunks


def _apply_sentence_overlap(
    chunks: list[str],
    chunk_overlap: int,
    chunk_size: int
) -> list[str]:
    """
    Add semantic overlap using complete sentences.

    The final chunk size never exceeds chunk_size.
    """

    if (
        chunk_overlap <= 0
        or len(chunks) <= 1
    ):
        return chunks

    result = [chunks[0]]

    for index in range(1, len(chunks)):

        previous_chunk = result[-1]
        current_chunk = chunks[index]

        previous_sentences = _split_into_sentences(
            previous_chunk
        )

        if not previous_sentences:
            result.append(current_chunk)
            continue

        overlap_sentences = []
        overlap_length = 0

        for sentence in reversed(
            previous_sentences
        ):

            sentence_length = len(sentence)

            additional_length = (
                sentence_length
                if not overlap_sentences
                else sentence_length + 1
            )

            if (
                overlap_length + additional_length
                <= chunk_overlap
            ):

                overlap_sentences.insert(
                    0,
                    sentence
                )

                overlap_length += additional_length

            else:
                break

        if not overlap_sentences:
            result.append(current_chunk)
            continue

        overlap_text = " ".join(
            overlap_sentences
        ).strip()

        combined_length = (
            len(overlap_text)
            + 1
            + len(current_chunk)
        )

        if combined_length <= chunk_size:

            combined = (
                f"{overlap_text} "
                f"{current_chunk}"
            ).strip()

            result.append(combined)

        else:
            result.append(current_chunk)

    return result


def _merge_small_fragments(
    chunks: list[str],
    chunk_size: int,
    small_chunk_ratio: float = 0.25
) -> list[str]:
    """
    Merge very small chunks with neighboring chunks
    whenever the merged result fits within chunk_size.

    A chunk is considered small when:

        len(chunk) < chunk_size * small_chunk_ratio
    """

    if len(chunks) <= 1:
        return chunks

    minimum_size = int(
        chunk_size * small_chunk_ratio
    )

    chunks = list(chunks)

    changed = True

    while changed:

        changed = False

        for index in range(len(chunks)):

            current_chunk = chunks[index]

            if len(current_chunk) >= minimum_size:
                continue

            # --------------------------------------------------
            # Try previous chunk
            # --------------------------------------------------

            if index > 0:

                previous_chunk = chunks[index - 1]

                merged_length = (
                    len(previous_chunk)
                    + 1
                    + len(current_chunk)
                )

                if merged_length <= chunk_size:

                    chunks[index - 1] = (
                        f"{previous_chunk} "
                        f"{current_chunk}"
                    ).strip()

                    chunks.pop(index)

                    changed = True
                    break

            # --------------------------------------------------
            # Try next chunk
            # --------------------------------------------------

            if index < len(chunks) - 1:

                next_chunk = chunks[index + 1]

                merged_length = (
                    len(current_chunk)
                    + 1
                    + len(next_chunk)
                )

                if merged_length <= chunk_size:

                    chunks[index] = (
                        f"{current_chunk} "
                        f"{next_chunk}"
                    ).strip()

                    chunks.pop(index + 1)

                    changed = True
                    break

    return chunks


def _rebalance_small_fragments(
    chunks: list[str],
    chunk_size: int,
    small_chunk_ratio: float = 0.25
) -> list[str]:
    """
    Rebalance very small chunks.

    Strategy:

    1. Try moving complete sentences from the previous chunk.
    2. If sentence detection cannot find a boundary,
       use a safe word-boundary fallback.
    3. Never split a word.
    4. Never exceed chunk_size.
    5. Preserve all original content.
    """

    if len(chunks) <= 1:
        return chunks

    minimum_size = int(
        chunk_size * small_chunk_ratio
    )

    chunks = list(chunks)

    index = 1

    while index < len(chunks):

        current_chunk = chunks[index]

        # --------------------------------------------------
        # Already large enough
        # --------------------------------------------------

        if len(current_chunk) >= minimum_size:
            index += 1
            continue

        previous_chunk = chunks[index - 1]

        # ==================================================
        # METHOD 1
        # Sentence-based rebalancing
        # ==================================================

        previous_sentences = _split_into_sentences(
            previous_chunk
        )

        if len(previous_sentences) >= 2:

            remaining_sentences = list(
                previous_sentences
            )

            moved_sentences = []

            while len(remaining_sentences) > 1:

                sentence = remaining_sentences[-1]

                candidate_previous = " ".join(
                    remaining_sentences[:-1]
                ).strip()

                if not candidate_previous:
                    break

                candidate_current = (
                    " ".join(
                        moved_sentences
                        + [sentence]
                        + [current_chunk]
                    )
                ).strip()

                if len(candidate_current) > chunk_size:
                    break

                moved_sentences.insert(
                    0,
                    sentence
                )

                remaining_sentences.pop()

                new_current = (
                    " ".join(
                        moved_sentences
                    )
                    + " "
                    + current_chunk
                ).strip()

                if len(new_current) >= minimum_size:
                    break

            if moved_sentences:

                new_previous = " ".join(
                    remaining_sentences
                ).strip()

                new_current = (
                    " ".join(
                        moved_sentences
                    )
                    + " "
                    + current_chunk
                ).strip()

                if (
                    new_previous
                    and len(new_previous) <= chunk_size
                    and len(new_current) <= chunk_size
                ):

                    chunks[index - 1] = new_previous
                    chunks[index] = new_current

                    index += 1
                    continue

        # ==================================================
        # METHOD 2
        # Word-boundary fallback
        # ==================================================

        previous_words = previous_chunk.split()

        current_words = current_chunk.split()

        if len(previous_words) <= 1:
            index += 1
            continue

        moved_words = []

        # Keep at least one word in previous chunk.
        while len(previous_words) > 1:

            candidate_word = previous_words[-1]

            candidate_current = (
                " ".join(
                    [candidate_word]
                    + moved_words
                    + current_words
                )
            ).strip()

            if len(candidate_current) > chunk_size:
                break

            moved_words.insert(
                0,
                candidate_word
            )

            previous_words.pop()

            new_current = (
                " ".join(
                    moved_words
                    + current_words
                )
            ).strip()

            # Stop once the small chunk reaches
            # the minimum desired size.
            if len(new_current) >= minimum_size:
                break

        if moved_words:

            new_previous = " ".join(
                previous_words
            ).strip()

            new_current = " ".join(
                moved_words
                + current_words
            ).strip()

            if (
                new_previous
                and new_current
                and len(new_previous) <= chunk_size
                and len(new_current) <= chunk_size
            ):

                chunks[index - 1] = new_previous
                chunks[index] = new_current

        index += 1

    return chunks


def _validate_chunks(
    chunks: list[str],
    chunk_size: int
) -> None:
    """
    Final safety validation.

    Ensures no chunk exceeds chunk_size.
    """

    for index, chunk in enumerate(chunks):

        if len(chunk) > chunk_size:

            raise ValueError(
                f"Chunk {index} exceeds "
                f"chunk_size: "
                f"{len(chunk)} > {chunk_size}"
            )


def split_text(
    text: str,
    chunk_size: int = 700,
    chunk_overlap: int = 120
) -> list[str]:
    """
    Production-oriented document chunker.

    Pipeline:

        Raw text
            ↓
        Normalize
            ↓
        Paragraph detection
            ↓
        Paragraph grouping
            ↓
        Sentence-aware splitting
            ↓
        Word-level fallback
            ↓
        Semantic overlap
            ↓
        Small-fragment merging
            ↓
        Small-fragment rebalancing
            ↓
        Final cleanup
            ↓
        Validation
    """

    # ==================================================
    # VALIDATION
    # ==================================================

    if not text or not text.strip():
        return []

    if chunk_size <= 0:
        raise ValueError(
            "chunk_size must be greater than 0."
        )

    if chunk_overlap < 0:
        raise ValueError(
            "chunk_overlap cannot be negative."
        )

    if chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap must be smaller than chunk_size."
        )

    # ==================================================
    # NORMALIZE
    # ==================================================

    text = _normalize_text(text)

    if not text:
        return []

    # ==================================================
    # PARAGRAPHS
    # ==================================================

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(
            r"\n\s*\n",
            text
        )
        if paragraph.strip()
    ]

    if not paragraphs:
        return []

    # ==================================================
    # BUILD INITIAL CHUNKS
    # ==================================================

    chunks = []

    current_paragraphs = []
    current_length = 0

    for paragraph in paragraphs:

        paragraph = re.sub(
            r"\s+",
            " ",
            paragraph
        ).strip()

        if not paragraph:
            continue

        paragraph_length = len(paragraph)

        # --------------------------------------------------
        # Normal paragraph
        # --------------------------------------------------

        if paragraph_length <= chunk_size:

            additional_length = (
                paragraph_length
                if not current_paragraphs
                else paragraph_length + 2
            )

            if (
                current_length + additional_length
                <= chunk_size
            ):

                current_paragraphs.append(
                    paragraph
                )

                current_length += additional_length

            else:

                if current_paragraphs:

                    chunks.append(
                        "\n\n".join(
                            current_paragraphs
                        ).strip()
                    )

                current_paragraphs = [
                    paragraph
                ]

                current_length = paragraph_length

        # --------------------------------------------------
        # Oversized paragraph
        # --------------------------------------------------

        else:

            if current_paragraphs:

                chunks.append(
                    "\n\n".join(
                        current_paragraphs
                    ).strip()
                )

                current_paragraphs = []
                current_length = 0

            sentences = _split_into_sentences(
                paragraph
            )

            if sentences:

                chunks.extend(
                    _build_sentence_chunks(
                        sentences,
                        chunk_size
                    )
                )

            else:

                chunks.extend(
                    _split_large_sentence(
                        paragraph,
                        chunk_size
                    )
                )

    # ==================================================
    # FINAL PARAGRAPH GROUP
    # ==================================================

    if current_paragraphs:

        chunks.append(
            "\n\n".join(
                current_paragraphs
            ).strip()
        )

    # ==================================================
    # REMOVE EMPTY CHUNKS
    # ==================================================

    chunks = [
        chunk.strip()
        for chunk in chunks
        if chunk and chunk.strip()
    ]

    if not chunks:
        return []

    # ==================================================
    # SEMANTIC OVERLAP
    # ==================================================

    chunks = _apply_sentence_overlap(
        chunks=chunks,
        chunk_overlap=chunk_overlap,
        chunk_size=chunk_size
    )

    # ==================================================
    # SMALL-FRAGMENT MERGING
    # ==================================================

    chunks = _merge_small_fragments(
        chunks=chunks,
        chunk_size=chunk_size,
        small_chunk_ratio=0.25
    )

    # ==================================================
    # SMALL-FRAGMENT REBALANCING
    # ==================================================

    chunks = _rebalance_small_fragments(
        chunks=chunks,
        chunk_size=chunk_size,
        small_chunk_ratio=0.25
    )

    # ==================================================
    # FINAL CLEANUP
    # ==================================================

    final_chunks = []

    for chunk in chunks:

        chunk = re.sub(
            r"[ \t]+",
            " ",
            chunk
        ).strip()

        if chunk:
            final_chunks.append(
                chunk
            )

    # ==================================================
    # FINAL SAFETY VALIDATION
    # ==================================================

    _validate_chunks(
        final_chunks,
        chunk_size
    )

    return final_chunks