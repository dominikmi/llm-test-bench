"""Offline unit tests for benchmark_agent_tools: suite loading, mock
dispatch, schema validation, call classification, and answer grading."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import benchmark_agent_tools as tools

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
        quality, is_json = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)
        self.assertTrue(is_json)

    def test_json_answer_partial(self) -> None:
        text = json.dumps({"status": "delivered", "total": 249.5, "flag": True})
        quality, _ = tools._grade_answer(self.fields, text)
        self.assertAlmostEqual(quality, 66.67, places=1)

    def test_json_inside_prose(self) -> None:
        text = 'Here is the answer: {"status": "shipped", "total": 249.5, "flag": true} done.'
        quality, is_json = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)
        self.assertTrue(is_json)

    def test_prose_fallback(self) -> None:
        text = "status: shipped\ntotal: 249.5\nflag: true"
        quality, is_json = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)
        self.assertFalse(is_json)

    def test_number_tolerance(self) -> None:
        text = json.dumps({"status": "shipped", "total": 249.505, "flag": True})
        quality, _ = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 100.0)

    def test_wrong_answer(self) -> None:
        text = json.dumps({"status": "delivered", "total": 10, "flag": False})
        quality, _ = tools._grade_answer(self.fields, text)
        self.assertEqual(quality, 0.0)


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
