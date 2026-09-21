"""Offline unit tests for the oMLX review benchmark client and reporting."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import benchmark_omlx_reviews as benchmark
import benchmark_paths as paths


def sse_stream(chunks: list[dict[str, Any]], done: bool = True) -> io.BytesIO:
    """Build an SSE byte stream matching the oMLX chunk format."""
    data = b"".join(
        f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks
    )
    if done:
        data += b"data: [DONE]\n\n"
    return io.BytesIO(data)


class TestOmlxStreaming(unittest.TestCase):
    """Verify SSE accumulation for content, reasoning, and terminal usage."""

    def test_collect_stream_accumulates_deltas_and_usage(self) -> None:
        """Assemble split deltas and keep the final include_usage chunk."""
        stream = sse_stream([
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"reasoning_content": "checking "}}]},
            {"choices": [{"delta": {"reasoning_content": "code"}}]},
            {"choices": [{"delta": {"content": "[{\"issue\":"}}]},
            {"choices": [{"delta": {"content": " \"x\"}]"}}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "prompt_eval_duration": 0.5,
                    "generation_duration": 2.0,
                    "prompt_tokens_per_second": 200.0,
                    "generation_tokens_per_second": 20.0,
                },
            },
        ])
        content, reasoning, usage = benchmark.OmlxClient._collect_stream(stream)
        self.assertEqual('[{"issue": "x"}]', content)
        self.assertEqual("checking code", reasoning)
        self.assertEqual(100, usage["prompt_tokens"])
        self.assertEqual(20.0, usage["generation_tokens_per_second"])

    def test_collect_stream_skips_keepalives_and_done(self) -> None:
        """Ignore SSE comments and blank lines; stop at the DONE sentinel."""
        stream = io.BytesIO(
            b": keepalive\n\n"
            b'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
            b"data: [DONE]\n\n"
            b'data: {"choices": [{"delta": {"content": "late"}}]}\n\n'
        )
        content, reasoning, usage = benchmark.OmlxClient._collect_stream(stream)
        self.assertEqual("ok", content)
        self.assertEqual("", reasoning)
        self.assertEqual({}, usage)


class TestOmlxPayload(unittest.TestCase):
    """Verify request construction uses oMLX-native fields only."""

    def test_payload_uses_omlx_fields_and_streaming(self) -> None:
        """Stream with usage and oMLX thinking control; no llama.cpp fields."""
        client = benchmark.OmlxClient()
        with patch.object(benchmark, "THINKING_ENABLED", False):
            payload = client.build_payload("plain-model", benchmark.CASES[0])
        self.assertTrue(payload["stream"])
        self.assertTrue(payload["stream_options"]["include_usage"])
        self.assertIn("thinking_budget", payload)
        self.assertNotIn("timings_per_token", payload)
        self.assertNotIn("t_max_predict_ms", payload)
        self.assertNotIn("thinking_budget_tokens", payload)
        self.assertEqual("json_schema", payload["response_format"]["type"])
        self.assertEqual(0, payload["temperature"])

    def test_schema_forces_thinking_off(self) -> None:
        """Disable thinking under response_format; oMLX emits [] otherwise."""
        client = benchmark.OmlxClient()
        with patch.object(benchmark, "THINKING_ENABLED", False):
            payload = client.build_payload("plain-model", benchmark.CASES[0])
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(0, payload["thinking_budget"])

    def test_thinking_mode_drops_schema(self) -> None:
        """OMLX_THINKING swaps the schema for a bounded thinking budget."""
        client = benchmark.OmlxClient()
        with patch.object(benchmark, "THINKING_ENABLED", True):
            payload = client.build_payload("plain-model", benchmark.CASES[0])
        self.assertNotIn("response_format", payload)
        self.assertTrue(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(
            benchmark.THINKING_BUDGET_TOKENS, payload["thinking_budget"]
        )

    def test_profile_alias_drops_temperature(self) -> None:
        """Let server-side oMLX profiles govern sampling for alias models."""
        client = benchmark.OmlxClient()
        payload = client.build_payload(
            "Devstral-Small-2-24B-Instruct-2512-6bit:devstral-code",
            benchmark.CASES[0],
        )
        self.assertNotIn("temperature", payload)

    def test_no_thinking_models_disables_thinking_per_model(self) -> None:
        """OMLX_NO_THINKING_MODELS keeps schema-free output but drops thinking."""
        client = benchmark.OmlxClient()
        with (
            patch.object(benchmark, "THINKING_ENABLED", True),
            patch.object(
                benchmark, "NO_THINKING_MODELS", frozenset({"bonsai"})
            ),
        ):
            payload = client.build_payload("Bonsai", benchmark.CASES[0])
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(0, payload["thinking_budget"])
        self.assertNotIn("response_format", payload)

    def test_preset_overrides_and_reconciles_enable_thinking(self) -> None:
        """Apply INI sampling and keep the two thinking controls consistent."""
        client = benchmark.OmlxClient(
            {"model-a": {"temperature": 0.6, "enable_thinking": False}}
        )
        payload = client.build_payload("Model-A", benchmark.CASES[0])
        self.assertEqual(0.6, payload["temperature"])
        self.assertFalse(payload["enable_thinking"])
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])


class TestOmlxPresets(unittest.TestCase):
    """Verify oMLX-native preset parsing and value coercion."""

    def test_preset_keys_map_to_omlx_request_fields(self) -> None:
        """Map llama-style INI keys onto oMLX request field names and types."""
        with tempfile.TemporaryDirectory() as directory:
            ini_path = Path(directory) / "presets.ini"
            ini_path.write_text(
                "[Model-A]\n"
                "temp = 0.6\n"
                "repeat-penalty = 1.05\n"
                "thinking-budget = 512\n"
                "enable-thinking = false\n"
                "reasoning-effort = low\n"
                "seed = 7\n",
                encoding="utf-8",
            )
            presets = benchmark.load_presets(ini_path)
        sampling = presets["model-a"]
        self.assertEqual(0.6, sampling["temperature"])
        self.assertEqual(1.05, sampling["repetition_penalty"])
        self.assertEqual(512, sampling["thinking_budget"])
        self.assertFalse(sampling["enable_thinking"])
        self.assertEqual("low", sampling["reasoning_effort"])
        self.assertEqual(7, sampling["seed"])

    def test_shipped_presets_cover_bare_model_ids(self) -> None:
        """Keep presets-omlx.ini parseable and keyed by configured models."""
        presets = benchmark.load_presets(paths.CONFIG_DIR / "presets-omlx.ini")
        configured = {model.casefold() for model in benchmark.MODELS}
        bare_models = {
            model.casefold() for model in benchmark.MODELS if ":" not in model
        }
        self.assertTrue(bare_models <= set(presets))
        self.assertTrue(set(presets) <= configured)


class TestConfigurableJudge(unittest.TestCase):
    """Verify the judge can target an endpoint other than the local server."""

    def test_judge_request_uses_configured_endpoint_and_key(self) -> None:
        """Send judge traffic to the explicit base URL with its own key."""
        judge = benchmark.ConfigurableJudgeClient(
            "critic-ornith:LATEST",
            "http://127.0.0.1:8080/v1",
            "sk-noauth",
            30.0,
        )
        body = io.BytesIO(
            b'{"choices": [{"message": {"content": "{\\"verdict\\": \\"yes\\"}"}}]}'
        )
        response = MagicMock()
        response.__enter__.return_value = body
        with patch(
            "benchmark_omlx_reviews.urllib.request.urlopen",
            return_value=response,
        ) as mock_open:
            parsed = judge._request({"model": "critic-ornith:LATEST"})
        sent = mock_open.call_args.args[0]
        self.assertEqual(
            "http://127.0.0.1:8080/v1/chat/completions",
            sent.full_url,
        )
        self.assertEqual("Bearer sk-noauth", sent.headers["Authorization"])
        self.assertIn("choices", parsed)


class TestOmlxMetrics(unittest.TestCase):
    """Verify oMLX usage fields map onto the shared metrics shape."""

    def test_review_maps_usage_to_metrics(self) -> None:
        """Convert oMLX durations to milliseconds and record model load."""
        client = benchmark.OmlxClient()
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "prompt_eval_duration": 0.5,
            "generation_duration": 2.0,
            "prompt_tokens_per_second": 200.0,
            "generation_tokens_per_second": 20.0,
            "model_load_duration": 3.0,
        }
        with patch.object(
            client,
            "_stream_with_retry",
            return_value=('[{"issue": "x"}]', "why", usage),
        ):
            metrics = client.review("model", benchmark.CASES[0])
        self.assertEqual(100, metrics.prompt_tokens)
        self.assertEqual(40, metrics.completion_tokens)
        self.assertEqual(500.0, metrics.prompt_ms)
        self.assertEqual(2000.0, metrics.generation_ms)
        self.assertEqual(200.0, metrics.prompt_tokens_per_second)
        self.assertEqual(20.0, metrics.output_tokens_per_second)
        self.assertEqual(0, metrics.draft_tokens)
        self.assertEqual(3.0, client.last_model_load_seconds)


class TestOmlxReporting(unittest.TestCase):
    """Verify model list, output paths, summaries, and report persistence."""

    def test_models_file_loads_unique_ids(self) -> None:
        """Load the configured oMLX model list without duplicates."""
        models = benchmark.load_models(benchmark.MODELS_PATH)
        self.assertEqual(benchmark.MODELS, models)
        self.assertEqual(len(models), len(set(models)))

    def test_default_paths_use_omlx_directories(self) -> None:
        """Keep oMLX artifacts out of the Galileo result directories."""
        self.assertEqual(paths.OMLX_REVIEW_RESULTS_DIR, benchmark.RESULTS_PATH.parent)
        self.assertEqual(paths.LOGS_DIR / "omlx-review.log", benchmark.LOG_PATH)

    def test_summarize_uses_omlx_models(self) -> None:
        """Aggregate only models from the oMLX model list."""
        result = benchmark.failed_result(
            benchmark.MODELS[0], benchmark.CASES[0], "boom"
        )
        summaries = benchmark.summarize([result])
        self.assertEqual(1, len(summaries))
        self.assertEqual(
            1.0, summaries[benchmark.MODELS[0]]["failed_cases"]
        )

    def test_save_results_writes_valid_json(self) -> None:
        """Write summaries and leave no temporary file behind."""
        case = next(
            case for case in benchmark.CASES if case.category == "quality"
        )
        metrics = benchmark.ResponseMetrics(
            text="resource leak; use with open; add type hints and stream the file",
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
        result = benchmark.score_response(
            benchmark.MODELS[0], case, metrics
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
            self.assertEqual("omlx", parsed["benchmark_parameters"]["backend"])
            self.assertEqual(
                benchmark.PROMPT_VERSION,
                parsed["benchmark_parameters"]["prompt_version"],
            )
            self.assertTrue(csv_path.exists())
            self.assertTrue(report_path.exists())
            self.assertFalse(output_path.with_suffix(".json.tmp").exists())


class TestCaseDefinitions(unittest.TestCase):
    """Verify static JSON case definitions load and stay consistent."""

    def test_python_definitions_match_galileo_cases(self) -> None:
        """Guard test_definitions/python.json against drifting from CASES."""
        import benchmark_galileo_reviews as galileo

        loaded = benchmark.load_case_definitions(
            paths.TEST_DEFINITIONS_DIR / "python.json"
        )
        self.assertEqual(
            galileo.CASES,
            loaded,
        )
        self.assertEqual(galileo.CASES, benchmark.CASES)

    def test_load_definitions_rejects_malformed_file(self) -> None:
        """Reject definition files that fail the schema."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                json.dumps({"language": "x", "cases": [{"case_id": "q"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                benchmark.load_case_definitions(path)

    def test_load_definitions_missing_file(self) -> None:
        """Raise FileNotFoundError for an unknown language file."""
        with self.assertRaises(FileNotFoundError):
            benchmark.load_case_definitions(
                paths.TEST_DEFINITIONS_DIR / "cobol.json"
            )

    def test_language_files_parse_and_tag_cases(self) -> None:
        """Every shipped language file yields language-tagged valid cases."""
        for language in benchmark.available_languages():
            path = paths.TEST_DEFINITIONS_DIR / f"{language}.json"
            cases = benchmark.load_case_definitions(path)
            with self.subTest(language=path.stem):
                self.assertGreaterEqual(len(cases), 10)
                self.assertTrue(
                    all(case.language == path.stem for case in cases)
                )
                self.assertTrue(
                    all(
                        case.category in {"quality", "security"}
                        for case in cases
                    )
                )
                self.assertTrue(all(case.findings for case in cases))

    def test_report_paths_differ_per_language(self) -> None:
        """Keep per-language result files apart; python keeps legacy names."""
        python_paths = benchmark.report_paths_for("python")
        rust_paths = benchmark.report_paths_for("rust")
        self.assertNotEqual(python_paths[0], rust_paths[0])
        self.assertEqual(
            "omlx-review-results-rust.json", rust_paths[0].name
        )
        self.assertEqual("omlx-review-rust.log", rust_paths[3].name)
        self.assertEqual("omlx-review-results.json", python_paths[0].name)

    def test_report_records_language(self) -> None:
        """Record the selected language and case counts in the payload."""
        rust_cases = benchmark.load_case_definitions(
            paths.TEST_DEFINITIONS_DIR / "rust.json"
        )
        payload = benchmark.report_payload(
            [], {}, language="rust", cases=rust_cases
        )
        parameters = payload["benchmark_parameters"]
        self.assertEqual("rust", parameters["language"])
        self.assertEqual(len(rust_cases), parameters["cases"])
        self.assertEqual(
            sum(c.category == "quality" for c in rust_cases),
            parameters["quality_cases"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
