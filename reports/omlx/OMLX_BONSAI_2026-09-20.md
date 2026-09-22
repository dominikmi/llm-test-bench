# Ternary-Bonsai-2-27B Deep Dive — 2026-09-20

Dedicated analysis of `Ternary-Bonsai-2-27B-MLX-4bit:bonsai2-coder`, the
hardest model in the oMLX field to benchmark correctly. It required three
separate fixes to produce honest numbers; this report documents each failure
mode, the fix, and the final scores. Referenced from the Python, JavaScript,
and C++ leaderboard reports.

## The model

pipenetwork MLX build of `prism-ml/Ternary-Bonsai-2-27B` — a ternarized
Qwen3.8-27B. `Qwen3_5ForConditionalGeneration` VLM: 64 layers, hybrid
Gated-DeltaNet linear attention with full attention every 4th layer, 4-bit
affine quantization (16.1 GB), 256k context, ships an MTP speculation head
(`mtp.safetensors`) alongside a separate DFlash2 draft model.

## Three problems, three fixes

### 1. Reasoning leaked into `content` (channel never opened)

Under the template's default `xhigh` reasoning effort (and `low`), the think
channel never opened on benchmark prompts: `reasoning_content` stayed empty
while `content` filled with raw chain-of-thought ("We need answer user's
request..."), degenerating into hallucinated CWE catalogs until the token
cap. Only 1/20 responses produced a JSON array.

**Fix:** `reasoning-effort = medium` in `config/presets-omlx.ini` — the only
effort level verified to split channels correctly (empirical, reproducible;
root cause inside the distill not understood).

### 2. Truncation — output budget too small

Even with the channel fixed, medium-effort Bonsai is verbose: thinking plus a
"Let me analyze..." preamble consumed the global 1024-token cap before the
findings array. C++ was worst: 20/20 responses truncated.

**Fix:** `max-tokens = 4096` in the same preset section (per-model override;
other models stay at 1024). Bonsai needs ~2100–2700 tokens mean to think and
answer.

### 3. Suspected eos mismatch — investigated, not a bug

`config.json` declares `eos_token_id: 248044` (`<|endoftext|>`) while
`generation_config.json` declares `248046` (`<|im_end|>`). Verified harmless:
generation stops correctly on `<|im_end|>`; the split is the standard
base-vs-chat eos convention. No fix needed.

## Speculation: measured, no free lunch

| Path | Out tok/s | Notes |
|---|---:|---|
| DFlash2 draft (profile default) | ~15–17 | engages correctly, 52–75% acceptance — but the draft runs at ~target speed, so net gain ≈ 0 |
| Built-in MTP head | ~13.9 | slower; also conflicts with TurboQuant KV (`vlm_mtp_enabled` required for this VLM) |
| Neither | ~15 | — |

~20 out tok/s is dense-27B reality on this hardware; the MoE leaders
(3–4B active params) hit 56–79 by architecture, not tuning. Config left at
profile defaults (DFlash on, TurboQuant KV on, MTP off).

## Score progression

| Language | Broken (xhigh + 1024) | Fixed effort | + 4096 budget | Field rank |
|---|---:|---:|---:|---|
| Python | 62.00 | 76.17 | **85.33** | #2 of 6 |
| JavaScript | 64.33 | — | **74.83** | #6 of 6 |
| C++ | 65.33 | — | **81.00** | #3 of 6 |

## Final diagnostics (4096 budget)

| Language | Recall | Precision | Unsup | Truncated | Clean JSON | Mean out tok |
|---|---:|---:|---:|---:|---:|---:|
| Python | 77.5 | 100.0 | 0 | 1/20 | 19/20 | 2123 |
| JavaScript | 67.5 | 100.0 | 0 | 1/20 | 19/20 | 2403 |
| C++ | 73.3 | 100.0 | 0 | 2/20 | 18/20 | 2698 |

Precision 100.0 with zero unsupported findings on all three languages — when
it can finish writing, Bonsai is the most disciplined emitter in the field.
It also scored 100 on the corrected C++ `security-08` IDOR case (3695 of 4096
tokens used — it needed nearly all the headroom).

## Disclaimers

- Bonsai's rows are **not like-for-like** with the rest of the field: it runs
  `reasoning_effort=medium` (others use template defaults) and a 4096-token
  output budget (others: 1024). Its numbers answer "what can Bonsai do when
  configured correctly," not "Bonsai under identical constraints."
- 1–2 cases per language still hit the 4096 cap; `max-tokens` beyond that is
  unexplored.
- `reasoning_effort=medium` is an empirical fix — reproducible but not
  root-caused; a different oMLX/mlx-vlm version could change the behavior.
