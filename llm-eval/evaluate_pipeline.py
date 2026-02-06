#!/usr/bin/env python3
"""
Theon RAG Evaluation Pipeline

Evaluates Theon responses against ground truth using GreenPT embeddings
and cosine similarity.

Usage:
    python evaluate_pipeline.py --with-collection [--single]
    python evaluate_pipeline.py --no-collection [--single]
"""

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import requests

# Configuration
THEON_API_URL = os.environ.get("THEON_API_URL", "http://localhost:8080")
THEON_API_TOKEN = os.environ.get("THEON_API_TOKEN", "")
GREENPT_API_KEY = os.environ.get("GREENPT_API_KEY", "")
GREENPT_API_URL = os.environ.get("GREENPT_API_URL", "https://api.greenpt.ai/v1")
DATASET_FILE = os.environ.get("DATASET_FILE", "./golden/evaluation_dataset.json")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./evaluation_results")


@dataclass
class EvaluationResult:
    question_id: str
    dataset: str
    question: str
    ground_truth: str
    response: str
    similarity_score: float
    response_time: float
    collection_used: Optional[str]
    source_url: str


def get_embedding(text: str) -> list[float]:
    """Get embedding from GreenPT API."""
    response = requests.post(
        f"{GREENPT_API_URL}/embeddings",
        headers={
            "Authorization": f"Bearer {GREENPT_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "green-embedding",
            "input": text,
            "encoding_format": "float",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    a = np.array(vec1)
    b = np.array(vec2)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def call_theon_api(question: str, collections: list[str]) -> tuple[str, float]:
    """Call Theon API and return response text and duration."""
    import time

    payload = {
        "messages": [{"role": "user", "content": question}],
        "chat_id": f"eval_{uuid.uuid4()}",
        "collections": collections,
        "temperature": 0.0,
    }

    start = time.time()
    response = requests.post(
        f"{THEON_API_URL}/api/generation/chat",
        headers={
            "Authorization": f"Bearer {THEON_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
        stream=True,
    )
    
    # Collect streamed response
    full_response = ""
    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
        if chunk:
            full_response += chunk
    
    duration = time.time() - start
    return full_response, duration


def extract_answer_from_stream(stream_response: str) -> str:
    """Extract the actual answer text from Theon stream response.
    
    Theon streams JSON objects that may be concatenated without newlines:
    {"message": {"content": "..."}}{"message": {"content": "..."}}...
    """
    import re
    
    content_parts = []
    
    # Find all JSON objects matching {"message": {"content": "..."}}
    # This handles both newline-separated and concatenated JSON
    pattern = r'\{"message":\s*\{"content":\s*"((?:[^"\\]|\\.)*)"\}\}'
    matches = re.findall(pattern, stream_response)
    
    for match in matches:
        # Unescape JSON string escapes
        content = match.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        content_parts.append(content)
    
    if content_parts:
        return "".join(content_parts)
    
    # Fallback: try line-by-line parsing for other formats
    for line in stream_response.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if "message" in data and isinstance(data["message"], dict):
                content = data["message"].get("content", "")
                if content:
                    content_parts.append(content)
            elif "choices" in data:
                delta = data["choices"][0].get("delta", {})
                if "content" in delta:
                    content_parts.append(delta["content"])
        except json.JSONDecodeError:
            continue
    
    return "".join(content_parts)


def load_dataset(path: str) -> list[dict]:
    """Load evaluation dataset from JSON file."""
    with open(path) as f:
        data = json.load(f)
    return data["questions"]


def evaluate_question(question_data: dict, use_collection: bool) -> EvaluationResult:
    """Evaluate a single question."""
    question = question_data["question"]
    ground_truth = question_data["ground_truth"]
    dataset = question_data["dataset"]
    
    collections = [dataset] if use_collection else []
    collection_display = dataset if use_collection else None
    
    print(f"  Calling Theon API...")
    raw_response, response_time = call_theon_api(question, collections)
    response = extract_answer_from_stream(raw_response)
    
    if not response.strip():
        response = raw_response
    
    print(f"  Getting embeddings...")
    try:
        response_embedding = get_embedding(response[:8000])
        ground_truth_embedding = get_embedding(ground_truth[:8000])
        similarity = cosine_similarity(response_embedding, ground_truth_embedding)
    except Exception as e:
        print(f"  Warning: Embedding failed: {e}")
        similarity = 0.0
    
    return EvaluationResult(
        question_id=question_data["question_id"],
        dataset=dataset,
        question=question,
        ground_truth=ground_truth,
        response=response,
        similarity_score=similarity,
        response_time=response_time,
        collection_used=collection_display,
        source_url=question_data["source_url"],
    )


def generate_markdown_report(
    results: list[EvaluationResult],
    mode: str,
    output_path: Path,
) -> None:
    """Generate markdown report with results."""
    
    bzk_results = [r for r in results if r.dataset == "bzk_pilot"]
    omgevingswet_results = [r for r in results if r.dataset == "omgevingswet"]
    
    def calc_stats(res: list[EvaluationResult]) -> tuple[float, float, float]:
        if not res:
            return 0.0, 0.0, 0.0
        scores = [r.similarity_score for r in res]
        return np.mean(scores), np.min(scores), np.max(scores)
    
    all_avg, all_min, all_max = calc_stats(results)
    bzk_avg, bzk_min, bzk_max = calc_stats(bzk_results)
    omg_avg, omg_min, omg_max = calc_stats(omgevingswet_results)
    
    with open(output_path, "w") as f:
        f.write(f"# Theon RAG Evaluation Report\n\n")
        f.write(f"**Mode:** {mode}\n")
        f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Total Questions:** {len(results)}\n\n")
        
        f.write("## Summary\n\n")
        f.write("| Dataset | Questions | Avg Similarity | Min | Max |\n")
        f.write("|---------|-----------|----------------|-----|-----|\n")
        f.write(f"| **All** | {len(results)} | {all_avg:.4f} | {all_min:.4f} | {all_max:.4f} |\n")
        f.write(f"| BZK | {len(bzk_results)} | {bzk_avg:.4f} | {bzk_min:.4f} | {bzk_max:.4f} |\n")
        f.write(f"| Omgevingswet | {len(omgevingswet_results)} | {omg_avg:.4f} | {omg_min:.4f} | {omg_max:.4f} |\n")
        f.write("\n---\n\n")
        
        f.write("## Detailed Results\n\n")
        
        for i, result in enumerate(results, 1):
            f.write(f"### Question {i}: {result.question}\n\n")
            f.write(f"**Dataset:** {result.dataset}\n")
            f.write(f"**Collection used:** {result.collection_used or 'None'}\n")
            f.write(f"**Similarity Score:** {result.similarity_score:.4f}\n")
            f.write(f"**Response Time:** {result.response_time:.2f}s\n\n")
            
            f.write("#### Theon Response\n\n")
            f.write(f"{result.response}\n\n")
            
            f.write("#### Ground Truth\n\n")
            f.write(f"{result.ground_truth}\n\n")
            
            f.write(f"**Source:** {result.source_url}\n\n")
            f.write("---\n\n")


def generate_json_report(
    results: list[EvaluationResult],
    mode: str,
    output_path: Path,
) -> None:
    """Generate JSON report with results."""
    
    bzk_results = [r for r in results if r.dataset == "bzk_pilot"]
    omgevingswet_results = [r for r in results if r.dataset == "omgevingswet"]
    
    def calc_stats(res: list[EvaluationResult]) -> dict:
        if not res:
            return {"avg": 0.0, "min": 0.0, "max": 0.0, "count": 0}
        scores = [r.similarity_score for r in res]
        return {
            "avg": float(np.mean(scores)),
            "min": float(np.min(scores)),
            "max": float(np.max(scores)),
            "count": len(res),
        }
    
    report = {
        "metadata": {
            "mode": mode,
            "timestamp": datetime.now().isoformat(),
            "total_questions": len(results),
        },
        "summary": {
            "all": calc_stats(results),
            "bzk_pilot": calc_stats(bzk_results),
            "omgevingswet": calc_stats(omgevingswet_results),
        },
        "results": [
            {
                "question_id": r.question_id,
                "dataset": r.dataset,
                "question": r.question,
                "ground_truth": r.ground_truth,
                "response": r.response,
                "similarity_score": r.similarity_score,
                "response_time": r.response_time,
                "collection_used": r.collection_used,
                "source_url": r.source_url,
            }
            for r in results
        ],
    }
    
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Theon RAG Evaluation Pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--with-collection", action="store_true", help="Use appropriate collection per question")
    group.add_argument("--no-collection", action="store_true", help="Don't use any collection")
    parser.add_argument("--single", action="store_true", help="Only evaluate first question")
    args = parser.parse_args()
    
    if not THEON_API_TOKEN:
        print("Error: THEON_API_TOKEN environment variable not set")
        sys.exit(1)
    
    mode = "with_collection" if args.with_collection else "no_collection"
    use_collection = args.with_collection
    
    print(f"Loading dataset from {DATASET_FILE}...")
    questions = load_dataset(DATASET_FILE)
    
    if args.single:
        questions = questions[:1]
        print("Running in single-question mode")
    
    print(f"Evaluating {len(questions)} questions ({mode})...\n")
    
    results = []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['question'][:60]}...")
        result = evaluate_question(q, use_collection)
        results.append(result)
        print(f"  Score: {result.similarity_score:.4f}\n")
    
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = output_dir / f"{mode}_{timestamp}.md"
    json_path = output_dir / f"{mode}_{timestamp}.json"
    
    print("Generating reports...")
    generate_markdown_report(results, mode, md_path)
    generate_json_report(results, mode, json_path)
    
    all_scores = [r.similarity_score for r in results]
    bzk_scores = [r.similarity_score for r in results if r.dataset == "bzk_pilot"]
    omg_scores = [r.similarity_score for r in results if r.dataset == "omgevingswet"]
    
    print("\n" + "=" * 50)
    print("EVALUATION COMPLETE")
    print("=" * 50)
    print(f"\nOverall Average: {np.mean(all_scores):.4f}")
    if bzk_scores:
        print(f"BZK Average:     {np.mean(bzk_scores):.4f}")
    if omg_scores:
        print(f"Omgevingswet:    {np.mean(omg_scores):.4f}")
    print(f"\nReports saved to:")
    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")


if __name__ == "__main__":
    main()
