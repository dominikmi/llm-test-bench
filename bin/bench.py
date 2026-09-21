#!/usr/bin/env python3
"""Single entry point dispatching to the benchmark runners.

Usage:
    bin/bench.py <bench> [runner arguments...]

Benches (named <backend>-<what>):
    omlx-review       quality+security review on oMLX models
                      (modules/benchmark_omlx_reviews.py)
    galileo-review    quality+security review on Galileo models
                      (modules/benchmark_galileo_reviews.py)
    omlx-tools        tool-use suite on oMLX models
    omlx-logic        logic suite on oMLX models
    omlx-agent        both agentic suites on oMLX; pass --suite explicitly
    galileo-tools     tool-use suite on Galileo models
    galileo-logic     logic suite on Galileo models
    galileo-agent     both agentic suites on Galileo; pass --suite explicitly
                      (all six via modules/benchmark_agent_tools.py)
    galileo-pipeline  full agentic pipeline (OpenCode/Serena/Headroom)
                      on Galileo models
    omlx-pipeline     full agentic pipeline on oMLX models
                      (both via modules/benchmark_opencode_agents.py)
    galileo-math      math benchmark on Galileo models
    omlx-math         math benchmark on oMLX models
                      (both via modules/math_bench_galileo_reviews.py)

Everything after the bench name is forwarded verbatim to that runner's
argparse, e.g.:

    bin/bench.py omlx-tools --presets config/presets-omlx-agent.ini
    bin/bench.py omlx-review --lang python --judge yes

Environment variables (OMLX_*, GALILEO_*) apply exactly as when invoking
the runner module directly (python3 -m modules.<runner>); runner modules
are imported lazily after argument parsing so their module-level env
binding is unaffected.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Final

# Runners live in the modules/ package next to this script's directory.
PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BENCH_MODULES: Final[dict[str, str]] = {
    "omlx-review": "benchmark_omlx_reviews",
    "galileo-review": "benchmark_galileo_reviews",
    "omlx-agent": "benchmark_agent_tools",
    "omlx-tools": "benchmark_agent_tools",
    "omlx-logic": "benchmark_agent_tools",
    "galileo-agent": "benchmark_agent_tools",
    "galileo-tools": "benchmark_agent_tools",
    "galileo-logic": "benchmark_agent_tools",
    "galileo-pipeline": "benchmark_opencode_agents",
    "omlx-pipeline": "benchmark_opencode_agents",
    "galileo-math": "math_bench_galileo_reviews",
    "omlx-math": "math_bench_galileo_reviews",
}

# Flag/value pairs injected unless the user already passed the flag.
BENCH_DEFAULT_ARGS: Final[dict[str, tuple[str, ...]]] = {
    "omlx-agent": ("--backend", "omlx"),
    "omlx-tools": ("--backend", "omlx", "--suite", "tool-use"),
    "omlx-logic": ("--backend", "omlx", "--suite", "logic"),
    "galileo-agent": ("--backend", "galileo"),
    "galileo-tools": ("--backend", "galileo", "--suite", "tool-use"),
    "galileo-logic": ("--backend", "galileo", "--suite", "logic"),
    "galileo-pipeline": ("--backend", "galileo"),
    "omlx-pipeline": ("--backend", "omlx"),
    "galileo-math": ("--backend", "galileo"),
    "omlx-math": ("--backend", "omlx"),
}


def _inject_defaults(
    runner_args: list[str], defaults: tuple[str, ...]
) -> list[str]:
    """Prepend flag/value pairs for flags the user did not pass."""
    present = {
        arg.split("=", 1)[0] for arg in runner_args if arg.startswith("--")
    }
    injected: list[str] = []
    for flag, value in zip(defaults[::2], defaults[1::2], strict=True):
        if flag not in present:
            injected.extend((flag, value))
    return [*injected, *runner_args]


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

    runner_args = _inject_defaults(
        list(args.runner_args), BENCH_DEFAULT_ARGS.get(args.bench, ())
    )
    module = importlib.import_module(
        f"modules.{BENCH_MODULES[args.bench]}"
    )
    return int(module.main(runner_args))


if __name__ == "__main__":
    sys.exit(main())
