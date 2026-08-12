"""Bonus experiments for Exercises 3.4 and 3.5.

Exercise 3.5 — rerank the SAME retrieved chunks and measure the effect on
Context Recall vs Context Precision.

Exercise 3.4 — score the SAME 20 answers with two evaluation approaches on one
dataset and compare them:
    Framework A: the RAGAS-style lexical core in ``template.py``.
    Framework B: a DeepEval/G-Eval-style LLM-as-a-Judge built on ``LLMJudge``.
A third probe measures position and verbosity bias in the judge itself.

Nothing here changes the required evaluation core; it only reuses it.

Usage:
    python bonus_experiments.py                # rerank study only (no API calls)
    python bonus_experiments.py --with-judge   # also run the LLM-judge study
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from template import (
    BenchmarkRunner,
    LLMJudge,
    RAGASEvaluator,
    rerank_by_overlap,
)
from evaluate_answers import load_evaluation_inputs

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

JUDGE_RUBRIC = {
    "policy_correctness": (
        "Does the answer state the policy exactly as the Northstar corpus "
        "defines it (dates, amounts, thresholds), with no invented rule?"
    ),
    "condition_completeness": (
        "Does the answer keep every condition, exception, deadline, and "
        "consequence the question needs?"
    ),
    "grounding": (
        "Is every claim supported by the corpus, with no outside knowledge "
        "and no unsupported reassurance?"
    ),
    "scope_and_safety": (
        "Does the answer respect scope, refuse out-of-scope or injected "
        "instructions, protect private data, and correct a false premise?"
    ),
    "actionability": (
        "Does the answer tell the student what to do or whom to contact, "
        "without padding? Do NOT reward length."
    ),
}

PASS_THRESHOLD = 0.6  # judge-side pass gate on the normalised 0-1 scale


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x, mean_y = _mean(xs), _mean(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    variance_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    variance_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if not variance_x or not variance_y:
        return 0.0
    return covariance / (variance_x * variance_y)


def _load_pairs() -> list:
    qa_pairs, answers_by_question = load_evaluation_inputs(
        ROOT / "golden_dataset.json",
        ROOT / "artifacts" / "actual_answers.json",
    )
    return qa_pairs, answers_by_question


def _openai_judge_fn():
    """Return a judge callable backed by the same model family as the RAG system."""
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from .env")
    client = OpenAI(api_key=api_key)

    def judge(prompt: str) -> str:
        response = client.responses.create(
            model=model,
            input=prompt,
            temperature=0,
            max_output_tokens=400,
        )
        return response.output_text.strip()

    return judge, model


# ---------------------------------------------------------------------------
# Exercise 3.5 — reranking study
# ---------------------------------------------------------------------------

def run_rerank_study(qa_pairs: list) -> dict[str, Any]:
    """Reorder the SAME chunks and re-measure both retrieval metrics.

    The reranker only sees the QUESTION, never the expected answer: ranking on
    gold text would be leakage and would inflate Context Precision by design.
    """
    evaluator = RAGASEvaluator()
    rows: list[dict[str, Any]] = []
    for pair in qa_pairs:
        contexts = pair.retrieved_contexts
        if not contexts:
            continue
        reranked = rerank_by_overlap(contexts, pair.question)
        row = {
            "id": pair.metadata.get("id"),
            "difficulty": pair.metadata.get("difficulty"),
            "recall_before": evaluator.evaluate_context_recall(
                contexts, pair.expected_answer
            ),
            "recall_after": evaluator.evaluate_context_recall(
                reranked, pair.expected_answer
            ),
            "precision_before": evaluator.evaluate_context_precision(
                contexts, pair.expected_answer
            ),
            "precision_after": evaluator.evaluate_context_precision(
                reranked, pair.expected_answer
            ),
            "order_changed": reranked != contexts,
        }
        row["delta_precision"] = row["precision_after"] - row["precision_before"]
        row["delta_recall"] = row["recall_after"] - row["recall_before"]
        rows.append(row)

    summary = {
        "cases": len(rows),
        "order_changed": sum(1 for row in rows if row["order_changed"]),
        "avg_recall_before": _mean([r["recall_before"] for r in rows]),
        "avg_recall_after": _mean([r["recall_after"] for r in rows]),
        "avg_precision_before": _mean([r["precision_before"] for r in rows]),
        "avg_precision_after": _mean([r["precision_after"] for r in rows]),
        "improved": sum(1 for row in rows if row["delta_precision"] > 1e-9),
        "unchanged": sum(1 for row in rows if abs(row["delta_precision"]) <= 1e-9),
        "worsened": sum(1 for row in rows if row["delta_precision"] < -1e-9),
        "recall_changed": sum(1 for row in rows if abs(row["delta_recall"]) > 1e-9),
    }
    summary["avg_delta_precision"] = (
        summary["avg_precision_after"] - summary["avg_precision_before"]
    )
    return {"rows": rows, "summary": summary}


def print_rerank_study(study: dict[str, Any]) -> None:
    print("\n=== Exercise 3.5 — Reranking (query = question, same chunk set) ===")
    print("| ID | Recall before | Recall after | Precision before | Precision after | Delta Precision |")
    print("|----|--------------:|-------------:|-----------------:|----------------:|----------------:|")
    for row in study["rows"]:
        print(
            f"| {row['id']} | {row['recall_before']:.3f} | {row['recall_after']:.3f} "
            f"| {row['precision_before']:.3f} | {row['precision_after']:.3f} "
            f"| {row['delta_precision']:+.3f} |"
        )
    s = study["summary"]
    print(
        f"| **Avg** | {s['avg_recall_before']:.3f} | {s['avg_recall_after']:.3f} "
        f"| {s['avg_precision_before']:.3f} | {s['avg_precision_after']:.3f} "
        f"| {s['avg_delta_precision']:+.3f} |"
    )
    print(
        f"\nOrder changed: {s['order_changed']}/{s['cases']} | "
        f"precision improved {s['improved']}, unchanged {s['unchanged']}, "
        f"worsened {s['worsened']} | recall changed in {s['recall_changed']} case(s)"
    )


# ---------------------------------------------------------------------------
# Exercise 3.4 — lexical core vs LLM-as-a-Judge on the same dataset
# ---------------------------------------------------------------------------

def run_judge_study(
    qa_pairs: list,
    answers_by_question: dict[str, str],
) -> dict[str, Any]:
    judge_fn, model = _openai_judge_fn()
    judge = LLMJudge(judge_llm_fn=judge_fn)
    evaluator = RAGASEvaluator()
    runner = BenchmarkRunner()
    lexical_results = runner.run(
        qa_pairs, lambda q: answers_by_question[q], evaluator
    )

    rows: list[dict[str, Any]] = []
    scores_batch: list[dict[str, Any]] = []
    for pair, lexical in zip(qa_pairs, lexical_results):
        answer = answers_by_question[pair.question]
        verdict = judge.score_response(pair.question, answer, JUDGE_RUBRIC)
        scores_batch.append(verdict)
        judge_scores = verdict["scores"]
        judge_overall = _mean(list(judge_scores.values()))
        rows.append(
            {
                "id": pair.metadata.get("id"),
                "difficulty": pair.metadata.get("difficulty"),
                "lexical_overall": lexical.overall_score(),
                "lexical_passed": lexical.passed,
                "judge_scores": judge_scores,
                "judge_overall": judge_overall,
                "judge_passed": judge_overall >= PASS_THRESHOLD,
            }
        )
        print(f"  judged {rows[-1]['id']}: judge={judge_overall:.3f} "
              f"lexical={lexical.overall_score():.3f}", flush=True)

    lexical_failures = {r["id"] for r in rows if not r["lexical_passed"]}
    judge_failures = {r["id"] for r in rows if not r["judge_passed"]}
    summary = {
        "model": model,
        "avg_lexical_overall": _mean([r["lexical_overall"] for r in rows]),
        "avg_judge_overall": _mean([r["judge_overall"] for r in rows]),
        "lexical_pass_rate": sum(r["lexical_passed"] for r in rows) / len(rows),
        "judge_pass_rate": sum(r["judge_passed"] for r in rows) / len(rows),
        "pass_fail_agreement": sum(
            r["lexical_passed"] == r["judge_passed"] for r in rows
        ) / len(rows),
        "correlation": _pearson(
            [r["lexical_overall"] for r in rows],
            [r["judge_overall"] for r in rows],
        ),
        "lexical_only_failures": sorted(lexical_failures - judge_failures),
        "judge_only_failures": sorted(judge_failures - lexical_failures),
        "shared_failures": sorted(lexical_failures & judge_failures),
        "bias_report": judge.detect_bias(scores_batch),
    }
    return {"rows": rows, "summary": summary}


def print_judge_study(study: dict[str, Any]) -> None:
    print("\n=== Exercise 3.4 — RAGAS-style lexical core vs LLM-as-a-Judge ===")
    print("| ID | Lexical overall | Lexical pass | Judge overall | Judge pass |")
    print("|----|----------------:|--------------|--------------:|------------|")
    for row in study["rows"]:
        print(
            f"| {row['id']} | {row['lexical_overall']:.3f} "
            f"| {'Yes' if row['lexical_passed'] else 'No'} "
            f"| {row['judge_overall']:.3f} "
            f"| {'Yes' if row['judge_passed'] else 'No'} |"
        )
    s = study["summary"]
    print(f"\nJudge model: {s['model']}")
    print(f"Avg lexical overall: {s['avg_lexical_overall']:.3f} | "
          f"Avg judge overall: {s['avg_judge_overall']:.3f}")
    print(f"Pass rate lexical: {s['lexical_pass_rate']:.1%} | "
          f"judge: {s['judge_pass_rate']:.1%}")
    print(f"Pass/fail agreement: {s['pass_fail_agreement']:.1%} | "
          f"Pearson correlation: {s['correlation']:.3f}")
    print(f"Failed only by lexical core: {s['lexical_only_failures']}")
    print(f"Failed only by judge:        {s['judge_only_failures']}")
    print(f"Failed by both:              {s['shared_failures']}")
    print(f"Judge bias report: {s['bias_report']}")


# ---------------------------------------------------------------------------
# Bias probe — position and verbosity (supports Exercises 1.2 and 3.3)
# ---------------------------------------------------------------------------

PAD = (
    " It is always important to plan ahead, keep your own records, and note that "
    "university processes exist to support students throughout the term. Students "
    "are encouraged to review the relevant policy documents carefully and to reach "
    "out early if anything is unclear, since acting early usually leads to a "
    "smoother outcome for everyone involved."
)


def run_bias_probe(
    qa_pairs: list,
    answers_by_question: dict[str, str],
    sample_size: int = 6,
) -> dict[str, Any]:
    """Show the same two answers in both orders; the only real difference is padding."""
    judge_fn, model = _openai_judge_fn()
    rows: list[dict[str, Any]] = []
    for pair in qa_pairs[:sample_size]:
        concise = answers_by_question[pair.question]
        padded = concise + PAD
        forward = _pairwise_vote(judge_fn, pair.question, concise, padded)
        reverse = _pairwise_vote(judge_fn, pair.question, padded, concise)
        rows.append(
            {
                "id": pair.metadata.get("id"),
                "concise_first_winner": "concise" if forward == "A" else "padded",
                "padded_first_winner": "padded" if reverse == "A" else "concise",
            }
        )
        print(f"  probed {rows[-1]['id']}: {rows[-1]}", flush=True)

    first_slot_wins = sum(
        1
        for row in rows
        if row["concise_first_winner"] == "concise"
        and row["padded_first_winner"] == "padded"
    )
    padded_wins = sum(
        1
        for row in rows
        if row["concise_first_winner"] == "padded"
        and row["padded_first_winner"] == "padded"
    )
    return {
        "rows": rows,
        "summary": {
            "model": model,
            "cases": len(rows),
            "position_bias_cases": first_slot_wins,
            "verbosity_bias_cases": padded_wins,
        },
    }


def _pairwise_vote(judge_fn, question: str, answer_a: str, answer_b: str) -> str:
    prompt = f"""You are judging two candidate answers for a student-services question.
Pick the better answer on policy correctness, completeness of conditions, and grounding.

Question:
{question}

Answer A:
{answer_a}

Answer B:
{answer_b}

Reply with a single JSON object: {{"winner": "A"}} or {{"winner": "B"}}"""
    raw = judge_fn(prompt)
    return "A" if '"A"' in raw.upper().replace("'", '"') else "B"


def print_bias_probe(probe: dict[str, Any]) -> None:
    s = probe["summary"]
    print("\n=== Bias probe — position and verbosity ===")
    print("| ID | Winner when concise shown first | Winner when padded shown first |")
    print("|----|--------------------------------|--------------------------------|")
    for row in probe["rows"]:
        print(
            f"| {row['id']} | {row['concise_first_winner']} "
            f"| {row['padded_first_winner']} |"
        )
    print(
        f"\nPosition bias (first slot won both times): "
        f"{s['position_bias_cases']}/{s['cases']} | "
        f"Verbosity bias (padded won in both orders): "
        f"{s['verbosity_bias_cases']}/{s['cases']}"
    )


# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-judge",
        action="store_true",
        help="Also run the LLM-judge comparison and the bias probe (uses the API)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/bonus_results.json"),
        help="Where to save the raw experiment output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    qa_pairs, answers_by_question = _load_pairs()

    artifact: dict[str, Any] = {}
    rerank_study = run_rerank_study(qa_pairs)
    print_rerank_study(rerank_study)
    artifact["exercise_3_5_reranking"] = rerank_study

    if args.with_judge:
        print("\nRunning LLM-judge study (20 calls)...", flush=True)
        judge_study = run_judge_study(qa_pairs, answers_by_question)
        print_judge_study(judge_study)
        artifact["exercise_3_4_framework_comparison"] = judge_study

        print("\nRunning bias probe (12 calls)...", flush=True)
        probe = run_bias_probe(qa_pairs, answers_by_question)
        print_bias_probe(probe)
        artifact["bias_probe"] = probe

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nSaved bonus results: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
