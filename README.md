# Model Testing Suite

Benchmarks for comparing LLMs on code review, agentic tool use, logic, and math,
across two serving stacks: **Galileo** (remote llama.cpp server at
`<llama-server>:8080/v1`) and **oMLX** (local MLX server on macOS at
`127.0.0.1:8000/v1`).

## Getting started

Requires macOS, Python 3.11+, and [uv](https://docs.astral.sh/uv/). No model
servers are needed for setup, checks, or audits — live servers are only
required when actually running a bench:

```bash
make install   # verify toolchain + create .venv via uv sync (runtime + dev deps)
make check     # offline unit tests + ruff + mypy (no live servers required)
make audit     # uv audit (dependency CVEs) + bandit source scan
```

`make install` installs pinned dev tools (ruff, mypy, bandit) alongside the
runtime dependency (pydantic). Live benches still need the model list and
sampling presets under `config/` plus the `*_BASE_URL`/`*_API_KEY` env vars
described below.

## One entry point: `bin/bench.py`

All runners are dispatched through a single orchestrator. Benches are named
`<backend>-<what>`; everything after the bench name is forwarded verbatim to
the runner's own argparse. `--backend`/`--suite` defaults are injected per
bench name but never override flags you pass explicitly:

```bash
bin/bench.py <bench> [runner args...]
```

| Bench | Runner module | Target | What it measures |
|---|---|---|---|
| `omlx-review` | `modules/benchmark_omlx_reviews.py` | oMLX | Quality+security review (20 cases/lang), judge optional |
| `galileo-review` | `modules/benchmark_galileo_reviews.py` | Galileo | Same review cases on router-hosted models |
| `omlx-tools` | `modules/benchmark_agent_tools.py` | oMLX | Tool-calling discipline: plan adherence, schema validity, injection resistance |
| `omlx-logic` | `modules/benchmark_agent_tools.py` | oMLX | 21 deterministic reasoning cases, typed-answer grading |
| `omlx-agent` | `modules/benchmark_agent_tools.py` | oMLX | Both agentic suites; pass `--suite` explicitly |
| `galileo-tools` | `modules/benchmark_agent_tools.py` | Galileo | Tool-use suite on router-hosted models |
| `galileo-logic` | `modules/benchmark_agent_tools.py` | Galileo | Logic suite on router-hosted models |
| `galileo-agent` | `modules/benchmark_agent_tools.py` | Galileo | Both agentic suites on Galileo; pass `--suite` explicitly |
| `galileo-pipeline` | `modules/benchmark_opencode_agents.py` | Galileo via OpenCode | Full agentic pipeline: review quality + MCP tool compliance (Serena + Headroom) |
| `omlx-pipeline` | `modules/benchmark_opencode_agents.py` | oMLX via OpenCode | Same agentic pipeline against the `omlx` provider |
| `galileo-math` | `modules/math_bench_galileo_reviews.py` | Galileo | 10 applied math problems, exact/numeric answer match |
| `omlx-math` | `modules/math_bench_galileo_reviews.py` | oMLX | Same math problems on oMLX models |

```bash
bin/bench.py omlx-review --lang python --judge yes --presets config/presets-omlx-coder.ini
bin/bench.py omlx-tools --presets config/presets-omlx-agent.ini
bin/bench.py galileo-pipeline --lang python
bin/bench.py omlx-math --presets config/presets-omlx-coder.ini
```

Utilities not exposed via `bench.py` (no `main(argv)` contract or one-off
probes): `modules/judge_ab_test.py` (A/B two judge profiles on one subject),
`tests/test_galileo_models.py` (live smoke test).

Every runner module also remains directly executable from the repo root —
`python3 -m modules.benchmark_omlx_reviews ...` is equivalent to
`bin/bench.py omlx-review ...` (minus the injected `--backend`/`--suite`
defaults).

## The review benchmark (shared by three runners)

### Cases

20 fixed cases per language: 10 `quality` + 10 `security`. Each case is a short
code snippet with expected findings — a name plus accepted textual indicators.
Security cases map to CWEs (SQLi, path traversal, command injection, SSRF,
insecure deserialization, weak password hashing, JWT misuse, IDOR, Zip Slip,
sensitive logging). Quality cases cover resource leaks, float money, O(n²)
algorithms, swallowed exceptions, shared mutable state, non-atomic writes,
retry storms, and similar.

Cases are static JSON in `test_definitions/<lang>.json` (python, javascript,
rust, cpp, ruby). `test_definitions/python.json` is generated from the Galileo
script's in-code `CASES` — a unit test pins them equal, so if you edit `CASES`,
regenerate `python.json` or the test fails.

Schema (validated with pydantic, unknown keys rejected):

```json
{"language": "rust", "cases": [{"case_id": "quality-01",
  "category": "quality", "code": "...", "findings":
  [{"name": "panic on error", "indicators": ["unwrap", "result"]}]}]}
```

`language` is file-level with optional per-case override; `category` must be
`quality` or `security`; `findings` needs ≥1 entry.

### Scoring

`score_response` parses findings from the model's JSON response and computes
recall / precision / F1:

- **Keyword scorer** (default): an expected finding is *matched* if any of its
  indicator strings appears in a response finding; a response finding is
  *supported* if it contains any expected indicator. Cheap, deterministic, and
  gameable — a finding that merely names the right keyword counts as supported.
- **LLM judge** (`--judge yes`): per-finding `yes`/`partial`/`no` verdicts.
  `partial` counts as a match. Precision becomes the fraction of response
  findings the judge validates as real defects — much stricter than keywords.

The judge prompt is hardened: it instructs the evaluator to treat code and
candidate text as untrusted data, never as instructions — the model under test
could embed prompt injection in its review output.

### Metrics recorded per case

Latency, prompt/completion tokens, prompt-eval and generation durations,
tok/s rates, draft-token acceptance (speculative decoding on llama.cpp), plus
the raw response and reasoning text for post-hoc analysis.

### Resume semantics — the important footgun

Runs resume: completed `(model, case_id)` pairs are skipped on rerun. **A
corrupted response scored 0 counts as "completed"**, not as an error — so a
plain rerun keeps garbage results. `*_RETRY_FAILURES=1` first *moves* the whole
report set to `archives/<benchmark>/<timestamp>/`, then runs completely fresh
(the name is misleading — after the move there is nothing to resume).

Per-language artifacts (`*-results-<lang>.json` etc.) exist because case IDs
repeat across languages — `quality-01` exists in every file. Python keeps the
legacy unsuffixed names so pre-multi-language runs still resume.

## `modules/benchmark_galileo_reviews.py`

Targets the llama.cpp router on Galileo. Models are router aliases from
`config/models.json` (`coder-ornith:LATEST` etc. — each alias carries a
server-side preset). Sends llama.cpp-specific fields: `timings_per_token`,
`t_max_predict_ms`, `thinking_budget_tokens`, plus JSON-schema
`response_format` — llama.cpp handles thinking + grammar together fine.

Judge defaults to the local oMLX `tiel-critic` profile (`GALILEO_JUDGE_*` envs).

```bash
python3 -m modules.benchmark_galileo_reviews --presets --judge yes
python3 -m modules.benchmark_galileo_reviews --lang=rust --presets --judge yes
```

## `modules/benchmark_omlx_reviews.py`

oMLX is OpenAI-compatible but differs in ways that matter:

- **Streaming is mandatory.** PP/TG tok/s, `prompt_eval_duration`,
  `generation_duration`, and `time_to_first_token` exist *only* in the terminal
  SSE usage chunk (`stream=true` + `stream_options.include_usage=true`).
  Non-streaming gives just token counts.
- **oMLX-native fields**: `thinking_budget` (not `thinking_budget_tokens`),
  `repetition_penalty` (not `repeat_penalty`), `enable_thinking`,
  `chat_template_kwargs`.
- **`model:profile` aliases** (e.g. `Devstral-...:devstral-code`) keep the
  server-side profile's sampling — no `temperature=0` override is sent, unless
  a `presets-omlx.ini` entry overrides it. Bare model IDs get INI sampling.
- **Warm-up request** per model (`OMLX_WARMUP=1`) absorbs `model_load_duration`
  so case-1 latency isn't polluted by 15–60s model loads.
- HTTP 409 (model busy/unloading) is retryable.
- API key auto-read from `~/.omlx/settings.json` (`auth.api_key`).

### The thinking + schema bug (why `OMLX_THINKING` exists)

On oMLX, `enable_thinking=true` combined with `response_format` JSON-schema is
**broken**: models emit `[]`, empty arrays, or corrupted content — e.g. Tiel
wrote clean JSON into `reasoning_content` while `content` got mid-word
fragments with CJK token garbage. Likely a grammar-constrained generation
interacting badly with the think→content channel transition (possibly
MTP-related). The same models work fine in chat/agent contexts because those
never send `response_format`.

So the runner offers two modes:

| Mode | Payload | Tradeoff |
|---|---|---|
| `OMLX_THINKING=1` (default) | thinking on, budget 256, **no schema** | +8–15 points via better recall; looser parsing |
| `OMLX_THINKING=0` | thinking off + strict schema | clean structure, honest precision, lower scores |

An A/B test showed thinking-off+schema is the honest baseline, but thinking-on
scores higher mainly through recall. **Caveat**: without the schema, malformed
output collapses to a single blob-finding, which *inflates* keyword precision
(a blob containing keywords counts as one supported finding). The judge
neutralizes most of this inflation — prefer `--judge yes` for real runs.

### Judge placement

Default judge is `critic-ornith:LATEST` **on Galileo**, not local — running the
judge on the same oMLX server evicts the subject model between calls. Judge
payloads force `enable_thinking=false`: Galileo's thinking critic aliases burn
the whole 50-token verdict budget on reasoning and return empty content
(verdicts silently fall back to "no" — a real scoring hazard, fixed by
disabling thinking rather than raising the token cap).

Key hygiene: the local oMLX API key is forwarded to a judge endpoint only when
it shares the benchmark URL; a remote judge falls back to `sk-noauth` unless
`OMLX_JUDGE_API_KEY` is set.

```bash
OMLX_RETRY_FAILURES=1 python3 -m modules.benchmark_omlx_reviews --presets --judge yes
OMLX_THINKING=0 python3 -m modules.benchmark_omlx_reviews --lang=cpp
```

## `modules/benchmark_agent_tools.py` (omlx-tools / omlx-logic / galileo-tools / galileo-logic)

Two deterministic suites behind `--suite`, on either backend via
`--backend {omlx,galileo}` (default `omlx`):

- **`tool-use`** (`test_definitions/tool_use.json`, spec
  `docs/TOOLS_USE_TEST_SPEC.md`) — 10 cases against simulated tools with
  OpenAI-style function calling. Scores call/turn efficiency, classifies
  invalid calls, identical retries, off-plan calls, and forbidden-tool hits
  (including a prompt-injection case), then grades the final JSON answer.
- **`logic`** (`test_definitions/logic.json`, spec
  `docs/LOGIC_TEST_SPEC.md`) — 21 single-turn reasoning cases across 10
  categories (deduction, validity, constraints, underdetermination,
  simulation, intervention, self-reference, symbol manipulation, planning).
  Typed per-field grading (bool/number/enum/set/map) with partial credit;
  no judge, no tools.

Both record `extracted_answer` and `answer_text` per case in the results
JSON (gitignored under `results/`); CSV output stays a flat metric sheet.
Resume keys on `(model, case_id)`.

Backend selection switches the request dialect (oMLX `thinking_budget` vs
llama.cpp `thinking_budget_tokens`/`timings_per_token`/`t_max_predict_ms`),
the preset-file parser (oMLX or llama.cpp INI key maps), the model list
(`config/models-omlx.json` vs `config/models.json`), usage normalization
(oMLX `usage` rates vs llama.cpp `timings`), and the artifact directories
(`results/omlx-tools/` vs `results/galileo-tools/`). The `:`-alias
temperature override applies on oMLX only — Galileo `:TAG` names are router
aliases, not sampling profiles. Warm-up is oMLX-only.

```bash
bin/bench.py omlx-tools --presets config/presets-omlx-agent.ini
bin/bench.py omlx-logic --presets config/presets-omlx-coder.ini
bin/bench.py galileo-tools --presets config/presets.ini
bin/bench.py galileo-logic
```

## `modules/benchmark_opencode_agents.py`

Runs the same review cases through the **OpenCode agent** with two MCP
servers forced. `--backend {galileo,omlx}` (default `galileo`) picks the
provider prefix on `opencode run --model <provider>/<model>` — `galileo/…`
for the llama.cpp router, `omlx/…` for the local MLX server — plus the
matching model list (`config/models.json` vs `config/models-omlx.json`) and
results directory (`results/opencode-agent/` vs `results/opencode-omlx/`).
`OPENCODE_PROVIDER` overrides the prefix when the provider name in your
`opencode.json` differs from the backend name.

- **Serena** — `serena_read_file` on a materialized workspace fixture
- **Headroom** — `headroom_headroom_compress` for context compression

Each case becomes `opencode-agent-suite/workspaces/review-project[-<lang>]/cases/<case_id>/target.<ext>`
plus a `project_context.md`. The prompt prescribes an exact workflow: read
target, read context, build evidence summary, compress once, emit final JSON —
no other tools allowed.

Two session modes (`OPENCODE_SESSION_MODES`, default `continuing,fresh`):
*continuing* reuses one session per model (context accumulation), *fresh* gets
a new session per case. Scoring: `overall = 0.8 × content_F1 + 0.2 × tool_compliance`
where tool compliance is ½·serena_used + ½·headroom_used.

A model's **first case doubles as preflight** — if it errors, remaining cases
for that model/mode are deferred instead of burning 20 timeouts.

Language switching isolates everything: workspace dir, results, CSV, Markdown,
log, and the `events.jsonl` stream all get a `-<lang>` suffix. Caveat: the
workflow only uses `serena_read_file` (generic file read), so non-Python runs
work without a language server — but extending to symbol navigation would need
per-language LSPs.

## `modules/math_bench_galileo_reviews.py`

Ten applied math tasks (`modules/math_tasks.py`, stdlib-only reference
implementations): summation stability, number theory, modular arithmetic,
linear algebra, root finding, Monte Carlo, geometry, integration, recurrences.
The prompt demands a strict `{"answer": "..."}` JSON with the derivation kept
out of the response ("sharp-answer harness"), then numeric-matches with
tolerance.

`--backend {galileo,omlx}` (default `galileo`) selects the request dialect:
Galileo sends `timings_per_token`/`t_max_predict_ms`/`thinking_budget_tokens`
and reads `timings`; oMLX sends `thinking_budget`, drops `response_format`
when thinking is enabled (the empty-array bug), and reads `usage` rates.
`OMLX_*` env vars mirror the `GALILEO_*` ones (`OMLX_MATH_RESULTS`,
`OMLX_MATH_LOG`, …); oMLX results go to `results/omlx-math/`.

## `modules/judge_ab_test.py`

Scores one subject model under two judge profiles (`tiel-verifier` vs
`tiel-critic`) to measure judge sensitivity. Hardcoded model list at top of
file; output to `results/judge/`.

## Configuration

| File | Purpose |
|---|---|
| `config/models.json` | Galileo router aliases (review + pipeline + math benchmarks) |
| `config/models-omlx.json` | oMLX model IDs, including `:profile` aliases |
| `config/presets.ini` | llama.cpp per-model request sampling for Galileo runs |
| `config/presets-omlx.ini` | oMLX request sampling for bare model IDs (original baseline) |
| `config/presets-omlx-coder.ini` | Client-side "coder" profile: explicit per-request overrides replicating the as-tested config (uniform `thinking-budget=4096` where supported) |
| `config/presets-omlx-agent.ini` | Client-side "agentic" profile: hotter sampling + larger thinking budgets, injected per-request to overrule server defaults |
| `config/presets-galileo-agent.ini` | Client-side "agentic" profile for Galileo aliases: hotter sampling + `thinking-budget` (llama.cpp `thinking_budget_tokens`), injected per-request over router defaults |

oMLX presets use request fields only (`temperature`, `top_p`, `top_k`,
`min_p`, `repetition_penalty`, `enable_thinking`, `thinking_budget`,
`reasoning_effort`, `seed`). Server flags like `n-gpu-layers`/`ctx-size` are
meaningless there — oMLX silently ignores unknown fields, which is exactly how
the original port looked like it worked while dropping half its parameters.

## Model parametrization — the hidden experimental variable

Sampling parameters are *not* constant across this suite. They come from three
layers, and which layer wins differs per backend:

**Baseline (control variable).** Every runner sends `temperature: 0` unless a
preset or server-side profile overrides it — greedy decoding is the default
measurement regime, so runs without `--presets` are near-deterministic.

**Galileo: the alias IS the parametrization.** `config/presets.ini` is the
llama.cpp *router's own* preset file: each `[alias]` section binds a GGUF path,
server flags (`ctx-size`, `cache-type-k/v`, `n-gpu-layers`, `threads`,
`flash-attn`), and sampling defaults. The same weights appear under different
aliases with different regimes — `coder-*` (temp 0.8), `critic-*` (0.2),
`assistant-*`, `reviewer-*`. Choosing `coder-ornith` vs `critic-ornith` is a
sampling decision, not a model decision. When the benchmark is run with
`--presets`, the *same file* is parsed client-side and its request-level keys
(`temp`, `top-p`, `top-k`, `min-p`, `repeat-penalty`, `presence-penalty`,
`max-tokens`, `thinking-budget`, `enable-thinking`) are sent as per-request
overrides on top of the alias defaults — `presets-galileo-agent.ini` uses this
to give every alias a uniform agentic regime. `enable-thinking` presets are
reconciled with `chat_template_kwargs.enable_thinking` and zero the
`thinking_budget_tokens` field when off. Server flags (`ctx-size`, `threads`,
…) are parsed but **not** forwarded — they belong to router startup.

**oMLX: two sub-regimes.** `config/presets-omlx.ini` maps INI keys to
`ChatCompletionRequest` fields (`temp`→`temperature`, `top-p`→`top_p`,
`top-k`→`top_k`, `min-p`, `repeat-penalty`→`repetition_penalty`,
`presence-penalty`, `frequency-penalty`, `max-tokens`, `thinking-budget`,
`reasoning-effort`, `enable-thinking`, `seed`, `xtc-*`, `specprefill*`).

- *Bare model IDs* get the `temperature=0` baseline plus whatever the INI
  section adds — currently temp 0.6–0.8 with top-p 0.95, which is deliberately
  *not* the greedy baseline.
- *`model:profile` aliases* skip the temperature baseline entirely: the
  server-side oMLX profile governs sampling, so the benchmark measures the
  profile, not the raw model. An INI section for the alias overrides the
  profile key-by-key.
- A preset `enable-thinking` value is reconciled with
  `chat_template_kwargs.enable_thinking` — oMLX rejects contradictory
  top-level and template controls with HTTP 400. Preset thinking only matters
  for `OMLX_THINKING=0` runs; enabling it under `response_format` reproduces
  the `[]` bug.

**Thinking budget** is also a parameter, with different spellings per backend:
`GALILEO_THINKING_BUDGET`→`thinking_budget_tokens` (llama.cpp, 256 default) vs
`OMLX_THINKING_BUDGET`→`thinking_budget` (oMLX, 256).

**Auditability.** Every report records `model_parameters` per model: the
resolved `api_model`, the applied `sampling` dict, `runtime_props` (Galileo)
or `model_load_seconds` (oMLX). Plus `benchmark_parameters` captures
`temperature` baseline, `max_tokens`, thinking state, `presets_path`, and
`prompt_version`. If you change presets between runs, archived reports still
tell you exactly which regime produced which scores.

**Caveat:** with `--presets` enabled, oMLX bare models run temp 0.6–0.8 while
the same run's baseline was designed around greedy decoding — sampling noise
becomes part of the measurement. `seed` is supported in the INI if you want
reproducible sampling; it is not set by default.

## Environment variables

| Script | Key variables |
|---|---|
| galileo | `GALILEO_BASE_URL`, `GALILEO_API_KEY`, `GALILEO_MAX_TOKENS` (700), `GALILEO_THINKING_BUDGET` (256), `GALILEO_START_MODEL`, `GALILEO_RETRY_FAILURES`, `GALILEO_PRESETS`, `GALILEO_JUDGE_{MODEL,BASE_URL,API_KEY,MAX_TOKENS,TIMEOUT}`, `GALILEO_REVIEW_RESULTS`, `GALILEO_REVIEW_LOG` |
| omlx | `OMLX_BASE_URL`, `OMLX_API_KEY`, `OMLX_MAX_TOKENS` (1024), `OMLX_THINKING` (1), `OMLX_THINKING_BUDGET` (256), `OMLX_WARMUP` (1), `OMLX_START_MODEL`, `OMLX_RETRY_FAILURES`, `OMLX_MODELS`, `OMLX_PRESETS`, `OMLX_JUDGE_{MODEL,BASE_URL,API_KEY,TIMEOUT}`, `OMLX_NO_THINKING_MODELS`, `OMLX_NO_SCHEMA_MODELS`, `OMLX_REVIEW_RESULTS`, `OMLX_REVIEW_LOG` |
| omlx agentic suites | `OMLX_MODELS`, `OMLX_MAX_TOKENS`, `OMLX_BENCH_RESULTS`, `OMLX_BENCH_LOG`, `OMLX_START_MODEL`, plus the shared `OMLX_BASE_URL`/`OMLX_API_KEY` |
| galileo agentic suites | `GALILEO_MODELS`, `GALILEO_MAX_TOKENS`, `GALILEO_BENCH_RESULTS`, `GALILEO_BENCH_LOG`, `GALILEO_START_MODEL`, plus `GALILEO_BASE_URL`/`GALILEO_API_KEY` |
| math (omlx) | `OMLX_MATH_RESULTS`, `OMLX_MATH_LOG`, `OMLX_MODELS`, `OMLX_MAX_TOKENS` (700), `OMLX_THINKING_BUDGET` (256), `OMLX_PREDICT_TIMEOUT_MS`, `OMLX_MAX_RETRIES`, `OMLX_START_MODEL`, `OMLX_RETRY_FAILURES`, `OMLX_PRESETS` |
| opencode | `OPENCODE_SESSION_MODES`, `OPENCODE_CASE_TIMEOUT` (360s), `OPENCODE_MAX_AGENT_STEPS` (8), `OPENCODE_RETRY_FAILURES` (1), `OPENCODE_PROVIDER` |

All scripts also accept `--presets [path]` and `--lang {python,javascript,rust,cpp,ruby}`
(opencode takes `--lang` only; judges get `--judge yes` + `--judge-model`, and
oMLX additionally `--judge-url`).

## Comparability caveats

- **Galileo vs oMLX scores are not directly comparable**: different quants
  (GGUF vs MLX), KV-cache and speculative-decoding paths, server-side profile
  sampling, and the thinking+schema divergence. Same prompts ≠ same conditions.
- **Cross-language scores aren't comparable either** — same themes, different
  code, language-specific indicator tuning. Treat each language's table as its
  own ranking.
- `:profile` alias models run server-side sampling — they benchmark the
  profile, not the bare model.
- `max_tokens` truncation matters: a truncated response becomes one blob
  finding → keyword precision reads 100% on garbage. Galileo used 700; oMLX
  defaults to 1024 because thinking models kept hitting the cap.
- Keyword scoring vs judge scoring produce systematically different precision
  — don't mix scoring modes inside one comparison table.

## Layout

```
bin/                runnable entry points (bench.py orchestrator)
modules/            benchmark runners and shared support modules
tests/              offline unit tests plus the live Galileo smoke test
config/             model lists, presets (INI)
test_definitions/   static review cases per language (<lang>.json)
results/            current artifacts per benchmark (galileo-review/, omlx-review/, ...)
logs/               operational logs
archives/           immutable snapshots moved by *_RETRY_FAILURES=1
reports/            human-readable analyses
docs/               infrastructure notes
opencode-agent-suite/  agent fixture workspaces (cases/ tree per language)
```

`modules/benchmark_paths.py` owns all path constants;
`modules/review_definitions.py` owns the case dataclasses, the pydantic
definition schema, and the per-language path helper shared by all three
review runners. Run a runner directly with `python3 -m modules.<name>` from
the repo root, or go through `bin/bench.py` from anywhere.

## Verification

```bash
python3 -m unittest discover -s tests -t .

ruff check modules/ tests/ bin/
mypy modules/benchmark_paths.py modules/benchmark_galileo_reviews.py modules/benchmark_omlx_reviews.py modules/benchmark_agent_tools.py modules/benchmark_opencode_agents.py modules/math_bench_galileo_reviews.py modules/judge_ab_test.py modules/review_definitions.py tests/ bin/
```

`tests/test_galileo_models.py` is a live integration test — excluded from
the offline suite on purpose. `modules/math_tasks.py` is a stdlib demo
script and is intentionally outside the mypy gate.
