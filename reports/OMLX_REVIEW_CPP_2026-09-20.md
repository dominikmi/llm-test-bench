# oMLX Review Benchmark — C++ — 2026-09-20

Run: `--lang=cpp --presets --judge yes`, thinking enabled (budget 256),
`max_tokens=1024`, LLM judge on the remote llama.cpp router. 120 cases
(6 models × 20), 0 failures. Cases: `test_definitions/cpp.json`.

| Model | Overall | Quality | Security | PP tok/s | Out tok/s |
|---|---:|---:|---:|---:|---:|
| gemma-4-26B-A4B-it-QAT-MLX-4bit | 86.83 | 79.67 | 94.00 | 432.9 | 61.8 |
| Qwen3.6-35B-Claude-Distilled-MLX-oQ4-MTP | 81.25 | 70.93 | 91.57 | 405.4 | 75.8 |
| Tiel-Coder-35B-A3B-MLX-oQ4e-MTP | 80.27 | 77.88 | 82.67 | 477.2 | 65.1 |
| Ornith-1.5-35B-A3B-MLX-4bit | 72.94 | 58.00 | 87.89 | 421.0 | 78.6 |
| Ternary-Bonsai-2-27B:bonsai2-coder | 65.33 | 68.00 | 62.67 | 82.5 | 20.8 |
| Devstral-Small-2-24B:devstral-code | 61.01 | 60.30 | 61.72 | 99.5 | 12.5 |

*`security-08` was re-run on a corrected fixture (see below); all other cases
are from the original run.*

## Three-language comparison

| Model | Python | JavaScript | C++ | Spread |
|---|---:|---:|---:|---:|
| Tiel | 90.27 | 90.87 | 80.27 | 10.6 |
| Qwen3.6 | 81.81 | 91.56 | 81.25 | 10.3 |
| Gemma | 81.17 | 86.01 | 86.83 | **5.7** |
| Ornith | 82.44 | 80.70 | 72.94 | 9.5 |
| Bonsai | 76.17 | 64.33 | 65.33 | 11.8 |
| Devstral | 63.27 | 82.74 | 61.01 | 21.7 |

Gemma is the most *consistent* model across languages (spread 5.7) and takes
the C++ crown — the only one above 85. C++ compressed the whole field: every
model scored lower than on JavaScript, and quality recall dropped across the
board.

## Per-model diagnostics

| Model | Recall (q/s) | Precision | Unsup | Truncated | Clean JSON | Think |
|---|---:|---:|---:|---:|---:|---:|
| Gemma | 81.7 (73/90) | 96.7 | 2 | 1/20 | 18/20 | 20/20 |
| Tiel | 83.3 (83/83) | 88.0 | 12 | 1/20 | 7/20 | 20/20 |
| Qwen3.6 | 80.0 (70/90) | 89.5 | 10 | 0/20 | 0/20 | 20/20 |
| Ornith | 65.0 (43/87) | 97.3 | 2 | 8/20 | 6/20 | 20/20 |
| Bonsai | 54.2 (53/55) | 100.0 | 0 | 20/20 | 0/20 | 20/20 |
| Devstral | 68.3 (63/73) | 67.5 | 36 | 0/20 | 19/20 | 0/20 |

## What the C++ run shows

- **Gemma's discipline pays off on the hardest language**: 18/20 clean JSON,
  1/20 truncated, precision 96.7 — it does not find the most (recall 81.7,
  second to Tiel's 83.3) but wastes almost nothing. On the language where
  verbosity is most punished, economy wins.
- **Tiel stays recall leader** (83.3, balanced q/s) but its precision slipped
  to 88.0 — 12 unsupported findings, mostly speculative C++-isms the judge
  rejected. It also missed the corrected IDOR case entirely — see below.
- **Qwen3.6 climbs to #2** (81.25) after the fixture fix: still zero
  truncations and real thinking, but 10 unsupported findings and quality
  recall dropped to 70.
- **Ornith's recall collapsed on quality** (43.3) — the conservative reviewer
  finds too little when cases get idiomatic (RAII, `std::` specifics) —
  though its security recall is now solid (86.7) with the fixed case.
- **Bonsai hit a wall**: 20/20 truncated — every single response ran to the
  cap. Think channel works (20/20), precision a perfect 100.0, but recall
  54.2 is the field's worst. On C++'s denser cases the verbose medium-effort
  reasoning never leaves room for the findings list. `max_tokens` 1024 is
  now the binding constraint — this model needs a bigger budget or a terser
  effort level to score what it knows.
- **Devstral fell back to earth** (61.0): 36 unsupported findings — nearly
  2/case — the padding strategy that was merely tolerated on JS gets
  punished when the judge can check against C++ specifics. Precision 67.5
  is the field's worst by far.

## security-08 (IDOR): fixture defect found and fixed mid-run

**Original run — 0/6 sweep.** All six models scored 0.0 on
`security-08` — a `std::stoi(req.params["id"])` → `database.getInvoice(id)`
handler with no ownership check. Every model instead reported the uncaught
`std::stoi` exception (a real defect). The C++ snippet lacked the
auth-context cue present in the Python and Rust versions
(`user=Depends(current_user)` / `user: User`), so nothing signaled "this
endpoint is authenticated but not authorized." A 0/6 sweep indicated an
under-specified case, not a shared blind spot — on JavaScript, 5/6 models
caught the same IDOR without an explicit user param because the Express
`:id` pattern is iconic.

**Fix + targeted re-run.** The handler now takes `const User& user` — an
authenticated context that is never consulted. Only the six `security-08`
rows were re-run (resume keys preserved everything else). Results:

| Model | Fixed-case score | What happened |
|---|---:|---|
| Ornith, Gemma, Qwen3.6 | 100 | Caught both IDOR + missing object authorization |
| Tiel | 0 | Reported `std::stoi` trailing-char parsing — a real but different defect |
| Devstral | 0 | Listed CWE-190/CWE-754 parsing issues — never reached authorization |
| Bonsai | 0 | Truncated at 1024 again — reasoning consumed the budget before findings |

The corrected case now discriminates: 3/6 catch the IDOR, and the misses are
instructive — Tiel and Devstral fixated on input parsing when two defects
coexist, and Bonsai's verbosity remains its binding constraint. Leaderboard
and diagnostics tables above reflect the fixed scores.

## Caveats

- Cross-language absolute scores are not a shared scale — compare rankings.
- Bonsai runs `reasoning_effort=medium`; Devstral is non-thinking — the
  field is not thinking-normalized, and truncation stats reflect that.
- Judge `partial` verdicts count as matches; precision is judge-lenient.
