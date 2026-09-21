# oMLX Logic Reasoning Benchmark — 2026-09-21

First run of the logic suite (`test_definitions/logic.json`, spec
`docs/LOGIC_TEST_SPEC.md`): 6 models × 21 cases across 10 competencies —
deduction, formal validity, constraint scheduling, elimination,
underdetermination, state simulation, causal intervention, self-reference,
symbol manipulation, constrained planning. Single-turn, tool-free, judge-free:
each case requires a structured JSON answer graded on typed fields (exact,
normalized, set/map partial credit).

**Run configuration (matters — see caveats):** `max_tokens=1024` default for
all models **except Bonsai**, whose preset supplies `max_tokens=4096` and
`reasoning_effort=medium`. Non-streaming, temperature per presets.

## Leaderboard

| Rank | Model | Quality | JSON ans | Tok/pt | Mean turn s | Eff. out tok/s † | Truncated fails ‡ | Genuine fails |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Ternary-Bonsai-2-27B:bonsai2-coder | 97.62 | 0.95 | 4.2 | 15.81 | 25.8 | 0 | 1 |
| 2 | Tiel-Coder-35B-A3B-MLX-oQ4e-MTP | 95.24 | 0.95 | 3.6 | 5.91 | 58.1 | 0 | 1 |
| 3 | Qwen3.6-35B-Claude-Distilled-MLX-oQ4-MTP | 89.05 | 0.91 | 3.8 | 4.72 | 71.7 | 2 | 1 |
| 4 | Ornith-1.5-35B-A3B-MLX-4bit | 76.19 | 0.76 | 8.1 | 7.62 | 81.3 | 4 | 2 |
| 5 | Devstral-Small-2-24B:devstral-code | 61.90 | 0.95 | 2.8 | 15.03 | 11.5 | 1 | 8 |
| 6 | gemma-4-26B-A4B-it-QAT-MLX-4bit | 59.52 | 0.62 | 13.2 | 12.31 | 63.8 | 8 | 1 |

*† Derived: `completion_tokens / elapsed_seconds` — oMLX does not emit
per-second rates on non-streaming responses, and elapsed includes prompt
evaluation, so these understate raw decode speed. Devstral's 11.5 is
wall-clock reality (316 s total, 3 627 tokens).*

*‡ `completion_tokens` hit the 1024 cap (4096 for Bonsai) on a case scored
below 100. "Genuine fails" = sub-100 results that finished under the cap with
a parseable JSON answer — attributable to reasoning, not truncation.*

The `CALL EFF`, `TURN EFF`, `WASTE` columns are structurally 1.000/0.000 for
every model — no tools exist in this suite, so there is nothing to waste.
They are omitted here; JSON-answer rate and token cost carry the signal.

## The three findings that matter

### 1. `causal-07b` beat every model — the only universal miss

The case is a two-step counterfactual: door opens if card valid OR code
correct, *unless lockdown seals it*. State: card invalid, code correct,
lockdown active. Required: `door_now=closed` **and**
`door_if_lockdown_lifted=true`.

All six models emitted clean JSON well under the token cap — and **none
scored 100**. Five scored exactly 50 (one field of two); Tiel scored 0. Since
extracted answers are not stored in results, which specific field each model
missed is not recoverable post-hoc — but the uniform partial credit with zero
formatting failures means the miss is reasoning, not parsing. The plausible
read is the counterfactual half (removing the override correctly) — `07a`,
which only tests the override itself, scored 91.7 mean — but that is
inference, not evidence; see caveats.

### 2. Devstral's failures are genuine — and reveal a shallow-commit pattern

Devstral's 8 genuine failures were not truncation: it answered in **11–25
completion tokens** on six of them (11 on `knights-cookie-01b`, 13 on
`operator-09a`, 22 on `syllogism-02a`, 25 on `causal-07b`). It commits to an
answer without deliberation — and is wrong often enough to cost 38 points of
quality. Its worst-case is `ordering-05a`: valid JSON, 434 tokens, scored 0 —
it reported determinate positions the premises do not fix, i.e. overclaimed
on the suite's flagship epistemic-discipline case. Contrast with the tool
suite, where its failure mode was different (obeying a prompt injection).

### 3. Half of Gemma's score is a budget artifact — rerun needed

**Every one of Gemma's 8 zero-scores hit exactly 1024 tokens.** It also has
the worst JSON-answer rate (0.62) — and the two observations are the same
event: its responses run long and get cut before the JSON answer is emitted.
This says nothing yet about whether the reasoning inside those truncated
responses was right. Ornith is in the same boat on a smaller scale: all 4 of
its zeros are cap hits (`syllogism-02a/b`, `selfref-08a`, `grid-path-10b`).
Qwen3.6's two underdetermination failures (`ordering-05a/b`) are likewise
confounded — both responses were truncated mid-thought.

Conversely, Bonsai's clean sweep (20/21 at 100, zero cap hits) happened under
the only adequate budget — 4096 tokens via its preset. The honest reading of
this leaderboard is not "Bonsai is best" but "**Bonsai was the only model
allowed to finish thinking**". Tiel's 95.24 at the 1024 cap (its one cap hit
still scored 100 via prose fallback) is arguably the most impressive raw
result — it is both fast (5.91 s/turn mean) and accurate within a budget that
handicapped everyone else.

## Per-category means

| Category | Ornith | Tiel | Gemma | Qwen3.6 | Devstral | Bonsai |
|---|---:|---:|---:|---:|---:|---:|
| deduction | 100 | 100 | 50 ‡ | 100 | 50 | 100 |
| validity | 33 ‡ | 100 | 100 | 100 | 67 | 100 |
| constraints | 100 | 100 | 100 | 100 | 50 | 100 |
| elimination | 100 | 100 | 0 ‡ | 100 | 100 | 100 |
| underdetermination | 100 | 100 | 0 ‡ | 10 ‡ | 50 | 100 |
| simulation | 100 | 100 | 100 | 100 | 50 | 100 |
| intervention | 50 | 50 | 75 | 75 | 75 | 75 |
| self_reference | 50 ‡ | 100 | 50 ‡ | 100 | 50 | 100 |
| symbol_manipulation | 100 | 100 | 100 | 100 | 25 | 100 |
| planning | 50 ‡ | 100 | 0 ‡ | 100 | 100 | 100 |

*‡ = at least one sub-100 score in the category coincides with a 1024-token
truncation — treat as "unknown", not "failed".*

## Per-case matrix (quality; † = truncated at cap, ○ = non-JSON answer)

| Case | Ornith | Tiel | Gemma | Qwen3.6 | Devstral | Bonsai |
|---|---|---|---|---|---|---|
| knights-cookie-01a | 100 | 100 | 0 †○ | 100 | 100 | 100 |
| knights-cookie-01b | 100 ○ | 100 | 100 | 100 | **0** | 100 |
| syllogism-02a | 0 †○ | 100 | 100 | 100 | **0** | 100 ○ |
| syllogism-02b | 0 †○ | 100 | 100 | 100 | 100 | 100 |
| syllogism-02c | 100 | 100 | 100 | 100 | 100 | 100 |
| schedule-03a | 100 | 100 | 100 | 100 | 0 †○ | 100 |
| schedule-03b | 100 | 100 | 100 | 100 | 100 | 100 |
| zebra-04a | 100 | 100 | 0 †○ | 100 | 100 | 100 |
| zebra-04b | 100 | 100 | 0 †○ | 100 | 100 | 100 |
| ordering-05a | 100 | 100 ○ | 0 †○ | 0 †○ | **0** | 100 |
| ordering-05b | 100 | 100 | 0 †○ | 20 †○ | 100 | 100 |
| state-sim-06a | 100 | 100 | 100 | 100 | 100 | 100 |
| state-sim-06b | 100 | 100 | 100 | 100 | **0** | 100 |
| causal-07a | 50 | 100 | 100 | 100 | 100 | 100 |
| causal-07b | 50 | **0** | 50 | 50 | 50 | 50 |
| selfref-08a | 0 †○ | 100 | 0 †○ | 100 | **0** | 100 |
| selfref-08b | 100 | 100 | 100 | 100 | 100 | 100 |
| operator-09a | 100 | 100 | 100 | 100 | **0** | 100 |
| operator-09b | 100 | 100 | 100 | 100 | 50 | 100 |
| grid-path-10a | 100 | 100 | 0 †○ | 100 | 100 | 100 |
| grid-path-10b | 0 †○ | 100 | 0 †○ | 100 | 100 | 100 |

Bold = genuine failure (finished under cap, parseable answer, wrong).
† = response hit the token cap on a sub-100 case — outcome confounded.
○ = `json_answer=false` (prose fallback still graded; Tiel/Bonsai each scored
100 on a prose-fallback case).

## Caveats

- **The 1024-token budget confounds 15 of 29 sub-100 results.** For a clean
  ranking, rerun with `OMLX_MAX_TOKENS=4096` so the non-preset models get
  Bonsai's budget. Until then, Gemma's 59.52 and Ornith's 76.19 are lower
  bounds, not measurements.
- **Results do not store extracted answers.** On `causal-07b` we know each
  model missed one field but not which — adding the parsed answer JSON to
  the result record is a worthwhile runner improvement for exactly this
  audit scenario.
- **21 cases is a screen, not a ranking** — one confounded case swings a
  model ~4.8 points. The `floor`/`standard`/`hard` tags earned their keep:
  of the 9 `floor` cases, every miss is either a truncation or a Devstral
  instant-commit (`operator-09a`, 13 tokens) — no model failed a floor case
  on deliberated reasoning.
- **JSON-answer rate is budget-correlated**, not a pure compliance metric:
  Gemma's 8 JSON failures are exactly its 8 truncations. At an adequate
  budget this column should approach 1.0 for instruct-tuned models.
- **Suite ≠ tool-use.** A model can excel here and fail agentically
  (Devstral) or vice versa (Qwen3.6's error-recovery miss). The two reports
  measure different competencies; neither subsumes the other.
