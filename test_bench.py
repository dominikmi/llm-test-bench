"""Offline unit tests for bench.py dispatch: bench selection, suite alias
injection, and argument pass-through (runner modules are stubbed)."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

import bench


class DispatchTests(unittest.TestCase):
    """main() must route to the right module with forwarded argv."""

    def _run(self, argv: list[str]) -> tuple[str, list[str]]:
        calls: list[tuple[str, list[str]]] = []

        def fake_import(name: str) -> types.ModuleType:
            module = types.ModuleType(name)

            def runner_main(runner_argv: list[str]) -> int:
                calls.append((name, list(runner_argv)))
                return 0

            module.main = runner_main  # type: ignore[attr-defined]
            return module

        with mock.patch.object(
            bench.importlib, "import_module", side_effect=fake_import
        ):
            code = bench.main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_reviews_routes_to_omlx_runner(self) -> None:
        module, runner_argv = self._run(
            ["reviews", "--lang", "python", "--judge", "yes"]
        )
        self.assertEqual(module, "benchmark_omlx_reviews")
        self.assertEqual(runner_argv, ["--lang", "python", "--judge", "yes"])

    def test_galileo_routes_to_galileo_runner(self) -> None:
        module, _ = self._run(["galileo"])
        self.assertEqual(module, "benchmark_galileo_reviews")

    def test_tools_alias_injects_suite(self) -> None:
        module, runner_argv = self._run(
            ["tools", "--presets", "config/presets-omlx-agent.ini"]
        )
        self.assertEqual(module, "benchmark_agent_tools")
        self.assertEqual(
            runner_argv,
            ["--suite", "tool-use", "--presets", "config/presets-omlx-agent.ini"],
        )

    def test_logic_alias_injects_suite(self) -> None:
        module, runner_argv = self._run(["logic"])
        self.assertEqual(module, "benchmark_agent_tools")
        self.assertEqual(runner_argv, ["--suite", "logic"])

    def test_explicit_suite_not_duplicated(self) -> None:
        _, runner_argv = self._run(["agent", "--suite", "logic"])
        self.assertEqual(runner_argv, ["--suite", "logic"])

    def test_opencode_and_math_route(self) -> None:
        self.assertEqual(self._run(["opencode"])[0], "benchmark_opencode_agents")
        self.assertEqual(
            self._run(["math"])[0], "math_bench_galileo_reviews"
        )

    def test_unknown_bench_rejected(self) -> None:
        with (
            mock.patch.object(sys, "stderr"),
            self.assertRaises(SystemExit),
        ):
            bench.main(["nonsense"])


if __name__ == "__main__":
    unittest.main()
