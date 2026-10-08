from pathlib import Path
import json


DATASET_DIR = Path(__file__).parent / "datasets"


def load_dataset(dataset_file: str) -> list[dict]:
    """
    Load an evaluation dataset.

    The dataset contains evaluation questions and
    their ground-truth relevant chunk indexes.

    This function is independent of any particular PDF.
    """

    dataset_path = DATASET_DIR / dataset_file

    if not dataset_path.exists():
        raise FileNotFoundError(
            f"Evaluation dataset not found: {dataset_path}"
        )

    with open(dataset_path, "r", encoding="utf-8") as file:
        dataset = json.load(file)

    if not isinstance(dataset, list):
        raise ValueError(
            "Evaluation dataset must be a JSON list."
        )

    for index, item in enumerate(dataset):

        if "query" not in item:
            raise ValueError(
                f"Dataset item {index} is missing 'query'."
            )

        if "relevant_chunks" not in item:
            raise ValueError(
                f"Dataset item {index} is missing "
                "'relevant_chunks'."
            )

        if not isinstance(item["query"], str):
            raise ValueError(
                f"Dataset item {index}: "
                "'query' must be a string."
            )

        if not isinstance(
            item["relevant_chunks"],
            list,
        ):
            raise ValueError(
                f"Dataset item {index}: "
                "'relevant_chunks' must be a list."
            )

    return dataset