# oMLX Review Benchmark — JavaScript — 2026-09-20

Run: `--lang=javascript --presets --judge yes`, thinking enabled (budget 256),
`max_tokens=1024`, LLM judge on the remote llama.cpp router. 120 cases
(6 models × 20), 0 failures. Cases: `test_definitions/javascript.json`.

| Model | Overall | Quality | Security | PP tok/s | Out tok/s |
|---|---:|---:|---:|---:|---:|
| Qwen3.6-35B-Claude-Distilled-MLX-oQ4-MTP | 91.56 | 87.35 | 95.78 | 402.9 | 77.8 |
| Tiel-Coder-35B-A3B-MLX-oQ4e-MTP | 90.87 | 83.17 | 98.57 | 459.5 | 65.9 |
| gemma-4-26B-A4B-it-QAT-MLX-4bit | 86.01 | 76.02 | 96.00 | 425.7 | 63.1 |
| Devstral-Small-2-24B:devstral-code | 82.74 | 73.97 | 91.52 | 95.1 | 11.7 |
| Ornith-1.5-35B-A3B-MLX-4bit | 80.70 | 66.74 | 94.67 | 324.7 | 77.4 |
| Ternary-Bonsai-2-27B:bonsai2-coder | 64.33 | 64.67 | 64.00 | 88.3 | 22.9 |

## Cross-language comparison (vs Python run)

| Model | Python | JavaScript | Delta |
|---|---:|---:|---:|
| Qwen3.6 | 81.81 | 91.56 | **+9.75** |
| Tiel | 90.27 | 90.87 | +0.60 |
| Gemma | 81.17 | 86.01 | +4.84 |
| Devstral | 63.27 | 82.74 | **+19.47** |
| Ornith | 82.44 | 80.70 | -1.74 |
| Bonsai | 76.17 | 64.33 | **-11.84** |

## Per-model diagnostics

| Model | Recall | Precision | Unsup/case | Truncated | Clean JSON | Think channel |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3.6 | 90.4 | 96.0 | 0.2 | 1/20 | 0/20 | 20/20 |
| Tiel | 88.8 | 96.8 | 0.15 | 1/20 | 12/20 | 20/20 |
| Gemma | 78.8 | 98.3 | 0.05 | 2/20 | 17/20 | 20/20 |
| Devstral | 84.2 | 84.8 | 0.9 | 0/20 | 15/20 | 0/20 |
| Ornith | 74.6 | 99.0 | 0.05 | 7/20 | 8/20 | 20/20 |
| Bonsai | 57.5 | 95.0 | 0.05 | 19/20 | 1/20 | 20/20 |

## What changed vs Python

- **Qwen3.6 → #1** (91.56): best recall in the field (90.4) — the reasoning
  distill found nearly every expected finding. Still never emits bare JSON
  (0/20), tolerated by `parse_findings`.
- **Devstral +19.5 — the biggest mover.** On Python it was last with an
  honest capability gap; on JavaScript it produced 15/20 clean JSON, zero
  truncation, recall 84.2 — better than Ornith and Gemma. Two plausible
  drivers: Devstral is a code-specialist instruct model likely strongest on
  mainstream languages, and it is the only non-thinking entry — its entire
  1024-token budget goes to findings while others spend it on reasoning.
  Its precision (84.8, ~0.9 unsupported/case) is still the weakest.
- **Bonsai -11.8 — the fix held but JS hurt it.** The think channel works
  (20/20 `reasoning_content`), precision 95.0, but recall collapsed to 57.5
  and security dropped to 64.0. Still 19/20 truncated: medium-effort
  reasoning plus verbose preamble eats the 1024 budget before findings
  finish. On Python the judge salvaged more; on JS the truncation cost is
  visible. This is now an output-budget problem, not a channel problem.
- **Ornith steady-precision, low recall**: 99.0 precision again, but recall
  74.6 — conservative on both languages. 7/20 truncated.

## Caveats

- Cross-language scores are **not** a shared scale — different case files,
  loosely parallel difficulty. Compare rankings, not absolute numbers.
- Bonsai runs `reasoning_effort=medium` (channel-split fix); others run
  their template defaults — its row is best-tuned, not stock.
- Devstral's `think=0/20` is expected — it is a non-reasoning instruct
  model; the field is not thinking-normalized.
- Judge verdicts (`yes`/`partial`/`no`) treat `partial` as a match —
  precision numbers are judge-lenient.
