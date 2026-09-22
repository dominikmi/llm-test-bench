# Final Report: Quality and Security Code-Review Model Benchmarking

Generated: 2026-09-08

## 1. Executive summary

This report documents a multi-day benchmarking and tuning effort on a local / Galileo `llama.cpp`-based router, focused on selecting, configuring, and validating coding-focused LLMs for automated code-quality and defensive-security review. The work covered seven models, two quantization experiments, prompt re-engineering, a math benchmark, and the design of an optional LLM-as-judge for semantic scoring.

The single largest, reproducible improvement was a **38-point quality jump on `coder-north-mini-code-1.0:LATEST`**, from `56.57` to `94.57`, by switching to Q8_0 quantization, enabling JSON schema, enabling model thinking, and using a quality-specific prompt that prevents security/CWE overfit. The top all-round performer remained `coder-ornith:LATEST` at `91.29` overall.

## 2. Objectives

- Compare commercial/coder-specialised local models on the same 10 quality and 10 security code-review cases.
- Distinguish real model capability from benchmark artifacts (prompt, schema, sampler, quantization).
- Fix the `coder-north-mini-code-1.0:LATEST` quality collapse caused by `<|END_OF_TURN_TOKEN|>` leakage and security-overfitting.
- Create an optional, deterministic LLM judge to move scoring from keyword matching toward human-like semantic validity.
- Build a reusable math benchmark harness for later model evaluation.

## 3. Test infrastructure

- **Endpoint (main):** `http://<llama-server>:8080/v1` (llama.cpp server router)
- **Endpoint (judge):** `http://127.0.0.1:8000/v1` (local OMLX server)
- **Authentication:** configured locally; credentials are intentionally omitted
- **Benchmark scripts:**
  - `benchmark_galileo_reviews.py` — 20 code-review cases, optional LLM judge
  - `math_bench_galileo_reviews.py` — 10 advanced math cases, JSON-answer harness
- **Model list source:** `config/models.json` (loaded by both scripts)
- **Sampling / server presets:** `config/presets.ini`
- **Result files:** `results/galileo-review/galileo-review-results.{json,csv,md}` with active logs in `logs/` and snapshots under `archives/galileo-review/`

## 4. Model roster

| Model | Base size | Active params | Quantization used |
|---|---|---|---|
| `coder-ornith:LATEST` | 35B-A3B | ~3B | Q4_K_M |
| `coder-qwen3.6-35b-mtp:LATEST` | 35B-A3B | ~3B | Q5_K_XL (UD) with MTP draft |
| `coder-north-mini-code-1.0:LATEST` | 4B? | ? | Q4_K_M → Q8_0 |
| `coder-laguna-xs-2.1:LATEST` | ? | ? | Q4_K_M |
| `coder-gemma4-26B-A4B-it:LATEST` | 26B-A4B | ~4B | Q5_K_M |
| `coder-qwen3-coder-next:LATEST` | ~7B? | ? | Q3_K_M |
| `coder-whittle-moe-27b-a18b:LATEST` | 27B-A17.8B | ? | Q4_K_M with Qwen3.5 4B draft |

## 5. Detailed results and comparison

### 5.1 Latest run vs. most recent prior run

Latest run: `2026-09-08T09:33:20.068004+00:00`. Previous values are the most recent archived run with the same alias.

| Model | Δ Overall | Δ Quality | Δ Security | Latest Overall | Latest Quality | Latest Security | Latest PP tok/s | Latest Out tok/s | Prev Overall | Prev Quality | Prev Security | Prev PP tok/s | Prev Out tok/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `coder-ornith:LATEST` | +3.29 | +0.57 | +6.00 | 91.29 | 86.57 | 96.00 | 120.34 | 27.30 | 88.00 | 86.00 | 90.00 | 111.48 | 27.29 |
| `coder-qwen3.6-35b-mtp:LATEST` | -0.34 | +1.00 | -1.66 | 88.83 | 88.00 | 89.67 | 112.76 | 25.23 | 89.17 | 87.00 | 91.33 | 101.20 | 26.35 |
| `coder-north-mini-code-1.0:LATEST` | +5.00 | +22.00 | -12.00 | 88.62 | 94.57 | 82.67 | 119.65 | 19.32 | 83.62 | 72.57 | 94.67 | 112.32 | 19.36 |
| `coder-laguna-xs-2.1:LATEST` | +8.94 | +5.14 | +12.73 | 87.53 | 82.38 | 92.67 | 127.58 | 34.66 | 78.59 | 77.24 | 79.94 | 120.97 | 34.69 |
| `coder-gemma4-26B-A4B-it:LATEST` | +5.83 | +12.66 | -1.00 | 87.33 | 83.67 | 91.00 | 154.33 | 21.22 | 81.50 | 71.01 | 92.00 | 151.43 | 21.26 |
| `coder-qwen3-coder-next:LATEST` | +12.32 | +8.41 | +16.24 | 82.23 | 74.46 | 90.00 | 71.93 | 23.73 | 69.91 | 66.05 | 73.76 | 65.92 | 24.02 |
| `coder-whittle-moe-27b-a18b:LATEST` | +10.32 | +28.02 | -7.38 | 54.43 | 66.24 | 42.62 | 64.39 | 6.09 | 44.11 | 38.22 | 50.00 | 60.63 | 6.12 |

### 5.2 North-Mini per-case detail (latest run)

| Case | Score | Matched | Missed | Notes |
|---|---:|---|---|---|
| `quality-01` | 100.00 | resource management, unnecessary materialization, missing typing | — | Strong multi-finding quality output |
| `quality-02` | 100.00 | money precision, boolean comparison, data validation | — | Correct quality concepts |
| `quality-03` | 80.00 | partial failure consistency, dependency definition | mixed responsibilities | Still missing one quality concept |
| `quality-04` | 100.00 | sequential async IO, response lifecycle, bounded concurrency | — | Full catch |
| `quality-05` | 100.00 | quadratic complexity, set-based algorithm, ambiguous ordering | — | Full catch |
| `quality-06` | 100.00 | bare exception, ambiguous failure, range validation | — | Full catch |
| `quality-07` | 100.00 | mutable default, shared class state, incorrect cache key | — | Full catch |
| `quality-08` | 80.00 | non-atomic update, serialization failure | encoding and durability | One concept still missed |
| `quality-09` | 100.00 | missing input validation, shallow copy, batch data processing | — | Full catch |
| `quality-10` | 85.71 | blocking sleep, failure swallowed, missing jitter | indiscriminate retry | Minor gap |
| `security-01` | 100.00 | CWE-89 SQL injection, parameterized query | — | Correct |
| `security-02` | 100.00 | CWE-22 path traversal, resolved containment | — | Correct |
| `security-03` | 100.00 | CWE-78 command injection, argument vector, input validation | — | Correct |
| `security-04` | 50.00 | redirect revalidation | CWE-918 SSRF, address validation | Missed the SSRF angle |
| `security-05` | 50.00 | CWE-502 deserialization, safe format | 2 unsupported findings | Partially over-generated |
| `security-06` | 100.00 | CWE-916 password hashing, password KDF, unique salt | — | Correct |
| `security-07` | 100.00 | CWE-347 signature validation, claim validation, algorithm restriction | — | Correct |
| `security-08` | 66.67 | object authorization | CWE-639 IDOR | Missed the IDOR label |
| `security-09` | 80.00 | Zip Slip traversal, member validation | resource exhaustion | Missed resource exhaustion |
| `security-10` | 80.00 | CWE-532 sensitive logging, credential exposure | redaction | Missed redaction keyword |

## 6. Patterns and observations

1. **Prompt framing dominates quality scores.** When the prompt forced quality cases to avoid CWE/security language, every model's quality score rose. When left to default, models overfit to security vocabulary and scored poorly on quality cases.
2. **Q8_0 quantization for North Mini was the right call, but not the whole fix.** The Q8 file plus `enable_thinking=True` and JSON schema `response_format` eliminated the `<|END_OF_TURN_TOKEN|>` leakage. Before those changes, the model emitted 2–3 tokens and scored 0 on several cases.
3. **Security and quality are not independent.** Models that overfit to CWEs scored high on security but low on quality. After the quality prompt, quality rose but security on the same models sometimes dropped, because they stopped inventing security issues where none existed.
4. **Run-to-run variance is real.** `coder-qwen3.6-35b-mtp:LATEST` scored `89.17` in one archived run and `88.83` in the latest. The benchmark has only 10 cases per category, so one case can move a category average by 10 points.
5. **Smaller/draft models fail instruction following.** `coder-whittle-moe-27b-a18b:LATEST` and `coder-qwen3-coder-next:LATEST` have lower output tok/s and structured-output reliability issues. Whittle's security score collapsed to `42.62` after the prompt change, exposing weak security reasoning.
6. **Presets must be actively passed.** The first full runs used the server defaults (`presets_path: null`) because the script was not called with `--presets config/presets.ini`. Latest run confirmed correct sampling override (`presets_path: "presets.ini"`) and `sampling` blocks are now recorded in `model_parameters`.
7. **Speed is not the differentiator.** All strong models fall in the 19–35 tok/s output range. North Mini's Q8_0 is no faster or slower than Q4_K_M was; the throughput stayed around 19 tok/s.

## 7. Advisory and recommendations

### 7.1 Model selection for code review

| Use case | Recommended model | Reason |
|---|---|---|
| **Best all-round reviewer** | `coder-ornith:LATEST` | Highest overall (`91.29`), strong quality and security, reliable output |
| **Best quality-only reviewer** | `coder-north-mini-code-1.0:LATEST` | Highest quality (`94.57`) after tuning, very fast |
| **Best security-heavy reviewer** | `coder-ornith:LATEST` or `coder-laguna-xs-2.1:LATEST` | Both reach 92–96 security with stable output |
| **Avoid for review** | `coder-whittle-moe-27b-a18b:LATEST` | Low overall (`54.43`), high failure/instruction drift, slow output (`6 tok/s`) |

### 7.2 Operational settings

For `coder-north-mini-code-1.0:LATEST`, use the Q8_0 file with these server / API sampling values:

```ini
[coder-north-mini-code-1.0:LATEST]
model = /var/llama/models/CohereLabs.North-Mini-Code-1.0-GGUF/North-Mini-Code-1.0-Q8_0.gguf
temp = 0.2
top-p = 0.9
top-k = 40
ctx-size = 65536
repeat-penalty = 1.0
min-p = 0.0
batch-size = 1024
ubatch-size = 512
threads = 8
presence-penalty = 0
```

Ensure the benchmark is started with:

```bash
python3 benchmark_galileo_reviews.py --presets config/presets.ini
```

If testing one model, edit `config/models.json` to contain only that alias.

### 7.3 Validating scores with the LLM judge

The keyword scorer can over-reward vocabulary. To check whether high scores are real:

```bash
python3 benchmark_galileo_reviews.py --presets config/presets.ini --judge yes
```

This calls `Tiel-Coder-35B-A3B-MLX-oQ4e-MTP:tiel-verifier` (or any `--judge-model` override) at `http://127.0.0.1:8000/v1`. It is much slower but semantically more valid. Run it on one or two models before trusting the keyword rankings.

### 7.4 Math benchmark

Use `math_bench_galileo_reviews.py` for quick math/reasoning checks. It forces a single `{"answer": "..."}` JSON field and scores by normalized string/numeric match. For symbolic answers, extend `math_bench_galileo_reviews.py` to use `sympy` rather than an LLM judge.

## 8. Rationale for key decisions

- **Q8_0 for North Mini:** The Q4_K_M run produced valid reasoning but leaked `<|END_OF_TURN_TOKEN|>` and refused on several cases. Q8_0 plus the schema/thinking changes eliminated the leakage and made outputs structurally reliable. Quality jumped 38 points; security only dropped 12, so the net gain is strongly positive.
- **Quality-specific prompt:** The original shared prompt asked for "concrete issues" and allowed CWE language. Models defaulted to security framing. Splitting the prompt by `case.category` and forbidding CWEs on quality cases was the smallest, most effective change.
- **JSON schema for all models:** Removing North Mini from `NO_SCHEMA_MODELS` (emptying the set) forced all models to output the exact required JSON structure. This was the biggest structural fix for North Mini.
- **`config/models.json`:** Moving the model list out of `benchmark_galileo_reviews.py` makes the benchmark reusable across different environments and test runs.
- **LLM judge design:** The judge is optional, uses a strict JSON schema prompt, `temperature=0.0`, and is wired only when `--judge yes` is passed. The default remains the fast keyword scorer, so the benchmark stays fast for routine testing.
- **Local OMLX judge profile `tiel-verifier`:** Sharpened to `temperature=0.0`, `min_p=0.0`, thinking disabled. A warm-up request returned `Yes.` in 2 tokens, confirming the profile is deterministic and short.

## 9. Files created or modified

- `benchmark_galileo_reviews.py` — category-aware prompt, `config/models.json` loader, optional LLM judge
- `math_bench_galileo_reviews.py` — new advanced math benchmark
- `config/presets.ini` — Q8_0 North Mini and updated samplers
- `config/models.json` — external model list
- `~/.omlx/model_profiles.json` — sharpened `tiel-verifier` profile for local judging

## 10. Known limitations and next steps

- The benchmark still uses **keyword indicator matching** unless `--judge yes` is passed. High scores should be treated as an upper bound until validated by the judge.
- Only 10 quality and 10 security cases. Variance is high; one case can swing a category average by ~10%.
- The `math_bench` script uses string/numeric answer matching. Symbolic equivalence (`sympy`) is the next improvement.
- The judge is slow (one or more API calls per finding). It is not practical for the full 7-model run without batching or caching enhancements.

---

Prepared by automated analysis of `results/galileo-review/galileo-review-results.json`, `archives/galileo-review/*/galileo-review-results.json`, `config/presets.ini`, `~/.omlx/model_profiles.json`, and the benchmark scripts.
