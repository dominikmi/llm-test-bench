# llm-test-bench — clone-to-run entry points.
#
#   make prereqs   verify required tooling (uv, Python >= 3.11) and warn
#                  about optional runtime tools (opencode, uvx, servers)
#   make install   uv sync — create .venv with runtime + dev dependencies
#   make check     offline unit tests + ruff + mypy
#   make audit     uv audit (lockfile CVEs) + bandit (source scan)
#   make clean     remove .venv and tool caches

UV ?= uv

.PHONY: help prereqs install check audit clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

prereqs: ## Verify required tooling and warn about optional runtime tools
	@command -v $(UV) >/dev/null || { \
		echo "error: uv not found — install: brew install uv (or https://docs.astral.sh/uv/)"; \
		exit 1; }
	@$(UV) python find '>=3.11' >/dev/null 2>&1 || { \
		echo "error: Python >= 3.11 required — run: $(UV) python install 3.11"; \
		exit 1; }
	@echo "required: uv $(shell $(UV) --version | awk '{print $$2}'), Python >= 3.11 — OK"
	@command -v opencode >/dev/null \
		|| echo "note: 'opencode' CLI missing — only needed for *-pipeline benches"
	@command -v uvx >/dev/null \
		|| echo "note: 'uvx' missing — only needed for the Serena MCP in *-pipeline benches"
	@echo "note: benchmarks need a reachable oMLX (127.0.0.1:8000) and/or" \
		"Galileo (GALILEO_BASE_URL) server at run time — not for make check/audit"

install: prereqs ## Create/update .venv via uv sync (runtime + dev deps)
	$(UV) sync

check: install ## Offline unit tests + ruff + mypy (no live servers needed)
	$(UV) run python -m unittest \
		tests.test_bench \
		tests.test_benchmark_agent_tools \
		tests.test_benchmark_galileo_reviews \
		tests.test_benchmark_omlx_reviews \
		tests.test_benchmark_opencode_agents \
		tests.test_report_builder
	$(UV) run ruff check modules/ tests/ bin/
	$(UV) run mypy modules tests bin --exclude 'math_tasks'

audit: install ## uv audit (lockfile CVEs) + bandit source scan
	$(UV) audit --preview-features audit-command
	$(UV) run bandit -r modules/ -ll

clean: ## Remove .venv and tool caches
	rm -rf .venv .mypy_cache .ruff_cache .pytest_cache \
		modules/__pycache__ tests/__pycache__ bin/__pycache__
