# Project Guide

## Layout

- Benchmark runners and support modules remain at the project root.
- `config/` contains model lists, active presets, and preset snapshots.
- `test_definitions/` contains static JSON review cases per language (`<lang>.json`) and the tool-use suite (`tool_use.json`, spec in `docs/TOOLS_USE_TEST_SPEC.md`).
- `results/` contains current machine-generated benchmark artifacts grouped by benchmark.
- `logs/` contains active operational logs.
- `archives/` contains immutable historical run snapshots grouped by benchmark.
- `reports/` contains human-readable benchmark analyses.
- `docs/` contains infrastructure and deployment documentation.
- `opencode-agent-suite/` contains the isolated OpenCode fixture workspace.

## Verification

Run offline unit tests:

```bash
python3 -m unittest test_benchmark_galileo_reviews.py test_benchmark_opencode_agents.py test_benchmark_omlx_reviews.py test_benchmark_agent_tools.py
```

Run static checks:

```bash
ruff check benchmark_paths.py benchmark_galileo_reviews.py benchmark_omlx_reviews.py benchmark_opencode_agents.py benchmark_agent_tools.py math_bench_galileo_reviews.py judge_ab_test.py test_benchmark_galileo_reviews.py test_benchmark_omlx_reviews.py test_benchmark_opencode_agents.py test_benchmark_agent_tools.py test_galileo_models.py review_definitions.py
mypy benchmark_paths.py benchmark_galileo_reviews.py benchmark_omlx_reviews.py benchmark_opencode_agents.py benchmark_agent_tools.py math_bench_galileo_reviews.py judge_ab_test.py test_benchmark_galileo_reviews.py test_benchmark_omlx_reviews.py test_benchmark_opencode_agents.py test_benchmark_agent_tools.py test_galileo_models.py review_definitions.py
```

`test_galileo_models.py` is a live Galileo integration test and is intentionally excluded from the offline unit-test command.
