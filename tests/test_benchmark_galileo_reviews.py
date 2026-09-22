"""Offline unit tests for the Galileo review benchmark scoring and reporting."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from modules import benchmark_galileo_reviews as benchmark
from modules import benchmark_paths as paths
from modules import math_bench_galileo_reviews as math_benchmark


def metrics(response: str) -> benchmark.ResponseMetrics:
    """Build deterministic response metrics for offline scoring tests."""
    return benchmark.ResponseMetrics(
        text=response,
        reasoning_text="",
        elapsed_seconds=1.0,
        prompt_tokens=10,
        completion_tokens=20,
        prompt_ms=100.0,
        generation_ms=200.0,
        prompt_tokens_per_second=100.0,
        output_tokens_per_second=100.0,
        draft_tokens=0,
        accepted_draft_tokens=0,
    )


class TestBenchmarkPrompts(unittest.TestCase):
    """Verify calibrated prompts and strict final-answer contracts."""

    def test_review_prompt_prioritizes_supported_distinct_root_causes(self) -> None:
        """Request concrete evidence while keeping category boundaries explicit."""
        quality_prompt = benchmark.build_review_prompt(benchmark.QUALITY_CASES[0])
        security_prompt = benchmark.build_review_prompt(benchmark.SECURITY_CASES[0])
        self.assertIn("one distinct root cause", quality_prompt)
        self.assertIn("smallest exact affected expression", quality_prompt)
        self.assertIn("Prefer fewer high-confidence findings", quality_prompt)
        self.assertIn("Do not report security vulnerabilities", quality_prompt)
        self.assertIn("most specific applicable CWE", security_prompt)
        self.assertIn("exactly four non-empty string fields", security_prompt)
        synthetic = benchmark.ReviewCase(
            "test",
            "quality",
            "python",
            "value = operation()",
            (benchmark.ExpectedFinding("SECRET_EXPECTED_FINDING", ("secret",)),),
        )
        self.assertNotIn("SECRET_EXPECTED_FINDING", benchmark.build_review_prompt(synthetic))

    def test_math_prompt_requests_private_verification_without_answer_leakage(self) -> None:
        """Encourage checking while preserving a sharp final-answer format."""
        case = math_benchmark.MathCase(
            "test", "algebra", "Solve x + 1 = 2.", ("SECRET_ACCEPTED_ANSWER",)
        )
        prompt = math_benchmark.build_math_prompt(case)
        self.assertIn("verify the result", prompt)
        self.assertIn("exactly one JSON object", prompt)
        self.assertIn("one non-empty string field named \"answer\"", prompt)
        self.assertNotIn("SECRET_ACCEPTED_ANSWER", prompt)
        payload = math_benchmark.report_payload([], {})
        self.assertEqual(
            math_benchmark.PROMPT_VERSION,
            payload["benchmark_parameters"]["prompt_version"],
        )

    def test_judge_uses_separate_system_instructions(self) -> None:
        """Keep evaluator policy separate from untrusted candidate content."""
        response = {
            "choices": [{"message": {"content": '{"verdict":"yes"}'}}]
        }
        judge = benchmark.JudgeClient("judge")
        with patch.object(judge, "_request", return_value=response) as request:
            self.assertEqual("yes", judge._judge("candidate prompt"))
        messages = request.call_args.args[0]["messages"]
        self.assertEqual(["system", "user"], [message["role"] for message in messages])
        self.assertIn("untrusted data", messages[0]["content"])


class TestReviewScoring(unittest.TestCase):
    """Verify benchmark invariants, structured parsing, and scoring."""

    def test_benchmark_matrix_has_expected_dimensions(self) -> None:
        """Lock the configured six models and two groups of ten cases."""
        self.assertEqual(6, len(benchmark.MODELS))
        self.assertEqual(10, len(benchmark.QUALITY_CASES))
        self.assertEqual(10, len(benchmark.SECURITY_CASES))
        self.assertEqual(20, len(benchmark.CASES))
        self.assertEqual(6, len(set(benchmark.MODELS)))

    def test_hard_timeout_enforces_total_wall_clock(self) -> None:
        """Interrupt work even when an underlying operation remains active."""
        with (
            self.assertRaises(benchmark.TotalRequestTimeout),
            benchmark.hard_timeout(0.01),
        ):
            time.sleep(0.1)

    def test_parse_structured_findings(self) -> None:
        """Parse the required JSON-array response format."""
        response = json.dumps([
            {
                "issue": "CWE-89 SQL injection",
                "affected_code": "query",
                "impact": "database compromise",
                "remediation": "Use a parameterized query",
            }
        ])
        findings = benchmark.parse_findings(response)
        self.assertEqual(1, len(findings))
        self.assertIn("parameterized query", findings[0])

    def test_full_credit_for_complete_supported_response(self) -> None:
        """Award full credit when all expected concepts are supported."""
        case = benchmark.SECURITY_CASES[0]
        response = json.dumps([
            {
                "issue": "CWE-89 SQL injection",
                "affected_code": "interpolated query",
                "impact": "attacker-controlled SQL",
                "remediation": "Use a parameterized query",
            }
        ])
        result = benchmark.score_response("model", case, metrics(response))
        self.assertEqual(100.0, result.score)
        self.assertEqual(100.0, result.recall)
        self.assertEqual(100.0, result.precision)

    def test_unsupported_finding_reduces_precision(self) -> None:
        """Penalize hallucinated findings that match no reference criterion."""
        case = benchmark.SECURITY_CASES[0]
        response = json.dumps([
            {
                "issue": "CWE-89 SQL injection",
                "affected_code": "query",
                "impact": "database compromise",
                "remediation": "Use a parameterized query",
            },
            {
                "issue": "Race condition",
                "affected_code": "function",
                "impact": "none demonstrated",
                "remediation": "Add a mutex",
            },
        ])
        result = benchmark.score_response("model", case, metrics(response))
        self.assertEqual(1, result.unsupported_findings)
        self.assertLess(result.precision, 100.0)
        self.assertLess(result.score, 100.0)

    def test_unstructured_response_is_scored_without_crashing(self) -> None:
        """Treat malformed output as one finding rather than losing the case."""
        case = benchmark.SECURITY_CASES[0]
        result = benchmark.score_response(
            "model", case, metrics("SQL injection; use parameterized SQL")
        )
        self.assertGreater(result.score, 0.0)


class TestResultPersistence(unittest.TestCase):
    """Verify result output is valid and atomically finalized."""

    def test_default_paths_use_dedicated_project_directories(self) -> None:
        """Keep configuration, results, logs, and archives out of the project root."""
        self.assertEqual(
            benchmark.MODELS, benchmark.load_models(paths.CONFIG_DIR / "models.json")
        )
        self.assertEqual(paths.GALILEO_REVIEW_RESULTS_DIR, benchmark.RESULTS_PATH.parent)
        self.assertEqual(paths.LOGS_DIR / "galileo-review.log", benchmark.LOG_PATH)
        self.assertEqual(paths.GALILEO_MATH_RESULTS_DIR, math_benchmark.RESULTS_PATH.parent)
        self.assertEqual(paths.LOGS_DIR / "galileo-math.log", math_benchmark.LOG_PATH)

    def test_report_paths_differ_per_language(self) -> None:
        """Keep per-language artifacts apart; python keeps legacy names."""
        rust_paths = benchmark.report_paths_for("rust")
        self.assertEqual(
            "galileo-review-results-rust.json", rust_paths[0].name
        )
        self.assertEqual("galileo-review-rust.log", rust_paths[3].name)
        python_paths = benchmark.report_paths_for("python")
        self.assertEqual("galileo-review-results.json", python_paths[0].name)

    def test_save_results_writes_valid_json(self) -> None:
        """Write summaries and leave no temporary file behind."""
        case = benchmark.QUALITY_CASES[0]
        result = benchmark.score_response(
            benchmark.MODELS[0],
            case,
            metrics("resource leak; use with open; add type hints and stream the file"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_path = root / "results" / "review" / "results.json"
            csv_path = root / "results" / "review" / "results.csv"
            report_path = root / "results" / "review" / "results.md"
            with (
                patch.object(benchmark, "RESULTS_PATH", output_path),
                patch.object(benchmark, "CSV_PATH", csv_path),
                patch.object(benchmark, "REPORT_PATH", report_path),
            ):
                benchmark.save_reports([result], {})
            parsed = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(parsed["results"]))
            self.assertEqual(
                benchmark.PROMPT_VERSION,
                parsed["benchmark_parameters"]["prompt_version"],
            )
            self.assertTrue(csv_path.exists())
            self.assertTrue(report_path.exists())
            self.assertFalse(output_path.with_suffix(".json.tmp").exists())


class TestPresetParsing(unittest.TestCase):
    """Verify client-side preset keys parse into request payload fields."""

    def _write_presets(self, body: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "presets.ini"
        path.write_text(body, encoding="utf-8")
        return path

    def test_thinking_keys_parse_with_typed_values(self) -> None:
        """thinking-budget and enable-thinking survive INI parsing typed."""
        path = self._write_presets(
            "[coder-ornith:LATEST]\n"
            "temp = 0.7\n"
            "top-k = 40\n"
            "thinking-budget = 8192\n"
            "enable-thinking = true\n"
            "max-tokens = 4096\n"
        )
        presets = benchmark.load_presets(path)
        sampling = presets["coder-ornith:latest"]
        self.assertEqual(8192, sampling["thinking_budget_tokens"])
        self.assertIs(sampling["enable_thinking"], True)
        self.assertEqual(4096, sampling["max_tokens"])
        self.assertEqual(40, sampling["top_k"])
        self.assertAlmostEqual(0.7, sampling["temperature"])

    def test_enable_thinking_false_parses_as_bool(self) -> None:
        """Boolean-off presets parse false instead of being dropped."""
        path = self._write_presets(
            "[coder-gemma4-26B-A4B-it:LATEST]\nenable-thinking = false\n"
        )
        presets = benchmark.load_presets(path)
        self.assertIs(
            presets["coder-gemma4-26b-a4b-it:latest"]["enable_thinking"], False
        )

    def test_math_runner_shares_the_typed_preset_map(self) -> None:
        """The math runner accepts the same thinking keys on Galileo."""
        path = self._write_presets(
            "[coder-ornith:LATEST]\n"
            "thinking-budget = 4096\n"
            "enable-thinking = false\n"
            "max-tokens = 2048\n"
        )
        presets = math_benchmark.load_presets(path)
        sampling = presets["coder-ornith:latest"]
        self.assertEqual(4096, sampling["thinking_budget_tokens"])
        self.assertIs(sampling["enable_thinking"], False)
        self.assertEqual(2048, sampling["max_tokens"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
