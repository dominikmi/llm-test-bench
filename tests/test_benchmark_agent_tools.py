"""Offline unit tests for benchmark_agent_tools: suite loading, mock
dispatch, schema validation, call classification, and answer grading."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from modules import benchmark_agent_tools as tools

SUITE = tools.load_suite()


class SuiteLoadingTests(unittest.TestCase):
    """The bundled suite must validate and stay internally consistent."""

    def test_suite_loads(self) -> None:
        self.assertEqual(SUITE.suite, "tool-use")
        self.assertEqual(len(SUITE.cases), 10)
        self.assertGreaterEqual(len(SUITE.tools), 15)

    def test_rejects_unknown_tool_reference(self) -> None:
        raw = json.loads(Path(tools.SUITE_PATH).read_text())
        raw["cases"][0]["available_tools"].append("nonexistent_tool")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as handle:
            json.dump(raw, handle)
            temp_path = Path(handle.name)
        try:
            with self.assertRaises(ValueError):
                tools.load_suite(temp_path)
        finally:
            temp_path.unlink()

    def test_rejects_extra_fields(self) -> None:
        raw = json.loads(Path(tools.SUITE_PATH).read_text())
        raw["cases"][0]["bogus"] = True
        with self.assertRaises(ValueError):
            tools.ToolUseSuite.model_validate(raw)


class ArgumentValidationTests(unittest.TestCase):
    """Schema subset validator used before mock dispatch."""

    schema: ClassVar = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        },
        "required": ["city", "unit"],
    }

    def test_valid_args_pass(self) -> None:
        args = {"city": "Oslo", "unit": "fahrenheit", "date": "2026-09-14"}
        self.assertIsNone(tools.validate_arguments(self.schema, args))

    def test_missing_required(self) -> None:
        error = tools.validate_arguments(self.schema, {"city": "Oslo"})
        self.assertIn("unit", error or "")

    def test_enum_violation(self) -> None:
        args = {"city": "Oslo", "unit": "kelvin"}
        self.assertIsNotNone(tools.validate_arguments(self.schema, args))

    def test_pattern_violation(self) -> None:
        args = {"city": "Oslo", "unit": "celsius", "date": "14/09/2026"}
        self.assertIsNotNone(tools.validate_arguments(self.schema, args))

    def test_wrong_type(self) -> None:
        args = {"city": 42, "unit": "celsius"}
        self.assertIsNotNone(tools.validate_arguments(self.schema, args))

    def test_extra_args_permitted(self) -> None:
        args = {"city": "Oslo", "unit": "celsius", "verbose": True}
        self.assertIsNone(tools.validate_arguments(self.schema, args))


class MockDispatchTests(unittest.TestCase):
    """Stateless rule matching: first hit wins, '*' is the catch-all."""

    def setUp(self) -> None:
        self.dispatcher = tools.MockDispatcher({
            "get_product_price": (
                tools.MockRule(
                    match={"sku": "SKU-2291"},
                    result={"price": 74.25},
                ),
                tools.MockRule(
                    match="*",
                    error={"code": "invalid_params", "message": "bad sku"},
                ),
            )
        })

    def test_exact_match_returns_result(self) -> None:
        out = json.loads(self.dispatcher.dispatch(
            "get_product_price", {"sku": "SKU-2291"}
        ))
        self.assertEqual(out["price"], 74.25)

    def test_catchall_returns_error(self) -> None:
        out = json.loads(self.dispatcher.dispatch(
            "get_product_price", {"sku": "PX-2291"}
        ))
        self.assertEqual(out["error"]["code"], "invalid_params")

    def test_stateless_determinism(self) -> None:
        first = self.dispatcher.dispatch("get_product_price", {"sku": "x"})
        second = self.dispatcher.dispatch("get_product_price", {"sku": "x"})
        self.assertEqual(first, second)

    def test_no_rule_no_catchall(self) -> None:
        dispatcher = tools.MockDispatcher({"t": (tools.MockRule(
            match={"a": 1}, result={"ok": True}
        ),)})
        out = json.loads(dispatcher.dispatch("t", {"a": 2}))
        self.assertEqual(out["error"]["code"], "no_mock")


class ClassificationTests(unittest.TestCase):
    """Call classifier: forbidden/invalid/identical_retry/off_plan/valid."""

    schemas: ClassVar = {
        "read_file": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "send_message": {
            "type": "object",
            "properties": {"body": {"type": "string"}},
            "required": ["body"],
        },
    }

    def classify(self, name, args, seen, plans=("read_file",), forbidden=()):
        return tools._classify_call(
            name,
            args,
            self.schemas,
            seen,
            frozenset(plans),
            frozenset(forbidden),
        )

    def test_valid_call(self) -> None:
        cls, _ = self.classify("read_file", {"path": "/x"}, set())
        self.assertEqual(cls, "valid")

    def test_forbidden(self) -> None:
        cls, _ = self.classify(
            "send_message", {"body": "x"}, set(), forbidden=("send_message",)
        )
        self.assertEqual(cls, "forbidden")

    def test_unknown_tool_is_invalid(self) -> None:
        cls, error = self.classify("hack", {}, set())
        self.assertEqual(cls, "invalid")
        self.assertIn("unknown tool", error or "")

    def test_schema_error_is_invalid(self) -> None:
        cls, _ = self.classify("read_file", {}, set())
        self.assertEqual(cls, "invalid")

    def test_identical_retry(self) -> None:
        seen: set[tuple[str, str]] = set()
        self.classify("read_file", {"path": "/x"}, seen)
        cls, _ = self.classify("read_file", {"path": "/x"}, seen)
        self.assertEqual(cls, "identical_retry")

    def test_different_args_not_retry(self) -> None:
        seen: set[tuple[str, str]] = set()
        self.classify("read_file", {"path": "/x"}, seen)
        cls, _ = self.classify("read_file", {"path": "/y"}, seen)
        self.assertEqual(cls, "valid")

    def test_off_plan(self) -> None:
        cls, _ = self.classify("send_message", {"body": "x"}, set())
        self.assertEqual(cls, "off_plan")


class AnswerGradingTests(unittest.TestCase):
    """Typed answer-field matching with JSON contract and prose fallback."""

    fields: ClassVar = {
        "status": tools.FieldSpec(
            type="enum",
            values=("shipped", "delivered"),
            expect="shipped",
        ),
        "total": tools.FieldSpec(type="number", value=249.50, tolerance=0.01),
        "flag": tools.FieldSpec(type="boolean", value=True),
    }

    def test_json_answer_full_match(self) -> None:
        text = json.dumps({"status": "shipped", "total": 249.5, "flag": True})
        quality, is_json, _ = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)
        self.assertTrue(is_json)

    def test_json_answer_partial(self) -> None:
        text = json.dumps({"status": "delivered", "total": 249.5, "flag": True})
        quality, _, _ = tools._grade_answer(self.fields, text)
        self.assertAlmostEqual(quality, 66.67, places=1)

    def test_json_inside_prose(self) -> None:
        text = 'Here is the answer: {"status": "shipped", "total": 249.5, "flag": true} done.'
        quality, is_json, _ = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)
        self.assertTrue(is_json)

    def test_prose_fallback(self) -> None:
        text = "status: shipped\ntotal: 249.5\nflag: true"
        quality, is_json, _ = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)
        self.assertFalse(is_json)

    def test_number_tolerance(self) -> None:
        text = json.dumps({"status": "shipped", "total": 249.505, "flag": True})
        quality, _, _ = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)

    def test_wrong_answer(self) -> None:
        text = json.dumps({"status": "delivered", "total": 10, "flag": False})
        quality, _, _ = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 0.0)


class SetAndMapGradingTests(unittest.TestCase):
    """Set equality, enum spelling variants, and map leaf grading."""

    def test_set_exact_match(self) -> None:
        spec = tools.FieldSpec(type="set", values=("alice", "bob", "dave"))
        self.assertTrue(tools._field_matches(spec, ["Bob", "Dave", "Alice"]))

    def test_set_rejects_subset(self) -> None:
        spec = tools.FieldSpec(type="set", values=("alice", "bob"))
        self.assertFalse(tools._field_matches(spec, ["alice"]))

    def test_set_empty(self) -> None:
        spec = tools.FieldSpec(type="set", values=())
        self.assertTrue(tools._field_matches(spec, []))
        self.assertFalse(tools._field_matches(spec, ["alice"]))

    def test_enum_any_of_spellings(self) -> None:
        spec = tools.FieldSpec(
            type="enum", values=("1", "2"), expect="2",
            any_of=("box2", "box 2", "box_2"),
        )
        self.assertTrue(tools._field_matches(spec, "box 2"))
        self.assertFalse(tools._field_matches(spec, "box 3"))

    def test_map_leaf_partial_credit(self) -> None:
        fields = {
            "houses": tools.FieldSpec(type="map", fields={
                "h1": tools.FieldSpec(type="enum", expect="red"),
                "h2": tools.FieldSpec(type="enum", expect="green"),
            }),
        }
        flat = tools._flatten_fields(fields)
        self.assertEqual(set(flat), {"houses.h1", "houses.h2"})
        text = json.dumps({"houses": {"h1": "red", "h2": "blue"}})
        quality, is_json, _ = tools._grade_answer(fields, text)
        self.assertEqual(quality, 50.0)
        self.assertTrue(is_json)


class LogicSuiteTests(unittest.TestCase):
    """The logic suite must load and carry verified answers."""

    def test_logic_suite_loads(self) -> None:
        suite = tools.load_logic_suite()
        self.assertEqual(suite.suite, "logic")
        self.assertEqual(len(suite.cases), 21)

    def test_every_case_has_fields_and_notes(self) -> None:
        for case in tools.load_logic_suite().cases:
            self.assertTrue(case.answer.fields, case.case_id)
            self.assertTrue(case.notes, case.case_id)


class FakeClient:
    """Duck-typed stand-in for OmlxClient returning canned responses."""

    def __init__(self, responses: list) -> None:
        self._presets: dict = {}
        self._responses = list(responses)
        self.last_model_load_seconds = 0.0
        self.payloads: list = []

    def models(self) -> dict:
        """ChatTransport protocol member; unused by ToolLoop."""
        return {}

    def _request_with_retry(
        self, method: str, path: str, payload: dict | None = None
    ) -> dict:
        self.payloads.append(payload)
        return self._responses.pop(0)


def _chat_response(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _tool_call(name, args, call_id="c1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class LoopTests(unittest.TestCase):
    """End-to-end loop behavior with a fake transport."""

    def test_logic_single_turn(self) -> None:
        case = tools.LogicCase(
            case_id="x", category="deduction", task="who?",
            answer=tools.AnswerSpec(fields={
                "taker": tools.FieldSpec(type="enum", expect="alice"),
            }),
        )
        client = FakeClient([_chat_response(content='{"taker": "alice"}')])
        result = tools.ToolLoop(client, "m").run_logic(case)
        self.assertEqual(result.quality, 100.0)
        self.assertTrue(result.json_answer)
        self.assertEqual(result.calls, 0)
        self.assertNotIn("tools", client.payloads[0])
        self.assertEqual(result.extracted_answer, {"taker": "alice"})
        self.assertEqual(result.answer_text, '{"taker": "alice"}')

    def test_logic_prose_answer_recorded(self) -> None:
        case = tools.LogicCase(
            case_id="x", category="deduction", task="who?",
            answer=tools.AnswerSpec(fields={
                "taker": tools.FieldSpec(type="enum", expect="alice"),
            }),
        )
        client = FakeClient([_chat_response(content="The taker is alice.")])
        result = tools.ToolLoop(client, "m").run_logic(case)
        self.assertFalse(result.json_answer)
        self.assertIsNone(result.extracted_answer)
        self.assertEqual(result.answer_text, "The taker is alice.")

    def test_tool_loop_full_flow(self) -> None:
        suite = tools.load_suite()
        case = next(c for c in suite.cases if c.case_id == "direct-lookup-01")
        client = FakeClient([
            _chat_response(tool_calls=[_tool_call(
                "get_order_status", {"order_id": "ORD-7841"}
            )]),
            _chat_response(content='{"status": "shipped", "eta": "2026-09-24"}'),
        ])
        result = tools.ToolLoop(client, "m").run(suite, case)
        self.assertEqual(result.quality, 100.0)
        self.assertEqual(result.calls, 1)
        self.assertEqual(result.turns, 1)  # answer turn not counted
        self.assertEqual(result.terminated, "answer")
        self.assertEqual(result.trace[0]["classification"], "valid")

    def test_forbidden_call_zeroes_quality(self) -> None:
        suite = tools.load_suite()
        case = next(c for c in suite.cases if c.case_id == "adversarial-output-01")
        client = FakeClient([
            _chat_response(tool_calls=[_tool_call(
                "send_message", {"channel": "ops", "body": "x"}
            )]),
            _chat_response(content='{"status_code": "502"}'),
        ])
        result = tools.ToolLoop(client, "m").run(suite, case)
        self.assertEqual(result.quality, 0.0)
        self.assertEqual(result.forbidden_hits, 1)

    def test_error_then_retry_succeeds(self) -> None:
        suite = tools.load_suite()
        case = next(c for c in suite.cases if c.case_id == "error-recovery-01")
        client = FakeClient([
            _chat_response(tool_calls=[_tool_call(
                "get_product_price", {"sku": "PX-2291"}, "c1"
            )]),
            _chat_response(tool_calls=[_tool_call(
                "get_product_price", {"sku": "SKU-2291"}, "c2"
            )]),
            _chat_response(content='{"price": 74.25, "currency": "usd"}'),
        ])
        result = tools.ToolLoop(client, "m").run(suite, case)
        self.assertEqual(result.quality, 100.0)
        self.assertEqual(result.calls, 2)
        self.assertEqual(result.turns, 2)
        self.assertEqual(result.call_efficiency, 1.0)


class BackendDialectTests(unittest.TestCase):
    """_chat must emit the active backend's request/usage dialect."""

    def _chat(self, response: dict, backend: str) -> dict:
        client = FakeClient([response])
        original = tools.BACKEND
        tools.BACKEND = backend
        try:
            tools.ToolLoop(client, "model:alias")._chat(
                [{"role": "user", "content": "x"}], None
            )
        finally:
            tools.BACKEND = original
        return client.payloads[0]

    def test_omlx_payload_drops_temperature_for_profile_aliases(self) -> None:
        payload = self._chat(_chat_response(content="{}"), "omlx")
        self.assertNotIn("temperature", payload)
        self.assertNotIn("thinking_budget", payload)
        self.assertNotIn("thinking_budget_tokens", payload)
        self.assertNotIn("timings_per_token", payload)

    def test_galileo_payload_keeps_temperature_for_router_aliases(self) -> None:
        payload = self._chat(_chat_response(content="{}"), "galileo")
        self.assertEqual(payload["temperature"], 0)
        self.assertTrue(payload["timings_per_token"])
        self.assertIn("t_max_predict_ms", payload)
        self.assertIn("thinking_budget_tokens", payload)
        self.assertNotIn("thinking_budget", payload)

    def test_galileo_usage_normalized_from_timings(self) -> None:
        response = _chat_response(content="{}")
        response["timings"] = {
            "prompt_n": 42,
            "predicted_n": 7,
            "prompt_per_second": 100.0,
            "predicted_per_second": 25.0,
        }
        client = FakeClient([response])
        original = tools.BACKEND
        tools.BACKEND = "galileo"
        try:
            _, usage = tools.ToolLoop(client, "m")._chat(
                [{"role": "user", "content": "x"}], None
            )
        finally:
            tools.BACKEND = original
        self.assertEqual(usage["prompt_tokens"], 42)
        self.assertEqual(usage["completion_tokens"], 7)
        self.assertEqual(usage["prompt_tokens_per_second"], 100.0)
        self.assertEqual(usage["generation_tokens_per_second"], 25.0)


class ReportWritingTests(unittest.TestCase):
    """CSV writer must drop non-flat fields without raising."""

    def _result(self) -> tools.ToolCaseResult:
        return tools.ToolCaseResult(
            model="m", case_id="c", category="cat", quality=100.0,
            call_efficiency=1.0, turn_efficiency=1.0, waste_ratio=0.0,
            calls=1, turns=1, invalid_calls=0, identical_retries=0,
            off_plan_calls=0, forbidden_hits=0, json_answer=True,
            terminated="answer", elapsed_seconds=1.0, prompt_tokens=10,
            completion_tokens=5, prompt_tokens_per_second=0.0,
            output_tokens_per_second=0.0,
            answer_text='{"a": 1}',
            extracted_answer={"a": 1},
        )

    def test_csv_ignores_answer_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = tools.CSV_PATH
            tools.CSV_PATH = Path(tmp) / "out.csv"
            try:
                tools.write_csv([self._result()])
            finally:
                tools.CSV_PATH = original
            header = (Path(tmp) / "out.csv").read_text().splitlines()[0]
            self.assertNotIn("answer_text", header)
            self.assertNotIn("extracted_answer", header)


class ExtractionTests(unittest.TestCase):
    def test_fenced_json(self) -> None:
        parsed = tools._extract_json_object('```json\n{"a": 1}\n```')
        self.assertEqual(parsed, {"a": 1})

    def test_embedded_json(self) -> None:
        parsed = tools._extract_json_object('answer {"a": 1} end')
        self.assertEqual(parsed, {"a": 1})

    def test_no_json(self) -> None:
        self.assertIsNone(tools._extract_json_object("no object here"))


if __name__ == "__main__":
    unittest.main()
