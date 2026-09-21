#!/usr/bin/env python3
"""Single entry point dispatching to the benchmark runners.

Usage:
    bench.py <bench> [runner arguments...]

Benches (named <backend>-<what>):
    omlx-review       quality+security review on oMLX models
                      (benchmark_omlx_reviews.py)
    galileo-review    quality+security review on Galileo models
                      (benchmark_galileo_reviews.py)
    omlx-tools        tool-use suite on oMLX models
                      (benchmark_agent_tools.py --suite tool-use)
    omlx-logic        logic suite on oMLX models
                      (benchmark_agent_tools.py --suite logic)
    omlx-agent        both agentic suites; pass --suite tool-use|logic
    galileo-pipeline  full agentic pipeline (OpenCode/Serena/Headroom)
                      on Galileo models (benchmark_opencode_agents.py)
    galileo-math      math benchmark on Galileo models
                      (math_bench_galileo_reviews.py)

Everything after the bench name is forwarded verbatim to that runner's
argparse, e.g.:

    bench.py omlx-tools --presets config/presets-omlx-agent.ini
    bench.py omlx-review --lang python --judge yes

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
    "omlx-review": "benchmark_omlx_reviews",
    "galileo-review": "benchmark_galileo_reviews",
    "omlx-agent": "benchmark_agent_tools",
    "omlx-tools": "benchmark_agent_tools",
    "omlx-logic": "benchmark_agent_tools",
    "galileo-pipeline": "benchmark_opencode_agents",
    "galileo-math": "math_bench_galileo_reviews",
}

SUITE_ALIASES: Final[dict[str, str]] = {
    "omlx-tools": "tool-use",
    "omlx-logic": "logic",
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
        help="Benchmark to run (<backend>-<what>)",
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
