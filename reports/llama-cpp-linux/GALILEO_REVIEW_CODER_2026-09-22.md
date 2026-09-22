# Galileo code-review benchmark — coder preset (Python)

**Date:** 2026-09-22
**Results:** `results/galileo-review/galileo-review-coder-results.json` (local only, gitignored)
**Language:** python
**Presets:** `config/presets-galileo-coder.ini`
**Judge:** `Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-critic` on local oMLX, thinking suppressed (`enable_thinking=false` + `thinking_budget=0`)
**Outcome:** 120 cases scored, 0 recorded errors, 0 judge fallbacks (verified: no `Judge request failed` lines in the operation log).

## Provenance

| Input | Reference |
|---|---|
| Backend | galileo |
| Test cases | `test_definitions/python.json` |
| Model list | `config/models.json` (or `GALILEO_MODELS` env override) |
| Presets | `config/presets-galileo-coder.ini` |
| Prompt version | 2 |
| Response format | strict JSON schema, maximum six distinct findings |
| Judge | `Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-critic` |

Approximate reproduction (endpoint URL is not recorded):

```bash
GALILEO_BASE_URL=<endpoint> \
GALILEO_TIMEOUT_SECONDS=1800 \
GALILEO_PREDICT_TIMEOUT_MS=1800000 \
GALILEO_RETRY_FAILURES=1 \
GALILEO_REVIEW_RESULTS=<fresh path> \
GALILEO_REVIEW_LOG=<fresh path> \
GALILEO_JUDGE_MODEL=Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-critic \
bin/bench.py galileo-review --lang python --presets config/presets-galileo-coder.ini --judge yes
```

## Effective injected regime

Values below come from `model_parameters.<model>.sampling` — what was actually sent per request. `benchmark_parameters` records runner *defaults* and underreports preset overrides.

| Model | temp | top_p | top_k | min_p | rep_pen | max_tokens | thinking |
|---|---:|---:|---:|---:|---:|---:|---|
| ornith | 0.6 | 0.95 | 20 | 0.05 | 1.0 | 8192 | on @4096 effort=medium |
| qwen3-next | 0.6 | 0.95 | 20 | 0.05 | 1.0 | 8192 | — |
| laguna-xs-2.1 | 0.6 | 0.95 | 20 | 0.05 | 1.0 | 8192 | — |
| gemma4-26B-A4B-it | 0.8 | 0.95 | 64 | 0.05 | 1.05 | 8192 | off |
| qwen3.6-35b-mtp | 0.6 | 0.95 | 20 | 0.05 | 1.0 | 8192 | on @4096 effort=medium |
| north-mini-code-1.0 | 0.6 | 0.95 | 20 | 0.05 | 1.0 | 8192 | — |

> **Parametrization note:** `benchmark_parameters.temperature` is 0 but every model ran at an injected temperature — the recorded params describe the runner default, not the measured regime.

Uniform decoding was the intent: the coder preset holds temp/top-p/top-k/min-p/repeat-penalty constant across aliases so the benchmark ranks models, not operator tuning. Two deliberate exceptions: **gemma4** keeps its tuned sampling (0.8/64/1.05 — cooling it toward uniform previously caused termination issues), and **north-mini** deviates from its near-greedy server alias (0.2 → 0.6) — flag any degradation as profile sensitivity, not capability.

## Results

| Rank | Model | Overall | Quality | Security | Matched | Missed | Unsupported | Out tok | Time |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | ornith | 84.44 | 75.89 | 93.00 | 45 | 12 | 3 | 67021 | 4305 s |
| 2 | qwen3-next | 83.50 | 79.20 | 87.81 | 49 | 8 | 16 | 10238 | 504 s |
| 3 | laguna-xs-2.1 | 82.33 | 75.14 | 89.52 | 48 | 9 | 13 | 12845 | 424 s |
| 4 | gemma4-26B-A4B-it | 80.52 | 72.38 | 88.67 | 40 | 17 | 1 | 5010 | 299 s |
| 5 | qwen3.6-35b-mtp | 79.43 | 76.28 | 82.57 | 43 | 14 | 6 | 85469 | 3248 s |
| 6 | north-mini-code-1.0 | 69.95 | 65.18 | 74.71 | 43 | 14 | 28 | 16000 | 676 s |

Throughput: PP 190.6/97.2/196.6/100.2/209.5/227.5 tok/s, OUT 27.2/24.2/34.7/19.6/26.8/25.1 tok/s (leaderboard order).

## Thinking-channel telemetry

| Model | Reasoning chars | Per-case range | Budget | Inferred |
|---|---:|---|---|---|
| gemma4-26B-A4B-it | 0 | — | — | template ignores enable_thinking (inert) |
| ornith | 261498 | 4566–18589 | 4096 | thinking |
| qwen3.6-35b-mtp | 339827 | 10878–18901 | 4096 | thinking |
| laguna-xs-2.1 | 23876 | 1069–1294 | — | **thinking on runner-default budget — no explicit keys** |
| north-mini-code-1.0 | 25861 | 1172–1367 | — | **thinking on runner-default budget — no explicit keys** |
| qwen3-next | 0 | — | — | no reasoning, no thinking keys sent |

> **Parametrization finding:** `laguna-xs-2.1`, `north-mini-code-1.0` emitted reasoning with no explicit thinking keys — they ran on the runner-default budget, not the intended profile. Their scores are shallow-thinking numbers; a rerun isolates the budget effect.

The reasoning channel doubles as a detector for whether each alias's chat template implements `enable_thinking`. Interpretation of the facts above:

- **Laguna and north-mini are thinking-capable** — previously unverified. Their tight per-case ranges (~1.1–1.4k chars ≈ the 256-token runner default) indicate they hit the shallow cap on essentially every case while ornith/qwen3.6 ran 16× deeper. The preset file has since been updated (`enable-thinking=true`, `thinking-budget=4096`, `reasoning-effort=medium`).
- **qwen3.6 saturates its 4096 budget on most cases** (per-case max ~18.9k chars ≈ 4.7k tokens) — `reasoning-effort=medium` did not visibly discipline it.
- **Gemma and Qwen3-coder-next confirmed inert** on llama.cpp — zero reasoning despite (Gemma) or absent (QCN) the flag. Gemma's `enable-thinking=false` in the preset is documentation, not suppression; the same weights emit reasoning text on oMLX, whose template implements the channel.

## Per-case discrimination

**Best discriminators** (spread ≥40): `quality-06` (50→100), `quality-07` (50→100), `quality-10` (57→100), `security-05` (57→100), `security-08` (50→100), `security-10` (50→100).

**Near-saturated** (spread ≤20): `quality-01`, `quality-08`, `security-03`, `security-04`, `security-06`.

**Systematic misses** — expected findings *every* model failed to match (grading-contract suspects, compare with the causal-07b artifact):

- `quality-01`: missing typing
- `quality-03`: dependency definition
- `quality-05`: ambiguous ordering
- `quality-09`: batch data processing

`quality-09` is additionally the lowest-mean case (58, max 74): all six models found "missing input validation" and all missed "shallow copy" + "batch data processing" — only north-mini and QCN found the shallow copy. The uniform miss pattern resembles the `causal-07b` grading artifact from the logic suite: audit whether those expected findings are inferable from the case text or the judge under-credits paraphrases.

## Cost

- Fastest wall-clock: `gemma4-26B-A4B-it` (299 s); slowest: `ornith` (4305 s) — 14.4×.
- Cheapest output: `gemma4-26B-A4B-it` (5010 tokens).
- `qwen3-next` scored -0.94 vs `ornith` at 0.15× the output tokens.
- `laguna-xs-2.1` scored -2.12 vs `ornith` at 0.19× the output tokens.
- `gemma4-26B-A4B-it` scored -3.92 vs `ornith` at 0.07× the output tokens.
- `qwen3.6-35b-mtp` scored -5.02 vs `ornith` at 1.28× the output tokens.
- `north-mini-code-1.0` scored -14.50 vs `ornith` at 0.24× the output tokens.

Thinking models paid 6–14× wall-clock for marginal or negative score deltas: ornith's 72 min vs QCN's 8.4 min bought +0.94 points overall. If a leaner thinking arm is wanted, `reasoning-effort=low` is the next notch — but see the caveat below: ornith's case wins earlier came *from* reasoning depth.

## vs. baseline

Baseline: `results/galileo-review/galileo-review-results.json`

| Model | This run | Baseline | Δ |
|---|---:|---:|---:|
| ornith | 84.44 | 88.83 | -4.39 |
| gemma4-26B-A4B-it | 80.52 | 84.86 | -4.33 |
| qwen3.6-35b-mtp | 79.43 | 74.45 | +4.98 |

That baseline file is **not** a greedy baseline — its `benchmark_parameters.presets_path` is the same coder preset (it is the interrupted first attempt: ornith + gemma complete, qwen3.6 6/20). Judge-fallback pollution is *not* the explanation for the deltas — zero `Judge request failed` lines in either log. The differences are dominated by **sampling noise at temp 0.6** (per-case swings of ±20–50) plus `reasoning_effort=medium`, which only this run injected.

## Analysis (interpretation of the facts above)

- **Ornith wins on precision economics**: 45 matched / only 3 unsupported — thinking bought precision, not verbosity. Security 93.0 is the field's best subscore.
- **Qwen3-coder-next is the efficiency story**: best recall (49/8) and best quality subscore (79.2) at 10k output tokens in 504 s — ornith needed 67k tokens and 72 minutes for +0.94 points. No thinking channel, still near the top.
- **Qwen3.6 disproves "more reasoning = better"**: 340k reasoning chars (most by far), lowest of the thinking trio.
- **Gemma4 is the precision floor-raiser**: 1 unsupported finding in 20 cases, but the lowest recall (40 matched) — rarely hallucinates, just finds less.
- **North-mini's 28 unsupported findings are not hallucinations** — inspection of `parsed_findings` (e.g. quality-05) shows real-but-unexpected issues (quadratic nested loops, unhashable-input edge cases). Spray-and-pray: broad coverage, low precision, doubly confounded by the temp deviation and the shallow thinking cap.
- **Security > Quality uniformly** (74–93 vs 65–79): either security cases are easier, or the strict schema fits them better — consistent with prior suites.

## Caveats

- Scores are single samples at temperature >0 — expect ±4–5 points of noise on a ~20-case suite; small deltas are not meaningful without reruns.
- The preset file may have changed since this run — the results JSON's `model_parameters` section is the source of truth for what was actually injected.
- Judge verdicts depend on the judge model's own sampling; a different judge or judge timeout fallbacks degrade recall silently (verdict 'no' on failure).
- North-mini's score is confounded twice: the deliberate temp deviation (server alias tuned at 0.2, ran at 0.6) *and* the 256-token thinking default its peers didn't share.

## Next steps

- **Focused rerun** of laguna + north-mini with the updated preset (4096 budget, `reasoning-effort=medium`) against a fresh `GALILEO_REVIEW_RESULTS` path — isolates the 256→4096 thinking-budget effect for ~2 models × 20 cases.
- **Audit the systematic-miss findings** (`quality-01/03/05/09`) against the case definitions before treating them as model failures.
- `galileo-logic`/`galileo-tools` under `presets-galileo-agent.ini` exercise the same aliases on the multi-turn harness where thinking is expected to matter more.
