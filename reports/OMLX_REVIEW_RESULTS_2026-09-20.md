# oMLX Review Benchmark — 2026-09-20

Run: `--presets --judge yes`, thinking enabled (budget 256), `max_tokens=1024`,
judge `critic-ornith:LATEST` on Galileo. 120 cases (6 models × 20), 0 failures.

| Model | Overall | Quality | Security | PP tok/s | Out tok/s |
|---|---:|---:|---:|---:|---:|
| Tiel-Coder-35B-A3B-MLX-oQ4e-MTP | 90.27 | 82.76 | 97.78 | 464.0 | 62.7 |
| Ornith-1.5-35B-A3B-MLX-4bit | 82.44 | 68.00 | 96.89 | 425.3 | 79.2 |
| Qwen3.6-35B-Claude-Distilled-MLX-oQ4-MTP | 81.81 | 70.67 | 92.96 | 402.0 | 76.1 |
| gemma-4-26B-A4B-it-QAT-MLX-4bit | 81.17 | 71.67 | 90.67 | 397.0 | 56.0 |
| Devstral-Small-2-24B:devstral-code | 63.27 | 63.74 | 62.80 | 298.9 | 12.3 |
| Ternary-Bonsai-2-27B:bonsai2-coder | 62.00 | 60.00 | 64.00 | 89.7 | 20.0 |

## Why the top four scored well

| Model | Recall | Precision | Unsup/case | Truncated | Clean JSON |
|---|---:|---:|---:|---:|---:|
| Tiel | 88.3 | 95.5 | 0.2 | 1/20 | 9/20 |
| Ornith | 76.7 | 99.0 | 0.1 | 8/20 | 5/20 |
| Qwen3.6 | 77.9 | 94.1 | 0.2 | 2/20 | 0/20 |
| Gemma | 71.7 | 100.0 | 0.0 | 0/20 | 19/20 |

The differentiator is **recall on quality cases**, not security. Security cases
have few, obvious findings (SQLi, Marshal.load, static salt) — all four scored
90–98 there. Quality cases carry 3–4 subtler expected findings (shallow copy,
ambiguous sentinel, incorrect cache key, missing jitter); the winners still
missed ~1 per case, just less often:

- **Tiel** (90.3): broadest recall (88.3) with near-clean precision. Thinking
  produced the most exhaustive finding lists; misses were spread thin
  (dependency injection, cache key, durability).
- **Ornith** (82.4): highest precision (99.0) — conservative, reports only what
  it can defend — but thin recall (76.7) and verbose: 8/20 cases hit the 1024
  cap. Quality score (68.0) is its weak axis; more `max_tokens` headroom would
  likely help it most.
- **Qwen3.6** (81.8): reasoning-distilled; never emits bare-JSON output (0/20
  start with `[` — preamble/fences), tolerated by `parse_findings`. Solid
  recall, slight precision cost.
- **Gemma** (81.2): most disciplined emitter (19/20 pure JSON, precision 100,
  zero unsupported findings) but the most conservative recall — it reports
  fewer defects, which caps the score despite perfect reliability.

## Why Devstral and Bonsai scored low — two different failure modes

### Devstral (63.3): honest capability gap

Structurally clean — 16/20 valid JSON, zero truncation, no CoT leak. The deficit
is review quality itself: recall 65.0 (lists ~2 of 3–4 expected findings) and
the **only** precision problem in the field — 72.3, with ~1.6 unsupported
findings per case (e.g. speculative "add bounds checks on quantity" findings the
judge rejected). A 24B non-reasoning instruct model: finds the obvious defect,
pads the list with marginal issues. This number is trustworthy.

### Bonsai (62.0): broken generation path, inflated score

19/20 responses hit the 1024 cap, 19/20 leaked raw chain-of-thought into
`content` ("We need answer user's request. Need produce final JSON array
only..."), and only 1/20 emitted a JSON array at all. In `security-06` it
spiraled into a hallucinated CWE catalog — `CWE-364/365/366/367` all labeled
"Use of Hard-coded Cryptographic Key" — until truncation.

**Post-run diagnosis (verified live):** Bonsai's chat template leaks reasoning
into `content` under its default `xhigh` reasoning effort (and under `low`) —
the think channel never opens, `reasoning_content` stays empty, and the model
rambles to the token cap. `reasoning_effort=medium` is the only level verified
to emit a proper `reasoning_content` channel plus clean JSON `content`
(reproducible 2/2). This is a model/template quirk, not a profile bug — the
bare model ID behaves identically, and thinking budget size is irrelevant.
Fix shipped: `reasoning-effort = medium` in `presets-omlx.ini` for the
`bonsai2-coder` alias — thinking stays **on**. (`OMLX_NO_THINKING_MODELS`
remains available as an escape hatch for models that can't be fixed this way.)

**Its 62.0 is overstated**: unconstrained output falls into the blob-fallback,
and the judge salvaged real findings from the leaked reasoning text. As a
measurement of *usable structured output*, Bonsai effectively failed the suite.
A rerun with the medium-effort preset will produce the honest number.

## Caveats

- `benchmark_parameters.response_format` still says "strict JSON schema" but
  thinking mode dropped the schema — the label is a stale hardcoded string.
- Precision under thinking mode is slightly optimistic for all models
  (blob-fallback salvage); the judge mitigates but doesn't eliminate it.
- Throughput: profile aliases ran slowest (Devstral 12.3, Bonsai 20.0 out
  tok/s) — server-side profile sampling adds overhead vs. the bare models'
  56–79 tok/s.
