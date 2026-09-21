#!/usr/bin/env python3
"""Single entry point dispatching to the benchmark runners.

Usage:
    bench.py <bench> [runner arguments...]

Benches:
    reviews   oMLX code-review benchmark (benchmark_omlx_reviews.py)
    galileo   Galileo code-review benchmark (benchmark_galileo_reviews.py)
    agent     agentic suites; pass --suite tool-use|logic
              (benchmark_agent_tools.py)
    tools     alias for: agent --suite tool-use
    logic     alias for: agent --suite logic
    opencode  OpenCode agent fixture benchmark (benchmark_opencode_agents.py)
    math      Galileo math benchmark (math_bench_galileo_reviews.py)

Everything after the bench name is forwarded verbatim to that runner's
argparse, e.g.:

    bench.py tools --presets config/presets-omlx-agent.ini
    bench.py reviews --lang python --judge yes

Environment variables (OMLX_*, GALILEO_*) apply exactly as when invoking
the runner directly; runner modules are imported lazily after argument
parsing so their module-level env binding is unaffected.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Final

BENCH_MODULES: Final[dict[str, str]] = {
    "reviews": "benchmark_omlx_reviews",
    "galileo": "benchmark_galileo_reviews",
    "agent": "benchmark_agent_tools",
    "tools": "benchmark_agent_tools",
    "logic": "benchmark_agent_tools",
    "opencode": "benchmark_opencode_agents",
    "math": "math_bench_galileo_reviews",
}

SUITE_ALIASES: Final[dict[str, str]] = {
    "tools": "tool-use",
    "logic": "logic",
}


def main(argv: list[str] | None = None) -> int:
    """Parse the bench selector and forward remaining args to its runner."""
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description="Dispatch to a benchmark runner by name.",
    )
    parser.add_argument(
        "bench",
        choices=sorted(BENCH_MODULES),
        help="Benchmark to run (tools/logic select agent suites)",
    )
    parser.add_argument(
        "runner_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the runner unchanged",
    )
    args = parser.parse_args(argv)

    runner_args = list(args.runner_args)
    suite = SUITE_ALIASES.get(args.bench)
    if suite is not None and "--suite" not in runner_args:
        runner_args = ["--suite", suite, *runner_args]

    module = importlib.import_module(BENCH_MODULES[args.bench])
    return int(module.main(runner_args))


if __name__ == "__main__":
    sys.exit(main())
