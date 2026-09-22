# oMLX Logic Benchmark — Agentic Client Presets

**Date:** 2026-09-21
**Suite:** `test_definitions/logic.json` (21 cases, 10 categories, typed grading)
**Runner:** `benchmark_agent_tools.py --suite logic --presets config/presets-omlx-agent.ini`
**Baseline:** `reports/OMLX_LOGIC_2026-09-21.md` (equal-budget coder run at
`max_tokens=4096`; Bonsai from the original run — its coder preset already
carried 4096)
**Results:** `results/omlx-tools/omlx-logic-agent-results.json` (local only,
gitignored)

Same injected-agentic configuration as the tool-use agent run — see
`reports/OMLX_TOOLS_AGENT_2026-09-21.md` for the parameter table. All
parameters below verified from `model_parameters` in the results file.
Everyone ran `max_tokens=4096`, so this compares cleanly against the
equal-budget coder baseline.

## Results

| Rank | Model | Agent | Coder-4096 | Δ | JSON ans | Out tok | Time | Cap hits |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Ornith | **97.62** | 91.27 | **+6.35** | 1.00 | 13942 | 185 s | 0 |
| 1 | Bonsai | **97.62** | 97.62 | 0 | 1.00 | 9459 | 389 s | 0 |
| 3 | Tiel | 95.24 | 97.62 | −2.38 | 1.00 | 8316 | 146 s | 0 |
| 4 | Gemma | 92.06 | 88.10 | **+3.96** | **1.00** | **6039** | **102 s** | **0** |
| 5 | Qwen3.6 | 88.25 | 88.89 | −0.64 | 1.00 | 6985 | 102 s | 0 |
| 6 | Devstral | 66.67 | 69.05 | −2.38 | 1.00 | 3761 | 348 s | 0 |
| | **Field mean** | **89.58** | **88.76** | **+0.82** | | | | |

Every result terminated with `answer` — **zero truncations across all 126
runs**, versus 2 cap hits in the coder-4096 run (both Gemma) and 15 in the
original 1024 run. JSON-answer rate is 1.00 for all six models.

## Correction to the previous report: `causal-07b` did NOT beat the field

The coder report claimed `causal-07b` was "the suite's best discriminator"
on counterfactual reasoning. The stored `extracted_answer` fields — added to
the runner after that report — show that claim was wrong.

Expected: `door_now: "closed"` (enum), `door_if_lockdown_lifted: true`
(boolean). What the models returned:

| Model | `door_now` | `door_if_lockdown_lifted` | Score |
|---|---|---|---:|
| Ornith, Tiel, Qwen, Devstral, Bonsai | `false` | `true` | 50 |
| Gemma | `"closed"` | `"open"` | 50 |

**Every model got the substance right on both questions.** The five
boolean-style answers (`false`/`true`) are semantically correct — the door
is not open, and it would open without lockdown. Gemma went the other
direction: correct enum for `door_now`, then `"open"` where a boolean was
graded.

The failure is a **grading-contract artifact, not a reasoning failure**:
`_answer_contract` names only the required *keys* ("respond with a JSON
object containing exactly these keys"), not their types. The task phrases
the question as "Is the door open now?" — a yes/no framing — while the
grader expects the enum `"closed"`. All six models converged on the same
defensible type choice; that uniformity is evidence the prompt invites it.

**Implication:** `causal-07b` currently measures answer-type guessing, not
intervention reasoning. Options, in increasing strictness: (a) accept
`false`/`"closed"` synonyms in the grader, (b) state types in the answer
contract (`door_now must be "open" or "closed"`), (c) leave as-is and treat
strict schema adherence as part of the test. Option (b) is recommended — it
keeps the reasoning test while removing the ambiguity. Until changed, the
case's effective ceiling is 50 for boolean-phrased answers, and comparisons
on it are still valid *between* models (everyone faces the same trap).

The same artifact hit `causal-07a`: Tiel answered `light: false` and Gemma
`light: "no"` where the enum expects `"off"` — semantically correct, scored
50. That is Tiel's entire −2.38 regression and half of Gemma's missed
points.

## What actually changed per model

### Ornith +6.35 (91.27 → 97.62) — the real winner

Fixed `ordering-05a` (66.7→100) and `causal-07a` (50→100 — produced the
`"off"` enum where the coder run hadn't). Its only remaining miss is the
`causal-07b` contract artifact. Token count nearly identical to the coder
run (13,942 vs 13,952) — the gain is answer quality, not more compute.
Whether the credit belongs to temp 0.7/top_k 40 or to the now-bounded
thinking budget (8192 vs. previously unbounded server-side) is not
separable from n=1; both changed simultaneously.

### Gemma +3.96 (88.10 → 92.06) — the biggest behavioral change

- **Non-termination fixed**: 33,748 → 6,039 output tokens (5.6×), 540 →
  102 s total, and *zero* cap hits versus 2 at the same 4096 ceiling.
  `schedule-03a`, which it failed by rambling past 4096 tokens at coder
  settings, now passes in 369 tokens.
- JSON-answer rate 0.91 → 1.00.
- Cooling temp 0.8→0.7 and narrowing top_k 64→40 is the only change — and
  it is consistent with both the efficiency gain and the quality gain.
  Caveat: single sample; part of the delta could be luck, but the token
  collapse is too large to be pure noise.
- Remaining misses: `ordering-05a` (33.3 — mis-assigned positions) and both
  `causal-07` enum-type artifacts.

### Tiel −2.38 (97.62 → 95.24)

One regression: `causal-07a` 100→50 — answered `light: false` instead of
`"off"`. Same type artifact as `causal-07b`. Its reasoning was correct both
runs; at temp 0.7 it chose boolean phrasing. Effectively unchanged.

### Bonsai 0 (97.62 → 97.62)

Identical score; its only miss remains `causal-07b` (same `false`/`true`
pattern as the field). Now tied by Ornith, and still the slowest model
(389 s total, 18.5 s/turn — `reasoning_effort=medium` + 16384 thinking
budget). Equal quality at 2–5× the wall-clock cost of Tiel/Ornith.

### Qwen3.6 −0.64 (88.89 → 88.25) — churned, not flat

Four case flips in both directions:

- `state-sim-06b` 0→100 and `causal-07a` 50→100 (gains)
- `ordering-05a` 66.7→33.3, **`ordering-05b` 100→20** — a floor-case
  regression: it answered `alice: 1` but marked bob/carol/dave undetermined,
  inventing ambiguity in a fully determined total order
- `grid-path-10b` 100→50

This is the bidirectional flip pattern seen between the 1024 and 4096 coder
runs — consistent with sampling variance at temp 0.7 rather than a
systematic preset effect, though the `ordering-05b` hedge is the kind of
error worth watching for repetition.

### Devstral −2.38 (69.05 → 66.67) — confirmed profile ceiling

Still the weakest, still the same mechanism: six of its seven sub-100
scores were answered in **11–25 tokens** — instant commits, no reasoning
(thinking disabled, and the preset correctly does not try to enable a
channel the model doesn't support). Temp moved 0.15→0.3 with essentially
no effect: its failures (`knights-cookie-01b`, `syllogism-02a`,
`state-sim-06a/b`, `operator-09a`) are the same instant-commit pattern.
Notably it *lost* `selfref-08a` (100→0) — and this time it spent 921 tokens
deliberating before answering wrong, so the regression is not the
instant-commit pattern; it reasoned and failed. Also fixed `operator-09b`
(50→100). Net: confirmed as a coding-profile model at its ceiling on
deliberation tasks; no preset rescues it because the missing ingredient
(reasoning channel) doesn't exist to inject.

## Cross-cutting findings

1. **Answer-contract ambiguity is now the dominant measured artifact.** With
   `extracted_answer` stored, we can see that `causal-07a`/`causal-07b` cost
   the field a combined ~40 quality points across models — all on
   boolean-vs-enum phrasing, all with correct underlying reasoning. Fixing
   the contract (state expected types) would lift every model and remove
   the last artificial ceiling in the suite.

2. **The agentic presets produced zero truncations** — the bounded thinking
   budgets (8192/16384) plus max_tokens 4096 eliminated every cap hit,
   including Gemma's chronic non-termination. The suite now measures pure
   reasoning + format, not budget.

3. **Variance is the honest frame for small deltas.** Excluding the
   contract-artifact cases, the sum of agent-vs-coder flips is roughly
   symmetric (Ornith and Gemma up, Tiel and Devstral down slightly, Qwen
   churned). The +0.82 field mean is within the ~±5-point noise band
   established earlier. The defensible claims are the *mechanism-level*
   ones: Gemma's termination fix, Ornith's real case gains, Devstral's
   hard ceiling — not the headline ordering.

4. **Tool-use vs. logic diverge on the same presets.** Agentic sampling
   *cost* 5.6 points on tool-use (discipline failures: forbidden calls,
   injection compliance, retry loop) but was net-neutral-to-positive on
   logic (+0.82 mean, zero truncations). Interpretation: hotter/more
   diverse sampling helps deliberation slightly and hurts action
   discipline. If one preset must serve both, the coder preset is the
   safer default for tool-facing workloads; the agent preset's value is
   mainly for reasoning-heavy tasks — and even there the gain is small.

5. **Bonsai's efficiency problem persists regardless of preset.** Slowest
   model in both runs (~2–5× the field's per-turn cost). At equal quality,
   Ornith now matches it at half the wall time.

## Caveats

- n=1 per cell per preset; all deltas except Gemma's token collapse are
  within established sampling noise.
- `causal-07b` scoring caveat above applies to *both* reports' numbers —
  treat the case as measuring format strictness until the contract or
  grader is amended.
- Alias models (Bonsai, Devstral) still carry profile-managed non-sampling
  fields (context, KV cache, tool-result budget); sampling was overridden
  and verified, but the stack is not identical to the bare models.

## Bottom line

Agentic presets on logic: **neutral on average (+0.82), transformative for
Gemma (+4 and a 5.6× token reduction), genuinely positive for Ornith
(+6.35), and revealing about the test itself** — `causal-07b` turns out to
grade answer formatting, not the counterfactual reasoning it was designed
to probe. Combined with the tool-use regression (−5.6 mean), the evidence
does not support switching to agentic sampling as a default; it supports
per-workload preset selection.
