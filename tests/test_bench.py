"""Offline unit tests for bench.py dispatch: bench selection, backend and
suite default injection, and argument pass-through (runners are stubbed)."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from bin import bench


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

    def test_omlx_review_routes_to_omlx_runner(self) -> None:
        module, runner_argv = self._run(
            ["omlx-review", "--lang", "python", "--judge", "yes"]
        )
        self.assertEqual(module, "modules.benchmark_omlx_reviews")
        self.assertEqual(runner_argv, ["--lang", "python", "--judge", "yes"])

    def test_galileo_review_routes_to_galileo_runner(self) -> None:
        module, runner_argv = self._run(["galileo-review"])
        self.assertEqual(module, "modules.benchmark_galileo_reviews")
        self.assertEqual(runner_argv, [])

    def test_omlx_tools_injects_backend_and_suite(self) -> None:
        module, runner_argv = self._run(
            ["omlx-tools", "--presets", "config/presets-omlx-agent.ini"]
        )
        self.assertEqual(module, "modules.benchmark_agent_tools")
        self.assertEqual(
            runner_argv,
            [
                "--backend", "omlx",
                "--suite", "tool-use",
                "--presets", "config/presets-omlx-agent.ini",
            ],
        )

    def test_omlx_logic_injects_backend_and_suite(self) -> None:
        module, runner_argv = self._run(["omlx-logic"])
        self.assertEqual(module, "modules.benchmark_agent_tools")
        self.assertEqual(runner_argv, ["--backend", "omlx", "--suite", "logic"])

    def test_galileo_suites_inject_galileo_backend(self) -> None:
        _, runner_argv = self._run(["galileo-tools"])
        self.assertEqual(
            runner_argv, ["--backend", "galileo", "--suite", "tool-use"]
        )
        _, runner_argv = self._run(["galileo-logic"])
        self.assertEqual(
            runner_argv, ["--backend", "galileo", "--suite", "logic"]
        )
        _, runner_argv = self._run(["galileo-agent"])
        self.assertEqual(runner_argv, ["--backend", "galileo"])

    def test_explicit_suite_not_duplicated(self) -> None:
        _, runner_argv = self._run(["omlx-agent", "--suite", "logic"])
        self.assertEqual(runner_argv, ["--backend", "omlx", "--suite", "logic"])

    def test_explicit_backend_not_duplicated(self) -> None:
        _, runner_argv = self._run(["omlx-tools", "--backend", "galileo"])
        self.assertEqual(
            runner_argv, ["--suite", "tool-use", "--backend", "galileo"]
        )

    def test_pipeline_and_math_route_with_backend(self) -> None:
        self.assertEqual(
            self._run(["galileo-pipeline"]),
            ("modules.benchmark_opencode_agents", ["--backend", "galileo"]),
        )
        self.assertEqual(
            self._run(["omlx-pipeline"]),
            ("modules.benchmark_opencode_agents", ["--backend", "omlx"]),
        )
        self.assertEqual(
            self._run(["galileo-math"]),
            ("modules.math_bench_galileo_reviews", ["--backend", "galileo"]),
        )
        self.assertEqual(
            self._run(["omlx-math"]),
            ("modules.math_bench_galileo_reviews", ["--backend", "omlx"]),
        )

    def test_unknown_bench_rejected(self) -> None:
        with (
            mock.patch.object(sys, "stderr"),
            self.assertRaises(SystemExit),
        ):
            bench.main(["nonsense"])


if __name__ == "__main__":
    unittest.main()
