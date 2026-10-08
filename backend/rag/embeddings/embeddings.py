from fastembed import TextEmbedding


MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

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

    vectors = embedding_model.embed(texts)

    return [
        vector.tolist()
        for vector in vectors
    ]