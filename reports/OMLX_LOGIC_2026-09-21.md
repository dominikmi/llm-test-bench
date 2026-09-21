# oMLX Logic Reasoning Benchmark — 2026-09-21

Logic suite (`test_definitions/logic.json`, spec `docs/LOGIC_TEST_SPEC.md`):
6 models × 21 cases across 10 competencies — deduction, formal validity,
constraint scheduling, elimination, underdetermination, state simulation,
causal intervention, self-reference, symbol manipulation, constrained
planning. Single-turn, tool-free, judge-free: structured JSON answers graded
on typed fields (exact, normalized, set/map partial credit).

**Two runs, merged on equal budget.** Run 1 used the `max_tokens=1024`
default for all models except Bonsai (preset: 4096 + `reasoning_effort=
medium`) — that run is preserved in `omlx-logic-results.json` and analyzed
below as the budget-confounded baseline. Run 2 (`omlx-logic-4096-results.
json`) re-ran the five non-Bonsai models at `OMLX_MAX_TOKENS=4096`. The
leaderboard below combines Bonsai's run-1 results (already at 4096) with the
five run-2 results — all six at an effective 4096-token ceiling.

## Leaderboard (equal 4096-token budget)

| Rank | Model | Quality | JSON ans | Tok/pt | Mean turn s | Eff. out tok/s † | Cap hits | Genuine fails ‡ |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Tiel-Coder-35B-A3B-MLX-oQ4e-MTP | 97.62 | 0.95 | 3.3 | 5.54 | 58.4 | 0 | 1 |
| 1 | Ternary-Bonsai-2-27B:bonsai2-coder | 97.62 | 0.95 | 4.2 | 15.81 | 25.8 | 0 | 1 |
| 3 | Ornith-1.5-35B-A3B-MLX-4bit | 91.27 | 0.95 | 7.3 | 8.09 | 82.1 | 0 | 3 |
| 4 | Qwen3.6-35B-Claude-Distilled-MLX-oQ4-MTP | 88.89 | 1.00 | 2.9 | 3.72 | 69.9 | 0 | 4 |
| 5 | gemma-4-26B-A4B-it-QAT-MLX-4bit | 88.10 | 0.91 | 18.2 | 25.70 | 62.5 | **2** | 1 |
| 6 | Devstral-Small-2-24B:devstral-code | 69.05 | 1.00 | 1.9 | 11.67 | 11.1 | 0 | 8 |

*† Derived: `completion_tokens / elapsed_seconds` — oMLX omits per-second
rates on non-streaming responses, and elapsed includes prompt evaluation.
‡ Sub-100 results that finished under the cap with a parseable answer —
attributable to reasoning, not truncation. For Bonsai, run-1 values.*

`CALL EFF`/`TURN EFF`/`WASTE` are structurally 1.000/0.000 for all models —
no tools exist in this suite, so there is nothing to waste. Omitted.

## The findings that matter

### 1. `causal-07b` remains unbeaten — the suite's best discriminator

Two-step counterfactual: door opens if card valid OR code correct, *unless
lockdown seals it*. Required: `door_now=closed` **and**
`door_if_lockdown_lifted=true`. Across both runs, **no model has scored
100** — and at 4096 every answer was clean JSON, so this is settled as a
reasoning gap, not a budget artifact:

| Model | Run 1 (≤1024 tok) | Run 2 (≤4096 tok) |
|---|---:|---:|
| Tiel | 0 | 50 |
| Ornith | 50 | **0** (regressed) |
| Gemma | 50 | 50 |
| Qwen3.6 | 50 | 50 |
| Devstral | 50 | 50 |
| Bonsai | 50 | — (50 stands) |

Five models get exactly one of two fields. Which field is not recoverable —
extracted answers are not stored in results — but `causal-07a` (override
only, no counterfactual) is near-solved, so the plausible failure point is
the "remove the override" step. That is inference, not measurement; see
caveats.

### 2. Budget fix resolved 14 of 15 confounded results — and exposed variance

Of the 15 sub-100 results that coincided with the 1024 cap, 14 improved at
4096 — 13 to a full 100, plus Qwen `ordering-05a` 0→67. The lone exception
is Gemma's `ordering-05a`, which hit the cap *again* (see finding 3). But
the same runs also produced **regressions and improvements on cases that
were never truncated** — sampling variance at temperature 0.6, not budget.
Devstral's `ordering-05a`/`selfref-08a` fixes below are variance, not
budget (both were genuine run-1 failures):

| Model | Fixed at 4096 | Regressed at 4096 | Net quality |
|---|---|---|---:|
| Ornith | 02a, 02b, 08a, 10b (0→100 ×4) | 05a (100→67), 07b (50→0) | 76.19→91.27 |
| Gemma | 01a, 04a, 04b, 05b, 08a, 10a, 10b (0→100 ×7) | 03a (100→0, hit cap) | 59.52→88.10 |
| Qwen3.6 | 05a (0→67), 05b (20→100) | 07a (100→50), 06b (100→0) | 89.05→88.89 |
| Devstral | 03a, 05a, 08a (0→100 ×3) | 06a, 06b (100→0 ×2), 10b (100→50) | 61.90→69.05 |
| Tiel | — | 07b improved 0→50 | 95.24→97.62 |

**Read:** single-run scores carry roughly ±5 points of sampling noise. The
Ornith/Qwen/Gemma gap (91.27 vs 88.89 vs 88.10) is within that band — treat
ranks 3–5 as a cluster, not an ordering. The Devstral gap (−19.8 vs rank 5)
is not noise.

### 3. Gemma doesn't terminate — budget wasn't its only problem

Gemma converted all 7 resolvable truncations to 100 — but then hit the
**4096 cap twice**: `schedule-03a` (which it had *passed* at 930 tokens in
run 1 — it talked itself out of a correct answer) and `ordering-05a`, the
epistemic-discipline case where it apparently never stops enumerating. Its
totals are an outlier: 33,748 completion tokens / 540 s — more than Qwen
(5,470 / 78 s) and Devstral (2,720 / 245 s) combined. Highest cost, 5th
place. The two cap-hit zeros remain confounded, but the *pattern* — verbose
deliberation without a stopping criterion — is itself the finding.

### 4. Devstral's instant-commit pattern is confirmed, not truncation

All 8 of Devstral's genuine failures came in at **11–25 completion tokens**
— it emits an answer without deliberating: `state-sim-06a/b` (0 each, 11
tokens — it guesses the final box), `knights-cookie-01b` (0, 11 tok),
`operator-09a` (0, 13 tok), `syllogism-02a` (0, 22 tok). When it *does*
reason it can succeed — `schedule-03a` took 1,501 tokens / 124 s and scored
100 — but the default behavior is commit-first. Notably it *fixed* its
run-1 `ordering-05a` overclaim (0→100 at 35 tokens) — instant answers are
sometimes right, which makes the failure mode harder to detect in practice.

## Per-category means (equal budget)

| Category | Ornith | Tiel | Gemma | Qwen3.6 | Devstral | Bonsai |
|---|---:|---:|---:|---:|---:|---:|
| deduction | 100 | 100 | 100 | 100 | 50 | 100 |
| validity | 100 | 100 | 100 | 100 | 67 | 100 |
| constraints | 100 | 100 | 50 † | 100 | 100 | 100 |
| elimination | 100 | 100 | 100 | 100 | 100 | 100 |
| underdetermination | 83 | 100 | 50 † | 83 | 100 | 100 |
| simulation | 100 | 100 | 100 | 50 | 0 | 100 |
| intervention | 25 | 75 | 75 | 50 | 75 | 75 |
| self_reference | 100 | 100 | 100 | 100 | 100 | 100 |
| symbol_manipulation | 100 | 100 | 100 | 100 | 25 | 100 |
| planning | 100 | 100 | 100 | 100 | 75 | 100 |

*† Gemma's two remaining misses are 4096-cap hits — still confounded.*

## Per-case matrix (quality; † = hit token cap, ○ = non-JSON answer)

| Case | Ornith | Tiel | Gemma | Qwen3.6 | Devstral | Bonsai* |
|---|---|---|---|---|---|---|
| knights-cookie-01a | 100 | 100 | 100 | 100 | 100 | 100 |
| knights-cookie-01b | 100 ○ | 100 | 100 | 100 | **0** | 100 |
| syllogism-02a | 100 | 100 ○ | 100 | 100 | **0** | 100 ○ |
| syllogism-02b | 100 | 100 | 100 | 100 | 100 | 100 |
| syllogism-02c | 100 | 100 | 100 | 100 | 100 | 100 |
| schedule-03a | 100 | 100 | 0 †○ | 100 | 100 | 100 |
| schedule-03b | 100 | 100 | 100 | 100 | 100 | 100 |
| zebra-04a | 100 | 100 | 100 | 100 | 100 | 100 |
| zebra-04b | 100 | 100 | 100 | 100 | 100 | 100 |
| ordering-05a | 67 | 100 | 0 †○ | 67 | 100 | 100 |
| ordering-05b | 100 | 100 | 100 | 100 | 100 | 100 |
| state-sim-06a | 100 | 100 | 100 | 100 | **0** | 100 |
| state-sim-06b | 100 | 100 | 100 | **0** | **0** | 100 |
| causal-07a | 50 | 100 | 100 | 50 | 100 | 100 |
| causal-07b | **0** | 50 | 50 | 50 | 50 | 50 |
| selfref-08a | 100 | 100 | 100 | 100 | 100 | 100 |
| selfref-08b | 100 | 100 | 100 | 100 | 100 | 100 |
| operator-09a | 100 | 100 | 100 | 100 | **0** | 100 |
| operator-09b | 100 | 100 | 100 | 100 | 50 | 100 |
| grid-path-10a | 100 | 100 | 100 | 100 | 100 | 100 |
| grid-path-10b | 100 | 100 | 100 | 100 | 50 | 100 |

*\* Bonsai column is from run 1 — identical effective budget (4096-token
preset), so directly comparable. ○ = `json_answer=false` (prose fallback
still graded — Tiel and Bonsai each scored 100 on a prose-fallback case).*

## Parametrization impact — what is credible, what is not

Actual per-request sampling (from `presets-omlx.ini`, identical across both
runs and the tool-use run):

| Model | temp | top_p | top_k | min_p | rep_pen | Other |
|---|---:|---:|---:|---:|---:|---|
| Ornith | 0.6 | 0.95 | 20 | 0 | 1.0 | — |
| Tiel | 0.6 | 0.95 | 20 | 0 | 1.0 | MTP architecture |
| Gemma | **0.8** | 0.95 | **64** | **0.05** | **1.05** | hottest sampling in field |
| Qwen3.6 | 0.6 | 0.95 | 20 | 0 | 1.0 | MTP, reasoning-distilled |
| Devstral | — | — | — | — | — | **no preset — server profile defaults** |
| Bonsai | — | — | — | — | — | `reasoning_effort=medium`, cap 4096 |

**Credible claims:**

- **Bonsai's `reasoning_effort=medium` is a documented mechanism, not a
  correlation.** Its default (and `low`) effort leaks reasoning into the
  content channel, breaking the JSON contract; `medium` emits a proper
  think channel plus clean content. Effect here: 0 cap hits and 20/21 full
  marks — but at 15.8 s/turn mean it is ~3× slower than Tiel. Positive on
  quality and format, negative on latency. This trade is reproducible
  (same fix documented in the review benchmark).
- **The A3B trio (Ornith/Tiel/Qwen) share identical sampling** — quality
  differences among them are attributable to weights/training only. This
  is the cleanest comparison axis in the field: Tiel's edge over Ornith
  (97.6 vs 91.3) is a model difference, not a tuning artifact.
- **Temperature explains the observed run-to-run noise floor.** Identical
  configs flipped cases in both directions between runs — consistent with
  temp 0.6 sampling. Gemma runs hotter still (0.8, top_k 64, min_p 0.05);
  its flips were larger in magnitude and it alone failed to terminate at
  4096 twice. Consistent with wider sampling producing longer, less
  constrained deliberation — plausible mechanism, single model, not proven.
- **Devstral has no preset — it ran on the `devstral-code` server
  profile's defaults, which are unknown to this benchmark.** Its
  instant-commit pattern (11–25 tokens on failures) cannot be attributed
  to sampling, but the *absence of an explicit preset is a control gap*:
  its results were measured under different, undocumented conditions than
  the rest of the field. Recommend adding an explicit entry to normalize.

**Not credible from this data:**

- **Quantization differences** (4-bit vs oQ4e vs oQ4 vs 6-bit vs ternary)
  cannot be isolated — every model differs in both weights and quant, so
  no quality delta can be attributed to compression.
- **MTP (Tiel, Qwen)** — effective tok/s here includes prompt evaluation,
  and no non-MTP twin exists in the field; prior dedicated tests showed
  ~no gain over dense decode. Nothing in this run changes that.
- **`min_p`/`rep_penalty` as verbosity causes** — Gemma's rambling is
  *consistent* with its hotter sampling but equally consistent with model
  behavior; a temp-0.6 Gemma run would be needed to separate them.

## Caveats

- **Sampling variance is now measured, not assumed.** Identical-config runs
  flipped individual cases in both directions (e.g. Qwen 06b 100→0,
  Devstral 05a 0→100). ±1 case ≈ ±4.8 quality points — any ranking within
  that band is provisional. N≥2 runs per model would firm it up.
- **Extracted answers are not stored.** On `causal-07b` we know each model
  missed one of two fields but not which. Storing the parsed answer JSON in
  the result record is a worthwhile runner improvement for exactly this
  audit scenario.
- **Gemma's two cap-hit zeros stay confounded** even at 4096 — its
  non-termination means no budget may resolve them; the verbosity is the
  measurable defect regardless of what the truncated reasoning contained.
- **The ranks-3–5 cluster is real but unordered.** Ornith/Qwen/Gemma at
  91.3/88.9/88.1 with ±5 noise are statistically indistinguishable in one
  run. Tiel/Bonsai at 97.6 and Devstral at 69.1 are separated beyond noise.
- **21 cases is a screen.** Floor cases worked as designed: every floor
  miss across both runs is either a cap hit or a Devstral instant-commit —
  no model failed a floor case on deliberated reasoning.
- **Suite ≠ tool-use.** Devstral is weakest here and failed agentically too
  (prompt injection); Qwen3.6 shows the inverse pattern (strong logic,
  failed error-recovery with tools). The two benchmarks measure different
  competencies — neither subsumes the other.
