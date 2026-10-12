from fastembed import TextEmbedding


# English-focused 384-d model, chosen to fit 512 MB hosting (~200 MB
# loaded). Changing the model requires re-embedding stored chunks:
# python -m scripts.reindex_embeddings --apply
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Chunks embedded per batch. FastEmbed's default (256) briefly needs
# gigabytes of memory on large PDFs; small batches keep the peak low
# without changing the vectors.
BATCH_SIZE = 32

embedding_model = TextEmbedding(
    model_name=MODEL_NAME
)


def generate_embedding(text: str) -> list[float]:
    """
    Generate a 384-dimensional embedding for the given text.
    """

    if not text or not text.strip():
        raise ValueError("Text cannot be empty.")

    vector = list(
        embedding_model.embed([text])
    )[0]

    return vector.tolist()


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for multiple texts.
    """

    if not texts:
        return []

    vectors = embedding_model.embed(texts, batch_size=BATCH_SIZE)

    return [
        vector.tolist()
        for vector in vectors
    ]