# Project Guide

## Setup

```bash
make install   # uv sync: .venv with pinned runtime + dev deps (requires uv)
make check     # offline unit tests + ruff + mypy (no live servers needed)
make audit     # uv audit (dependency CVEs) + bandit source scan (MEDIUM+ gates)
```

Bandit findings on `urllib.request.urlopen` (B310) are marked `# nosec` with
justification: endpoint URLs come from operator-configured `*_BASE_URL` env
vars, never request input. Do not silence other findings the same way without
an equivalent justification.

## Layout

- `bin/` contains runnable entry points: `bin/bench.py <bench> [runner args]` dispatches to runners by `<backend>-<what>` names: `omlx-review`, `galileo-review`, `omlx-tools`, `omlx-logic`, `omlx-agent`, `galileo-tools`, `galileo-logic`, `galileo-agent`, `galileo-pipeline`, `omlx-pipeline`, `galileo-math`, `omlx-math`. It injects `--backend`/`--suite` defaults unless the user passed those flags. `bin/report.py <results.json>` builds a Markdown analysis draft (computed facts only — leaderboard, injected-regime table, thinking telemetry, per-case spread, caveats) for `reports/`; inference sections are left as TODOs. Runner modules also run via `python3 -m modules.<name>` from the repo root.
- `modules/` is the Python package holding all benchmark runners and shared support modules. Intra-package imports are relative (`from .benchmark_paths import ...`).
- `tests/` contains offline unit tests plus the live Galileo smoke test.
- `config/` contains model lists, active presets, and preset snapshots.
- `test_definitions/` contains static JSON review cases per language (`<lang>.json`), the tool-use suite (`tool_use.json`, spec `docs/TOOLS_USE_TEST_SPEC.md`), and the logic suite (`logic.json`, spec `docs/LOGIC_TEST_SPEC.md`).
- `results/` contains current machine-generated benchmark artifacts grouped by benchmark.
- `logs/` contains active operational logs.
- `archives/` contains immutable historical run snapshots grouped by benchmark.
- `reports/` contains human-readable benchmark analyses.
- `docs/` contains infrastructure and deployment documentation.
- `opencode-agent-suite/` contains the isolated OpenCode fixture workspace.

## Verification

Run offline unit tests from the repo root:

```bash
python3 -m unittest discover -s tests -t .
```

Run static checks:

```bash
ruff check modules/ tests/ bin/
mypy modules/benchmark_paths.py modules/benchmark_galileo_reviews.py modules/benchmark_omlx_reviews.py modules/benchmark_agent_tools.py modules/benchmark_opencode_agents.py modules/math_bench_galileo_reviews.py modules/judge_ab_test.py modules/report_builder.py modules/review_definitions.py tests/ bin/
```

`tests/test_galileo_models.py` is a live Galileo integration test and is intentionally excluded from the offline unit-test command. `modules/math_tasks.py` is a stdlib demo script and stays outside the mypy gate.
