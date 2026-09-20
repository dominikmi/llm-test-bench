#!/usr/bin/env python3
"""A/B test: score one model with tiel-verifier vs tiel-critic."""

import json
from typing import Any

from benchmark_galileo_reviews import (
    MODELS,
    QUALITY_CASES,
    BenchmarkRequestError,
    GalileoClient,
    JudgeClient,
    load_presets,
    score_response,
)
from benchmark_paths import CONFIG_DIR, JUDGE_RESULTS_DIR, ensure_parent_directories

# Model under test
MODEL = "coder-gemma4-26B-A4B-it:LATEST"
if MODEL not in MODELS:
    raise SystemExit(f"{MODEL} not found in {CONFIG_DIR / 'models.json'}: {MODELS}")

# Judges
VERIFIER = "Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-verifier"
CRITIC = "Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-critic"

OUTPUT_PATH = JUDGE_RESULTS_DIR / "judge_ab_results.json"

presets = load_presets(CONFIG_DIR / "presets.ini")
client = GalileoClient(presets)
judge_verifier = JudgeClient(VERIFIER)
judge_critic = JudgeClient(CRITIC)

records: list[dict[str, Any]] = []

for case in QUALITY_CASES:
    try:
        metrics = client.review(MODEL, case)
    except (BenchmarkRequestError, IndexError, KeyError, TypeError, ValueError) as error:
        print(f"{case.case_id} request failed: {error}")
        continue

    # Score with both judges
    r_v = score_response(MODEL, case, metrics, judge_verifier)
    r_c = score_response(MODEL, case, metrics, judge_critic)

    record = {
        "case_id": case.case_id,
        "category": case.category,
        "model": MODEL,
        "response": metrics.text,
        "reasoning_response": metrics.reasoning_text,
        "verifier": {
            "score": r_v.score,
            "recall": r_v.recall,
            "precision": r_v.precision,
            "matched_findings": r_v.matched_findings,
            "missed_findings": r_v.missed_findings,
        },
        "critic": {
            "score": r_c.score,
            "recall": r_c.recall,
            "precision": r_c.precision,
            "matched_findings": r_c.matched_findings,
            "missed_findings": r_c.missed_findings,
        },
    }
    records.append(record)

    status = "SAME" if r_v.score == r_c.score else "DIFF"
    print(
        f"{case.case_id}: verifier={r_v.score:.0f} critic={r_c.score:.0f} "
        f"[{status}]"
    )

# Summary
v_scores = [r["verifier"]["score"] for r in records]
c_scores = [r["critic"]["score"] for r in records]
avg_v = sum(v_scores) / len(v_scores) if v_scores else 0.0
avg_c = sum(c_scores) / len(c_scores) if c_scores else 0.0

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Cases evaluated: {len(records)}")
print(f"Verifier average: {avg_v:.2f}")
print(f"Critic average:  {avg_c:.2f}")
print(f"Difference:      {avg_c - avg_v:+.2f}")

# Save raw results
output = {
    "model": MODEL,
    "verifier_model": VERIFIER,
    "critic_model": CRITIC,
    "summary": {
        "verifier_average": round(avg_v, 2),
        "critic_average": round(avg_c, 2),
        "difference": round(avg_c - avg_v, 2),
        "cases_evaluated": len(records),
    },
    "records": records,
}

ensure_parent_directories(OUTPUT_PATH)
OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
print(f"\nRaw results saved to: {OUTPUT_PATH.resolve()}")
