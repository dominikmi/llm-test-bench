# Galileo Logic Suite — Coder Models (Agent Profile) — 2026-09-22

**Date:** 2026-09-22
**Results:** `results/galileo-tools/galileo-logic-results.json` (local only, gitignored)
**Suite:** logic
**Presets:** `config/presets-galileo-agent.ini`
**Outcome:** 126 cases scored, 0 recorded errors.

## Provenance

| Input | Reference |
|---|---|
| Backend | galileo |
| Test cases | `test_definitions/logic.json` |
| Model list | `config/models.json` (or `GALILEO_MODELS` env override) |
| Presets | `config/presets-galileo-agent.ini` |
| Prompt version | — |
| Response format | — |
| Judge | `none (deterministic grading)` |

Approximate reproduction (endpoint URL is not recorded):

```bash
GALILEO_BASE_URL=<endpoint> \
GALILEO_TIMEOUT_SECONDS=1800 \
GALILEO_MAX_TOKENS=1024 \
GALILEO_RETRY_FAILURES=1 \
GALILEO_BENCH_RESULTS=<fresh path> \
GALILEO_BENCH_LOG=<fresh path> \
bin/bench.py galileo-logic --presets config/presets-galileo-agent.ini
```

## Effective injected regime

Values below come from `model_parameters.<model>.sampling` — what was actually sent per request. `benchmark_parameters` records runner *defaults* and underreports preset overrides.

| Model | temp | top_p | top_k | min_p | rep_pen | max_tokens | thinking |
|---|---:|---:|---:|---:|---:|---:|---|
| ornith | 0.7 | 0.95 | 40 | 0.05 | 1.0 | 12288 | on @8192 |
| qwen3.6-35b-mtp | 0.7 | 0.95 | 40 | 0.05 | 1.0 | 12288 | on @8192 |
| gemma4-26B-A4B-it | 0.7 | 0.95 | 40 | 0.05 | 1.0 | 4096 | off |
| laguna-xs-2.1 | 0.7 | 0.95 | 40 | 0.05 | 1.0 | 12288 | on @8192 |
| north-mini-code-1.0 | 0.7 | 0.95 | 40 | 0.05 | 1.0 | 12288 | on @8192 |
| qwen3-next | 0.7 | 0.95 | 40 | 0.05 | 1.0 | 4096 | off |

## Results

| Rank | Model | Quality | Call eff | Turn eff | Waste | Invalid | Off-plan | Forbidden | Identical retry | Cap hits | JSON ans | Out tok | Time |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | ornith | 95.24 | 1.000 | 1.000 | 0.000 | 0 | 0 | 0 | 0 | 0 | 1.00 | 12033 | 481 s |
| 2 | qwen3.6-35b-mtp | 95.24 | 1.000 | 1.000 | 0.000 | 0 | 0 | 0 | 0 | 0 | 1.00 | 75775 | 2934 s |
| 3 | gemma4-26B-A4B-it | 92.06 | 1.000 | 1.000 | 0.000 | 0 | 0 | 0 | 0 | 0 | 1.00 | 7028 | 347 s |
| 4 | laguna-xs-2.1 | 90.48 | 1.000 | 1.000 | 0.000 | 0 | 0 | 0 | 0 | 0 | 0.95 | 27085 | 826 s |
| 5 | north-mini-code-1.0 | 88.25 | 1.000 | 1.000 | 0.000 | 0 | 0 | 0 | 0 | 0 | 1.00 | 12599 | 530 s |
| 6 | qwen3-next | 78.57 | 1.000 | 1.000 | 0.000 | 0 | 0 | 0 | 0 | 0 | 0.95 | 4989 | 265 s |

Throughput: PP 89.8/102.8/112.8/104.1/112.4/58.5 tok/s, OUT 27.7/27.5/22.8/35.0/25.3/24.3 tok/s (leaderboard order).

## Per-case discrimination

**Best discriminators** (spread ≥40): `grid-path-10a` (50→100), `knights-cookie-01b` (0→100), `ordering-05a` (33→100), `ordering-05b` (20→100), `state-sim-06a` (0→100), `state-sim-06b` (0→100), `syllogism-02c` (0→100).

**Near-saturated** (spread ≤20): `causal-07a`, `causal-07b`, `grid-path-10b`, `knights-cookie-01a`, `operator-09a`, `operator-09b`, `schedule-03a`, `schedule-03b`, `selfref-08a`, `selfref-08b`, `syllogism-02a`, `syllogism-02b`, `zebra-04a`, `zebra-04b`.

## Cost

- Fastest wall-clock: `qwen3-next` (265 s); slowest: `qwen3.6-35b-mtp` (2934 s) — 11.1×.
- Cheapest output: `qwen3-next` (4989 tokens).
- `qwen3.6-35b-mtp` scored +0.00 vs `ornith` at 6.30× the output tokens.
- `gemma4-26B-A4B-it` scored -3.17 vs `ornith` at 0.58× the output tokens.
- `laguna-xs-2.1` scored -4.76 vs `ornith` at 2.25× the output tokens.
- `north-mini-code-1.0` scored -6.98 vs `ornith` at 1.05× the output tokens.
- `qwen3-next` scored -16.67 vs `ornith` at 0.41× the output tokens.

## Analysis (interpretation of the facts above)

### The leaderboard is three regimes, not six models

| Regime | Models | Quality | Mechanism |
|---|---|---:|---|
| Solved everything solvable | ornith, qwen3.6 | 95.24 | Missed only the two contract-broken cases (below) |
| One real miss | gemma4 (92.06), laguna (90.48), north-mini (88.25) | | `ordering-05*`, `state-sim-06b` — genuine reasoning slips |
| Structurally weaker | qwen3-next | 78.57 | Four zero-scores: `knights-cookie-01b`, `syllogism-02c`, `state-sim-06a`, `grid-path-10a` |

### `causal-07a`/`causal-07b` are a test-definition bug, not a model failure

Every model scored **exactly 50.0** on both — inspected `extracted_answer` shows all models were semantically *correct* but type-mismatched against the strict grader:

- `causal-07a` expects enum `light ∈ {"on","off"}`; every model emitted `light: false` (JSON boolean). `str(False)` → `"false"` ∉ accepted set → miss. The `blocking_cause` string field passed for all (substring match).
- `causal-07b` expects boolean `door_if_lockdown_lifted=true`; models emitted the string `"open"` — and the enum-typed `door_now` got booleans (`false` vs `"closed"`) from some models.

The grader (`_field_matches`) does no boolean↔enum normalization. Tasks phrase yes/no questions but declare enum literals; models reasonably answer with JSON booleans. **Recommend fixing the test definitions** (accept boolean for two-valued enums, or rephrase tasks to demand the literal enum tokens) rather than accepting the −4.76 ceiling — the max attainable quality under this contract is 95.24, which is exactly what ornith and qwen3.6 scored.

### qwen3.6's tie cost 6.3× the tokens

Same quality as ornith (95.24) at **75,775 output tokens vs 12,033** — mean 3,608/case vs 573, hitting the 12,288 cap at least once. Thinking@8192 bought nothing over ornith's cheaper reasoning on this suite; the deterministic-grading cases reward correct final answers, not reasoning depth. This mirrors the review-suite pattern (most reasoning chars, no quality edge).

### Thinking correlates with the hard-case outcomes — imperfectly

qwen3-next (thinking off) zero-scored `state-sim-06a`, `knights-cookie-01b`, `syllogism-02c` — the multi-step simulation/constraint cases — while gemma4 (also thinking off) missed only `ordering-05a`. The thinking quartet (ornith, qwen3.6, laguna, north-mini) collectively failed just `state-sim-06b` (laguna) and `ordering-05*` (north-mini) outside the broken pair. Directionally consistent with "thinking helps state simulation," but gemma4's 92.06 with thinking off is the counterexample — architecture quality may matter more than the channel itself.

### Efficiency note

Perfect call/turn efficiency and zero waste across all six models — the logic suite is single-turn deterministic answering, so the agentic-efficiency columns carry no signal here (the suite never exercises tool-call discipline). The `json_answer` flag (laguna, qwen3-next at 0.95) marks one case each of non-JSON final output — worth a spot-check if format compliance matters downstream.

## Caveats

- Scores are single samples at temperature 0.7 — expect ±4–5 points of noise on a 21-case suite; the 90.48–95.24 band may overlap on rerun. The 78.57 gap (qwen3-next) is large enough to be real.
- `causal-07a/07b` scores are suppressed identically for all models — treat 95.24 as the effective ceiling; cross-suite comparisons including these two cases underestimate absolute capability.
- The preset file may have changed since this run — the results JSON's `model_parameters` section is the source of truth for what was actually injected.
