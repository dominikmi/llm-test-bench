# Galileo Review Benchmark — Client-Side Coder Presets

**Date:** 2026-09-22
**Suite:** `test_definitions/python.json` (20 cases: 10 quality + 10 security, strict JSON schema, max 6 findings)
**Runner:** `bin/bench.py galileo-review --lang python --judge yes --presets config/presets-galileo-coder.ini`
**Judge:** `Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-critic` on oMLX (`127.0.0.1:8000`), thinking suppressed via `enable_thinking=false` + `thinking_budget=0`
**Results:** `results/galileo-review/galileo-review-coder-results.json` (local only, gitignored)
**Outcome:** 120/120 cases scored, zero failures, zero errors, zero judge fallbacks.

## Injected regime (verified from `model_parameters.sampling` in the results file)

Uniform decoding for all six aliases: temp 0.6, top-p 0.95, top-k 20, min-p 0.05,
repeat-penalty 1.0, max_tokens 8192. Exceptions: **gemma4** keeps its tuned coder
sampling (temp 0.8, top-k 64, repeat 1.05) — cooling it toward uniform previously
caused termination issues. Thinking: ornith and qwen3.6 got `enable_thinking` +
`thinking_budget_tokens=4096` + `reasoning_effort=medium`; gemma4 and
qwen3-coder-next ran non-thinking; laguna and north-mini had no thinking keys in
the file at run time and fell back to the **runner default** (`enable_thinking`,
budget 256) — verified below, this matters for interpretation.

Note: `benchmark_parameters.temperature`/`thinking_budget_tokens` in the results
JSON record the runner *defaults* (0 / 256), not the per-request injected values —
the `model_parameters` section carries what was actually sent.

## Results

| Rank | Model | Overall | Quality | Security | Matched | Missed | Unsupported | Out tok | Time |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | **coder-ornith** | **84.44** | 75.89 | **93.00** | 45 | 12 | **3** | 67 021 | 4 305 s |
| 2 | coder-qwen3-coder-next | 83.50 | **79.20** | 87.81 | **49** | **8** | 16 | 10 238 | 504 s |
| 3 | coder-laguna-xs-2.1 | 82.33 | 75.14 | 89.52 | 48 | 9 | 13 | 12 845 | 424 s |
| 4 | coder-gemma4-26B | 80.52 | 72.38 | 88.67 | 40 | 17 | **1** | 5 010 | 299 s |
| 5 | coder-qwen3.6-35b-mtp | 79.43 | 76.28 | 82.57 | 43 | 14 | 6 | **85 469** | 3 248 s |
| 6 | coder-north-mini-code | 69.95 | 65.18 | 74.71 | 43 | 14 | **28** | 16 000 | 676 s |

Throughput (server-side, single request at a time): prompt processing 97–227
tok/s; output 19.6–34.8 tok/s. laguna is the fastest generator (34.7 tok/s),
gemma the slowest (19.6).

## The thinking-support question is now resolved empirically

The reasoning channel acts as a detector for whether each alias's chat template
implements `enable_thinking`:

| Model | Reasoning chars (20 cases) | Per-case range | Verdict |
|---|---:|---|---|
| qwen3.6 | 339 827 | 10 878–18 901 | thinking, saturating the 4096 budget most cases |
| ornith | 261 498 | 4 566–18 589 | thinking, case-dependent length |
| north-mini | 25 861 | 1 172–1 367 | **thinks — but pinned at the 256 runner default** |
| laguna | 23 876 | 1 069–1 294 | **thinks — pinned at 256** |
| gemma4 | 0 | — | template ignores `enable_thinking` |
| qwen3-coder-next | 0 | — | template ignores `enable_thinking` |

Laguna's and north-mini's per-case reasoning lengths are suspiciously tight
(~1.1–1.4k chars ≈ 256 tokens every case) — they hit the shallow default budget
on essentially every case while ornith/qwen3.6 ran 16× deeper. Their scores are
therefore **shallow-thinking numbers**; the preset file has since been updated
(`enable-thinking=true`, `thinking-budget=4096`, `reasoning-effort=medium`), so a
rerun isolates the budget effect cleanly.

## Findings accounting — precision/recall trade-off is the real differentiator

- **Qwen3-coder-next is the recall leader** (49 matched / 8 missed) with moderate
  noise (16 unsupported) — best quality subscore (79.20) of the field despite
  having no thinking channel at all.
- **Gemma4 is the precision floor-raiser**: only 1 unsupported finding in 20
  cases, but the lowest recall (40 matched). It rarely hallucinates; it just
  finds less.
- **Ornith is the cleanest thinker**: 45 matched with only 3 unsupported —
  thinking bought precision, not just verbosity.
- **North-mini's 28 unsupported findings** are the outlier. Inspection of
  `parsed_findings` (e.g. quality-05) shows they're not garbage — it reports
  real-but-unexpected issues (quadratic nested loops, repeated membership tests,
  unhashable-input edge cases) that don't match the expected-finding set. It is
  spray-and-pray: broad coverage at 65–75 precision, dragged down by both the
  noise and the shallow thinking cap.
- **Qwen3.6 produced the most reasoning** (340k chars, ~4.3k output tok/case)
  yet scored below non-thinking QCN — thinking volume does not buy findings.

## Cost asymmetry

Thinking models paid 6–14× wall-clock for marginal or negative score deltas:
ornith 72 min vs QCN's 8.4 min for +0.94 points overall. On this suite,
`reasoning_effort=medium` did not visibly discipline qwen3.6 (still saturating
the budget); if a leaner thinking arm is wanted, `low` is the next notch.

## Per-case discrimination

- **Best discriminators** (spread ≥43): quality-06 (50→100), quality-07 (50→100),
  security-08 (50→100), security-10 (50→100), security-05 (57→100), quality-10
  (57→100).
- **Saturated** (spread ≤20): quality-01, quality-08, security-03, security-04,
  security-06.
- **quality-09 is systematically under-scored** (mean 58, max 74): all six models
  found "missing input validation" and all missed "shallow copy" +
  "batch data processing" — only north-mini and QCN additionally found the shallow
  copy. The uniform miss pattern resembles the `causal-07b` grading artifact from
  the logic suite: worth auditing whether the expected findings are inferable
  from the case text or the judge is under-crediting paraphrases.

## vs. the earlier partial run (`galileo-review-results.json`)

That file is **not** a greedy baseline — its `benchmark_parameters.presets_path`
is the same coder preset (it is the interrupted first attempt: ornith + gemma
complete, qwen3.6 6/20). Comparing the two complete models: ornith 88.8→84.4 and
gemma 84.9→80.5. Judge-fallback pollution is *not* the explanation — zero
"Judge request failed" lines in both logs. The deltas are dominated by
**sampling noise at temp 0.6** (per-case swings of ±20–50: ornith −50 on
quality-03, +20×3 elsewhere) plus `reasoning_effort=medium` which only the
second run injected. Treat single-run scores at this temperature as carrying
roughly ±4–5 points of noise on a 20-case suite.

## Caveats

- North-mini's score is confounded by the deliberate temp deviation
  (server alias is tuned at 0.2; the uniform regime ran it at 0.6) — flag as
  profile sensitivity, not capability.
- Scores are single samples at temp 0.6; the suite is not power-law sensitive
  enough to separate models within ~5 points without reruns.
- `presets-galileo-coder.ini` now differs from the file version that produced
  this run (laguna/north-mini thinking keys added afterward) — the results file
  is the source of truth for what actually ran.
