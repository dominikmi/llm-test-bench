# oMLX Review Benchmark — C++ — 2026-09-20

Run: `--lang=cpp --presets --judge yes`, thinking enabled (budget 256),
`max_tokens=1024`, LLM judge on the remote llama.cpp router. 120 cases
(6 models × 20), 0 failures. Cases: `test_definitions/cpp.json`.

| Model | Overall | Quality | Security | PP tok/s | Out tok/s |
|---|---:|---:|---:|---:|---:|
| gemma-4-26B-A4B-it-QAT-MLX-4bit | 81.83 | 79.67 | 84.00 | 432.5 | 62.1 |
| Tiel-Coder-35B-A3B-MLX-oQ4e-MTP | 80.27 | 77.88 | 82.67 | 477.9 | 65.6 |
| Qwen3.6-35B-Claude-Distilled-MLX-oQ4-MTP | 76.25 | 70.93 | 81.57 | 420.5 | 76.3 |
| Ornith-1.5-35B-A3B-MLX-4bit | 67.94 | 58.00 | 77.89 | 420.5 | 79.1 |
| Ternary-Bonsai-2-27B:bonsai2-coder | 65.33 | 68.00 | 62.67 | 85.2 | 20.3 |
| Devstral-Small-2-24B:devstral-code | 61.01 | 60.30 | 61.72 | 99.9 | 12.5 |

## Three-language comparison

| Model | Python | JavaScript | C++ | Spread |
|---|---:|---:|---:|---:|
| Tiel | 90.27 | 90.87 | 80.27 | 10.6 |
| Qwen3.6 | 81.81 | 91.56 | 76.25 | 15.3 |
| Gemma | 81.17 | 86.01 | 81.83 | **4.8** |
| Ornith | 82.44 | 80.70 | 67.94 | 14.5 |
| Bonsai | 76.17 | 64.33 | 65.33 | 11.8 |
| Devstral | 63.27 | 82.74 | 61.01 | 21.7 |

Gemma is the most *consistent* model across languages (spread 4.8) and takes
the C++ crown — the only one above 80. C++ compressed the whole field: every
model scored lower than on JavaScript, and quality recall dropped across the
board.

## Per-model diagnostics

| Model | Recall (q/s) | Precision | Unsup | Truncated | Clean JSON | Think |
|---|---:|---:|---:|---:|---:|---:|
| Gemma | 76.7 (73/80) | 96.7 | 2 | 1/20 | 18/20 | 20/20 |
| Tiel | 83.3 (83/83) | 88.0 | 12 | 1/20 | 7/20 | 20/20 |
| Qwen3.6 | 75.0 (70/80) | 89.5 | 10 | 0/20 | 0/20 | 20/20 |
| Ornith | 60.0 (43/77) | 97.3 | 2 | 9/20 | 6/20 | 20/20 |
| Bonsai | 54.2 (53/55) | 100.0 | 0 | 20/20 | 0/20 | 20/20 |
| Devstral | 68.3 (63/73) | 67.5 | 36 | 0/20 | 20/20 | 0/20 |

## What the C++ run shows

- **Gemma's discipline pays off on the hardest language**: 18/20 clean JSON,
  1/20 truncated, precision 96.7 — it does not find the most (recall 76.7,
  second to Tiel's 83.3) but wastes almost nothing. On the language where
  verbosity is most punished, economy wins.
- **Tiel stays recall leader** (83.3, balanced q/s) but its precision slipped
  to 88.0 — 12 unsupported findings, mostly speculative C++-isms the judge
  rejected.
- **Qwen3.6 fell from #1 to #3**: still zero truncations and real thinking,
  but 10 unsupported findings and quality recall dropped to 70.
- **Ornith's recall collapsed on quality** (43.3) — the conservative reviewer
  finds too little when cases get idiomatic (RAII, `std::` specifics).
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

## Case-quality flag: security-08 (IDOR) — corrected post-run

All six models scored **0.0** on `security-08` — a `std::stoi(req.params["id"])`
→ `database.getInvoice(id)` handler with no ownership check. Every model
instead reported the uncaught `std::stoi` exception (a real defect). The C++
snippet lacked the auth-context cue present in the Python and Rust versions
(`user=Depends(current_user)` / `user: User`), so nothing signaled "this
endpoint is authenticated but not authorized." A 0/6 sweep indicated an
under-specified case, not a shared blind spot — on JavaScript, 5/6 models
caught the same IDOR without an explicit user param because the Express
`:id` pattern is iconic.

**Fix applied post-run** (`test_definitions/cpp.json`): the handler now takes
`const User& user` — an authenticated context that is never consulted, making
the missing authorization check discoverable. Scores above reflect the
*unfixed* case; a C++ rerun would lift every model's security number slightly
(up to ~5 points of the security axis).

## Caveats

- Cross-language absolute scores are not a shared scale — compare rankings.
- Bonsai runs `reasoning_effort=medium`; Devstral is non-thinking — the
  field is not thinking-normalized, and truncation stats reflect that.
- Judge `partial` verdicts count as matches; precision is judge-lenient.
