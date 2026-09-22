"""Offline unit tests for the report builder's fact extraction and flags."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modules import report_builder


def _review_result(**overrides: object) -> dict:
    base: dict = {
        "model": "m-a",
        "case_id": "quality-01",
        "category": "quality",
        "score": 80.0,
        "recall": 66.0,
        "precision": 100.0,
        "matched_findings": ["a"],
        "missed_findings": ["b"],
        "unsupported_findings": 0,
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "elapsed_seconds": 5.0,
        "output_tokens_per_second": 10.0,
        "prompt_tokens_per_second": 100.0,
        "reasoning_response": "",
        "error": None,
    }
    base.update(overrides)
    return base


def _write(payload: dict) -> Path:
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "r.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    # keep the temp dir alive until test end via class cleanup
    _tmpdirs.append(directory)
    return path


_tmpdirs: list[tempfile.TemporaryDirectory] = []


def tearDownModule() -> None:
    for directory in _tmpdirs:
        directory.cleanup()


class TestShapeDetection(unittest.TestCase):
    def test_review_shape(self) -> None:
        path = _write({"results": [_review_result()]})
        self.assertEqual("review", report_builder.load_run(path).shape)

    def test_agent_shape(self) -> None:
        path = _write(
            {"results": [{"model": "m", "case_id": "c", "quality": 90.0}]}
        )
        self.assertEqual("agent", report_builder.load_run(path).shape)

    def test_rejects_unknown_shape(self) -> None:
        path = _write({"results": [{"model": "m"}]})
        with self.assertRaises(ValueError):
            report_builder.load_run(path)


class TestThinkingTelemetry(unittest.TestCase):
    """Thinking detection and the runner-default-budget flag."""

    def _run(self) -> report_builder.RunMeta:
        results = [
            _review_result(
                model="m-thinker",
                reasoning_response="x" * 1100,
                matched_findings=["a"],
            ),
            _review_result(model="m-quiet", reasoning_response=""),
        ]
        payload = {
            "benchmark_parameters": {},
            "model_parameters": {
                "m-thinker": {"sampling": {"temperature": 0.6}},
                "m-quiet": {"sampling": {"enable_thinking": False}},
            },
            "results": results,
        }
        return report_builder.load_run(_write(payload))

    def test_detects_reasoning_and_runner_default_flag(self) -> None:
        telemetry = {t.model: t for t in report_builder.thinking_telemetry(self._run())}
        thinker = telemetry["m-thinker"]
        self.assertTrue(thinker.emitted_reasoning)
        self.assertFalse(thinker.thinking_keys_present)  # -> runner default flag
        quiet = telemetry["m-quiet"]
        self.assertFalse(quiet.emitted_reasoning)
        self.assertIs(quiet.enable_thinking_flag, False)

    def test_budget_pinned_detection(self) -> None:
        results = [
            _review_result(model="m", reasoning_response="x" * 1000)
            for _ in range(3)
        ]
        payload = {
            "model_parameters": {
                "m": {"sampling": {"thinking_budget_tokens": 256, "enable_thinking": True}}
            },
            "results": results,
        }
        telemetry = report_builder.thinking_telemetry(report_builder.load_run(_write(payload)))
        # 1000 chars / 4 chars-per-token = 250 <= 256 * 1.05 -> pinned
        self.assertTrue(telemetry[0].likely_budget_pinned)


class TestCaseSpread(unittest.TestCase):
    def test_systematic_miss_detection(self) -> None:
        results = [
            _review_result(model="a", missed_findings=["x", "y"]),
            _review_result(model="b", missed_findings=["x"]),
        ]
        run = report_builder.load_run(_write({"results": results}))
        spread = report_builder.per_case_spread(run)[0]
        self.assertEqual(("x",), spread.all_missed)


class TestReportDir(unittest.TestCase):
    """reports/ is grouped by serving stack."""

    def _run(self, backend: str | None) -> report_builder.RunMeta:
        params = {"backend": backend} if backend else {}
        return report_builder.load_run(
            _write({"benchmark_parameters": params, "results": [_review_result()]})
        )

    def test_omlx_and_galileo_dirs(self) -> None:
        path = Path("results/omlx-review/x.json")
        self.assertEqual(
            Path("reports/omlx"),
            report_builder.report_dir(self._run("omlx"), path),
        )
        self.assertEqual(
            Path("reports/llama-cpp-linux"),
            report_builder.report_dir(self._run("galileo"), path),
        )

    def test_backend_from_dir_name_when_unrecorded(self) -> None:
        path = Path("results/galileo-review/x.json")
        self.assertEqual(
            Path("reports/llama-cpp-linux"),
            report_builder.report_dir(self._run(None), path),
        )


class TestRender(unittest.TestCase):
    def test_markdown_contains_all_sections(self) -> None:
        results = [
            _review_result(model="m-a", score=80.0, reasoning_response="r" * 500),
            _review_result(model="m-b", case_id="quality-02", score=60.0),
        ]
        payload = {
            "benchmark_parameters": {
                "language": "python",
                "temperature": 0,
                "presets_path": "config/x.ini",
                "judge_enabled": True,
                "judge_model": "j",
            },
            "model_parameters": {
                "m-a": {"sampling": {"temperature": 0.6}},
                "m-b": {"sampling": {"temperature": 0.6}},
            },
            "results": results,
        }
        path = _write(payload)
        markdown = report_builder.render_markdown(
            report_builder.load_run(path), path, "T"
        )
        for section in (
            "Provenance",
            "test_definitions/python.json",
            "config/x.ini",
            "bin/bench.py",
            "Effective injected regime",
            "Results",
            "Thinking-channel telemetry",
            "Per-case discrimination",
            "Cost",
            "Caveats",
            "runner default, not the measured regime",
            "runner-default budget",
            "TODO",
        ):
            self.assertIn(section, markdown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
