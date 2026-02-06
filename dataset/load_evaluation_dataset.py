"""
Golden dataset loader for TruLens and RAGAS evaluation.

Usage:
    from govtech_hackathon.golden.load_evaluation_dataset import load_dataset, to_ragas_format, to_trulens_format
    
    # Load full dataset
    dataset = load_dataset()
    
    # Load specific subset
    bzk_questions = load_dataset(dataset_filter="bzk_pilot")
    omgevingswet_questions = load_dataset(dataset_filter="omgevingswet")
    
    # Convert to RAGAS format
    ragas_dataset = to_ragas_format(dataset)
    
    # Convert to TruLens format  
    trulens_records = to_trulens_format(dataset)
"""

import json
from pathlib import Path
from typing import Literal

DATASET_PATH = Path(__file__).parent / "evaluation_dataset.jsonl"


def load_dataset(
    dataset_filter: Literal["bzk_pilot", "omgevingswet"] | None = None,
    path: Path | str | None = None,
) -> list[dict]:
    """Load evaluation dataset from JSONL file."""
    file_path = Path(path) if path else DATASET_PATH
    
    questions = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                if dataset_filter is None or record["dataset"] == dataset_filter:
                    questions.append(record)
    return questions


def to_ragas_format(dataset: list[dict]) -> dict:
    """
    Convert to RAGAS Dataset format.
    
    Returns dict with:
    - questions: list[str]
    - ground_truths: list[list[str]]  (RAGAS expects list of ground truths per question)
    - question_ids: list[str]  (for tracking)
    """
    return {
        "questions": [q["question"] for q in dataset],
        "ground_truths": [[q["ground_truth"]] for q in dataset],
        "question_ids": [q["question_id"] for q in dataset],
    }


def to_trulens_format(dataset: list[dict]) -> list[dict]:
    """
    Convert to TruLens record format for golden dataset evaluation.
    
    Returns list of records with:
    - input: the question
    - expected_output: the ground truth answer
    - metadata: additional info (question_id, dataset, source_url)
    """
    return [
        {
            "input": q["question"],
            "expected_output": q["ground_truth"],
            "metadata": {
                "question_id": q["question_id"],
                "dataset": q["dataset"],
                "source_url": q["source_url"],
            },
        }
        for q in dataset
    ]


def to_langchain_format(dataset: list[dict]) -> list[dict]:
    """
    Convert to LangChain evaluation format.
    
    Returns list of examples with:
    - query: the question
    - answer: the ground truth answer
    """
    return [
        {
            "query": q["question"],
            "answer": q["ground_truth"],
        }
        for q in dataset
    ]


if __name__ == "__main__":
    # Quick test
    dataset = load_dataset()
    print(f"Loaded {len(dataset)} questions")
    print(f"  - BZK: {len([q for q in dataset if q['dataset'] == 'bzk_pilot'])}")
    print(f"  - Omgevingswet: {len([q for q in dataset if q['dataset'] == 'omgevingswet'])}")
    
    print("\nFirst question:")
    print(f"  Q: {dataset[0]['question']}")
    print(f"  A: {dataset[0]['ground_truth'][:100]}...")
