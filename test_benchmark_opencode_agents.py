"""Offline tests for the OpenCode agent benchmark harness."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock, call, patch

import benchmark_opencode_agents as benchmark
import benchmark_paths as paths


class TestOpenCodeEvents(unittest.TestCase):
    """Validate parsing of real OpenCode JSON event structures."""

    def test_session_modes_support_fresh_continuing_and_both(self) -> None:
        """Parse ordered unique modes and reject unknown values."""
        self.assertEqual(("fresh",), benchmark.load_session_modes("fresh"))
        self.assertEqual(
            ("continuing", "fresh"),
            benchmark.load_session_modes("continuing,fresh,continuing"),
        )
        with self.assertRaises(ValueError):
            benchmark.load_session_modes("unknown")

    def test_parse_events_extracts_tools_text_and_tokens(self) -> None:
        """Aggregate tool calls, final text, and step token accounting."""
        events = [
            {
                "type": "step_start",
                "sessionID": "ses_test",
                "part": {"type": "step-start"},
            },
            {
                "type": "tool_use",
                "sessionID": "ses_test",
                "part": {
                    "type": "tool",
                    "tool": "serena_read_file",
                    "state": {"status": "completed"},
                },
            },
            {
                "type": "tool_use",
                "sessionID": "ses_test",
                "part": {
                    "type": "tool",
                    "tool": "headroom_headroom_compress",
                    "state": {"status": "completed"},
                },
            },
            {
                "type": "text",
                "sessionID": "ses_test",
                "part": {"type": "text", "text": "[{\"issue\": \"test\"}]"},
            },
            {
                "type": "step_finish",
                "sessionID": "ses_test",
                "part": {
                    "type": "step-finish",
                    "tokens": {
                        "input": 200,
                        "output": 50,
                        "reasoning": 10,
                        "cache": {"read": 1000, "write": 20},
                    },
                },
            },
        ]
        stdout = "\n".join(json.dumps(event) for event in events)
        result = benchmark.parse_events(stdout, "", 0, 2.5)
        self.assertEqual("ses_test", result.session_id)
        self.assertEqual(200, result.input_tokens)
        self.assertEqual(50, result.output_tokens)
        self.assertEqual(1000, result.cache_read_tokens)
        expected_tools = ("serena_read_file", "headroom_headroom_compress")
        self.assertEqual(expected_tools, result.tools)
        self.assertEqual(expected_tools, result.successful_tools)

    def test_parse_events_counts_lazy_loader_semantic_errors(self) -> None:
        """Treat completed lazy-loader responses reporting unknown tools as errors."""
        event = {
            "type": "tool_use",
            "sessionID": "ses_test",
            "part": {
                "type": "tool",
                "tool": "load_tool",
                "state": {
                    "status": "completed",
                    "output": 'No instructions found for "serena".',
                },
            },
        }
        result = benchmark.parse_events(json.dumps(event), "", 0, 1.0)
        self.assertEqual(1, result.tool_errors)
        self.assertEqual((), result.successful_tools)

    def test_parse_events_rejects_completed_mcp_error_payload(self) -> None:
        """Do not count completed MCP responses containing an error as successful."""
        event = {
            "type": "tool_use",
            "sessionID": "ses_test",
            "part": {
                "type": "tool",
                "tool": "serena_list_dir",
                "state": {
                    "status": "completed",
                    "output": json.dumps({"error": "Directory not found"}),
                },
            },
        }
        result = benchmark.parse_events(json.dumps(event), "", 0, 1.0)
        self.assertEqual(1, result.tool_errors)
        self.assertEqual((), result.successful_tools)

    def test_command_and_prompt_disable_plugin_loading(self) -> None:
        """Use pure mode and name the required MCP tools without contradiction."""
        case = benchmark.CASES[0]
        command = benchmark.build_command("model", case, "ses_test")
        prompt = command[-1]
        self.assertIn("--pure", command)
        self.assertEqual("ses_test", command[command.index("--session") + 1])
        self.assertIn("`serena_read_file`", prompt)
        self.assertIn("`headroom_headroom_compress`", prompt)
        self.assertIn("candidate root causes and exact affected code", prompt)
        self.assertIn("Then make no further tool calls", prompt)
        self.assertIn("Prefer fewer high-confidence findings", prompt)
        self.assertNotIn("Do not call tools", prompt)

    def test_environment_isolates_serena_and_restricts_tools(self) -> None:
        """Set the step limit, explicit Serena project, and required-tool allowlist."""
        content = json.dumps({"agent": {"other": {"steps": 2}}})
        with patch.dict("os.environ", {"OPENCODE_CONFIG_CONTENT": content}, clear=False):
            environment = benchmark.build_environment()
        configuration = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(2, configuration["agent"]["other"]["steps"])
        reviewer = configuration["agent"]["benchmark-reviewer"]
        self.assertEqual(benchmark.MAX_AGENT_STEPS, reviewer["steps"])
        serena_command = configuration["mcp"]["serena"]["command"]
        self.assertEqual(list(benchmark.SERENA_COMMAND), serena_command)
        self.assertEqual(
            str(benchmark.WORKSPACE),
            serena_command[serena_command.index("--project") + 1],
        )
        permissions = reviewer["permission"]
        self.assertEqual("deny", permissions["*"])
        self.assertEqual("allow", permissions["serena_read_file"])
        self.assertEqual("allow", permissions["headroom_headroom_compress"])

    def test_score_requires_both_mcp_families(self) -> None:
        """Award full tool compliance only when Serena and Headroom are used."""
        case = benchmark.CASES[0]
        response = json.dumps([
            {
                "issue": "Resource leak and missing type hints",
                "affected_code": "open(path)",
                "impact": "The file is not closed and lines are materialized in memory",
                "remediation": "Use with open, iterate over the stream, and add annotations",
            }
        ])
        run = benchmark.OpenCodeRun(
            session_id="ses_test",
            response=response,
            tools=("serena_read_file", "headroom_headroom_compress"),
            successful_tools=(
                "serena_read_file",
                "serena_read_file",
                "headroom_headroom_compress",
            ),
            tool_errors=0,
            elapsed_seconds=1.0,
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=0,
            cache_read_tokens=1000,
            cache_write_tokens=0,
            exit_code=0,
            stderr="",
        )
        result = benchmark.score_run("model", case, run)
        self.assertEqual(100.0, result.tool_score)
        self.assertTrue(result.serena_used)
        self.assertTrue(result.headroom_used)

    def test_task_score_logging_includes_detailed_metrics(self) -> None:
        """Expose task findings and running model aggregates in terminal logs."""
        model = benchmark.MODELS[0]
        result = replace(
            benchmark.failed_result(model, benchmark.CASES[0], "", "placeholder"),
            content_score=80.0,
            tool_score=100.0,
            overall_score=84.0,
            recall=66.67,
            precision=100.0,
            matched_findings=("resource management",),
            missed_findings=("missing typing",),
            unsupported_findings=0,
            error=None,
        )
        with self.assertLogs(benchmark.LOGGER.name, level="INFO") as captured:
            benchmark.log_case_score(result, 1, len(benchmark.CASES), 10.25)
            benchmark.log_model_score("continuing", model, [result])
        output = "\n".join(captured.output)
        self.assertIn("overall=84.00", output)
        self.assertIn("recall=66.67", output)
        self.assertIn("matched=resource management", output)
        self.assertIn("missed=missing typing", output)
        self.assertIn("scoring_seconds=10.25", output)
        self.assertIn("Model score", output)

    def test_score_short_circuits_control_and_missing_tool_responses(self) -> None:
        """Reject invalid agent runs without spending a judge request."""
        run = benchmark.OpenCodeRun(
            session_id="ses_test",
            response=f"{benchmark.MAX_STEPS_SENTINEL}\nDetails",
            tools=("serena_read_file",),
            successful_tools=("serena_read_file",),
            tool_errors=0,
            elapsed_seconds=1.0,
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=0,
            cache_read_tokens=1000,
            cache_write_tokens=0,
            exit_code=0,
            stderr="",
        )
        with patch.object(benchmark, "score_response") as scorer:
            sentinel = benchmark.score_run("model", benchmark.CASES[0], run)
            missing = benchmark.score_run(
                "model",
                benchmark.CASES[0],
                replace(run, response="[]"),
            )
        scorer.assert_not_called()
        self.assertIn("maximum agent step limit", sentinel.error or "")
        self.assertIn("headroom_headroom_compress", missing.error or "")
        self.assertEqual(0.0, sentinel.overall_score)
        self.assertEqual(0.0, missing.overall_score)


class TestOpenCodeFailures(unittest.TestCase):
    """Validate diagnostic preservation and failure control flow."""

    def test_failed_result_preserves_partial_run(self) -> None:
        """Retain timeout session, tools, timing, tokens, and partial output."""
        run = benchmark.OpenCodeRun(
            session_id="ses_partial",
            response="partial",
            tools=("serena_read_file", "load_tool"),
            successful_tools=("serena_read_file",),
            tool_errors=1,
            elapsed_seconds=360.25,
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=5,
            cache_read_tokens=900,
            cache_write_tokens=10,
            exit_code=-15,
            stderr="terminated",
        )
        result = benchmark.failed_result(
            "model", benchmark.CASES[0], "", "timeout", run
        )
        self.assertEqual("ses_partial", result.session_id)
        self.assertEqual(360.25, result.elapsed_seconds)
        self.assertEqual(100, result.input_tokens)
        self.assertEqual(("serena_read_file", "load_tool"), result.tools)
        self.assertEqual(("serena_read_file",), result.successful_tools)
        self.assertFalse(result.serena_used)
        self.assertFalse(result.headroom_used)
        self.assertEqual("partial", result.response)

    def test_timeout_exception_carries_partial_run(self) -> None:
        """Parse accumulated events before propagating a timeout."""
        event = json.dumps(
            {
                "type": "step_start",
                "sessionID": "ses_partial",
                "part": {"type": "step-start"},
            }
        )
        process = Mock(pid=123, returncode=-15)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("opencode", 1),
            (event, "terminated"),
        ]
        with (
            patch.object(benchmark.subprocess, "Popen", return_value=process),
            patch.object(benchmark.os, "killpg"),
            patch.object(benchmark, "append_events"),
            patch.object(benchmark.time, "perf_counter", side_effect=[1.0, 3.0]),
            patch.object(benchmark, "CASE_TIMEOUT_SECONDS", 1.0),
            self.assertRaises(benchmark.OpenCodeExecutionError) as context,
        ):
            benchmark.OpenCodeRunner().run("model", benchmark.CASES[0], None)
        self.assertEqual("ses_partial", context.exception.run.session_id)
        self.assertEqual(2.0, context.exception.run.elapsed_seconds)

    def test_main_retries_failure_replaces_row_and_stops_after_preflight(self) -> None:
        """Retry failed state without duplicates and defer a model after preflight failure."""
        model = "model"
        cases = benchmark.CASES[:2]
        previous = benchmark.failed_result(model, cases[0], "", "old timeout")
        partial = benchmark.OpenCodeRun(
            session_id="ses_partial",
            response="",
            tools=("serena_read_file",),
            successful_tools=("serena_read_file",),
            tool_errors=0,
            elapsed_seconds=2.0,
            input_tokens=10,
            output_tokens=2,
            reasoning_tokens=0,
            cache_read_tokens=5,
            cache_write_tokens=0,
            exit_code=-15,
            stderr="terminated",
        )
        runner = Mock()
        runner.run.side_effect = benchmark.OpenCodeExecutionError("timeout", partial)
        saved: list[tuple[list[benchmark.AgentCaseResult], dict[str, str]]] = []

        def capture_reports(
            results: list[benchmark.AgentCaseResult], sessions: dict[str, str]
        ) -> None:
            saved.append((list(results), dict(sessions)))

        with (
            patch.object(benchmark, "MODELS", (model,)),
            patch.object(benchmark, "CASES", cases),
            patch.object(benchmark, "SESSION_MODES", ("continuing",)),
            patch.object(benchmark, "RETRY_FAILURES", True),
            patch.object(benchmark, "configure_logging"),
            patch.object(benchmark, "materialize_fixtures"),
            patch.object(benchmark, "load_state", return_value=([previous], {})),
            patch.object(benchmark, "OpenCodeRunner", return_value=runner),
            patch.object(benchmark, "save_reports", side_effect=capture_reports),
        ):
            exit_code = benchmark.main([])

        self.assertEqual(1, exit_code)
        runner.run.assert_called_once_with(model, cases[0], None, "continuing")
        self.assertEqual(1, len(saved[-1][0]))
        self.assertEqual("ses_partial", saved[-1][0][0].session_id)
        self.assertEqual("continuing", saved[-1][0][0].session_mode)
        self.assertEqual({f"continuing:{model}": "ses_partial"}, saved[-1][1])

    def test_main_runs_continuing_and_fresh_session_modes(self) -> None:
        """Reuse only continuing sessions while starting every fresh case anew."""
        model = "model"
        cases = benchmark.CASES[:2]
        run = benchmark.OpenCodeRun(
            session_id="ses_generated",
            response="[]",
            tools=(
                "serena_read_file",
                "serena_read_file",
                "headroom_headroom_compress",
            ),
            successful_tools=(
                "serena_read_file",
                "serena_read_file",
                "headroom_headroom_compress",
            ),
            tool_errors=0,
            elapsed_seconds=2.0,
            input_tokens=10,
            output_tokens=20,
            reasoning_tokens=0,
            cache_read_tokens=5,
            cache_write_tokens=0,
            exit_code=0,
            stderr="",
        )
        runner = Mock()
        runner.run.return_value = run

        def score(
            selected_model: str,
            case: benchmark.ReviewCase,
            selected_run: benchmark.OpenCodeRun,
            session_mode: str,
        ) -> benchmark.AgentCaseResult:
            return replace(
                benchmark.failed_result(
                    selected_model,
                    case,
                    selected_run.session_id,
                    "placeholder",
                    selected_run,
                    session_mode=session_mode,
                ),
                error=None,
            )

        saved: list[list[benchmark.AgentCaseResult]] = []
        with (
            patch.object(benchmark, "MODELS", (model,)),
            patch.object(benchmark, "CASES", cases),
            patch.object(benchmark, "SESSION_MODES", ("continuing", "fresh")),
            patch.object(benchmark, "configure_logging"),
            patch.object(benchmark, "materialize_fixtures"),
            patch.object(benchmark, "load_state", return_value=([], {})),
            patch.object(benchmark, "OpenCodeRunner", return_value=runner),
            patch.object(benchmark, "score_run", side_effect=score),
            patch.object(
                benchmark,
                "save_reports",
                side_effect=lambda results, _sessions: saved.append(list(results)),
            ),
        ):
            self.assertEqual(0, benchmark.main([]))

        self.assertEqual(
            [
                call(model, cases[0], None, "continuing"),
                call(model, cases[1], "ses_generated", "continuing"),
                call(model, cases[0], None, "fresh"),
                call(model, cases[1], None, "fresh"),
            ],
            runner.run.call_args_list,
        )
        self.assertEqual(4, len(saved[-1]))
        self.assertEqual({"continuing", "fresh"}, {row.session_mode for row in saved[-1]})


class TestOpenCodeReports(unittest.TestCase):
    """Validate persistent agent report formats."""

    def test_default_paths_separate_results_and_logs(self) -> None:
        """Keep OpenCode measurements together and operational logs separate."""
        self.assertEqual(paths.OPENCODE_AGENT_RESULTS_DIR, benchmark.RESULTS_PATH.parent)
        self.assertEqual(paths.OPENCODE_AGENT_RESULTS_DIR, benchmark.EVENTS_PATH.parent)
        self.assertEqual(paths.LOGS_DIR, benchmark.LOG_PATH.parent)

    def test_load_state_migrates_legacy_continuing_results(self) -> None:
        """Preserve active schema-four results created before dual-mode reporting."""
        model = benchmark.MODELS[0]
        legacy = asdict(
            benchmark.failed_result(model, benchmark.CASES[0], "ses_test", "timeout")
        )
        legacy.pop("session_mode")
        legacy.pop("agent_end_to_end_tokens_per_second")
        legacy["elapsed_seconds"] = 2.0
        legacy["output_tokens"] = 10
        payload = {
            "schema_version": benchmark.SCHEMA_VERSION,
            "results": [legacy],
            "sessions": {model: "ses_test"},
        }
        with tempfile.TemporaryDirectory() as directory:
            results_path = Path(directory) / "results.json"
            results_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(benchmark, "RESULTS_PATH", results_path):
                results, sessions = benchmark.load_state()
        self.assertEqual("continuing", results[0].session_mode)
        self.assertEqual(5.0, results[0].agent_end_to_end_tokens_per_second)
        self.assertEqual({f"continuing:{model}": "ses_test"}, sessions)

    def test_reports_are_created_atomically(self) -> None:
        """Write JSON, CSV, and Markdown from one deterministic failure row."""
        result = benchmark.failed_result(
            benchmark.MODELS[0], benchmark.CASES[0], "ses_test", "timeout"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "results" / "opencode" / "results.json"
            csv_path = root / "results" / "opencode" / "results.csv"
            markdown_path = root / "results" / "opencode" / "results.md"
            with (
                patch.object(benchmark, "RESULTS_PATH", json_path),
                patch.object(benchmark, "CSV_PATH", csv_path),
                patch.object(benchmark, "REPORT_PATH", markdown_path),
            ):
                benchmark.save_reports([result], {benchmark.MODELS[0]: "ses_test"})
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(payload["results"]))
            self.assertEqual(
                benchmark.PROMPT_VERSION,
                payload["configuration"]["review_prompt_version"],
            )
            self.assertTrue(csv_path.exists())
            self.assertIn("OpenCode + Serena + Headroom", markdown_path.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
