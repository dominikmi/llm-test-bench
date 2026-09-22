"""Agentic tool-use benchmark for models hosted on the local oMLX server.

Implements the simulated function-calling harness defined in
docs/TOOLS_USE_TEST_SPEC.md: the model receives OpenAI-style tool schemas,
its tool_calls are validated and dispatched against deterministic per-case
mocks, and grading is judge-free — derived from the call trace (tool names,
arguments, ordering, turn structure) plus typed answer-field matching.

Reuses the oMLX client's transport/retry plumbing, preset loading, and
report layout from benchmark_omlx_reviews; everything tool-specific
(suite schema, dispatcher, loop, classifier, grader) lives here.

Environment variables mirror the oMLX review runner: OMLX_BASE_URL,
OMLX_API_KEY, OMLX_MODELS, OMLX_TIMEOUT_SECONDS, OMLX_MAX_TOKENS,
OMLX_MAX_RETRIES, OMLX_RETRY_FAILURES, OMLX_START_MODEL, OMLX_PRESETS.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import benchmark_galileo_reviews as galileo
from . import benchmark_omlx_reviews as omlx
from .benchmark_galileo_reviews import (
    BenchmarkRequestError,
    atomic_write,
    load_models,
)
from .benchmark_paths import (
    CONFIG_DIR,
    GALILEO_TOOLS_ARCHIVES_DIR,
    GALILEO_TOOLS_RESULTS_DIR,
    LOGS_DIR,
    OMLX_TOOLS_ARCHIVES_DIR,
    OMLX_TOOLS_RESULTS_DIR,
    TEST_DEFINITIONS_DIR,
    ensure_parent_directories,
)

_as_int = omlx._as_int
_as_float = omlx._as_float

SUITE_PATH: Final = Path(
    os.getenv("OMLX_TOOLS_SUITE", str(TEST_DEFINITIONS_DIR / "tool_use.json"))
)

BACKENDS: Final = ("omlx", "galileo")


def _env(prefix: str, name: str, default: str) -> str:
    """Read a backend-namespaced environment variable."""
    return os.getenv(f"{prefix}_{name}", default)


# Rebound by resolve_backend() once --backend is known.
BACKEND = "omlx"
MODELS_PATH = Path(
    _env("OMLX", "MODELS", str(CONFIG_DIR / "models-omlx.json"))
)
MAX_TOKENS = int(_env("OMLX", "MAX_TOKENS", "1024"))
RETRY_FAILURES = omlx.RETRY_FAILURES
START_MODEL = omlx.START_MODEL
WARMUP_ENABLED = omlx.WARMUP_ENABLED
PRESETS_PATH = omlx.PRESETS_PATH


def _artifact_paths(suite_name: str) -> tuple[Path, Path, Path, Path]:
    """Return results, CSV, Markdown, and log paths for a suite+backend."""
    prefix = BACKEND.upper()
    results_dir = (
        GALILEO_TOOLS_RESULTS_DIR if BACKEND == "galileo" else OMLX_TOOLS_RESULTS_DIR
    )
    results = Path(
        os.getenv(
            f"{prefix}_BENCH_RESULTS",
            str(results_dir / f"{BACKEND}-{suite_name}-results.json"),
        )
    )
    log = Path(
        os.getenv(
            f"{prefix}_BENCH_LOG",
            str(LOGS_DIR / f"{BACKEND}-{suite_name}.log"),
        )
    )
    return results, results.with_suffix(".csv"), results.with_suffix(".md"), log


def _archives_dir() -> Path:
    """Archive directory for the active backend."""
    if BACKEND == "galileo":
        return GALILEO_TOOLS_ARCHIVES_DIR
    return OMLX_TOOLS_ARCHIVES_DIR


# Rebound by resolve_report_paths() once --suite is known.
RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH = _artifact_paths("tool-use")


def resolve_report_paths(suite_name: str) -> None:
    """Rebind module report paths to per-suite artifacts."""
    global RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH
    RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH = _artifact_paths(suite_name)


def resolve_backend(backend: str) -> None:
    """Rebind backend-dependent globals after --backend is parsed."""
    global BACKEND, MODELS_PATH, MAX_TOKENS, MODELS
    global RETRY_FAILURES, START_MODEL, WARMUP_ENABLED, PRESETS_PATH
    BACKEND = backend
    prefix = backend.upper()
    if backend == "galileo":
        MODELS_PATH = Path(
            _env(prefix, "MODELS", str(CONFIG_DIR / "models.json"))
        )
        MAX_TOKENS = int(_env(prefix, "MAX_TOKENS", "1024"))
        RETRY_FAILURES = galileo.RETRY_FAILURES
        START_MODEL = galileo.START_MODEL
        WARMUP_ENABLED = False
        PRESETS_PATH = galileo.PRESETS_PATH
    else:
        MODELS_PATH = Path(
            _env(prefix, "MODELS", str(CONFIG_DIR / "models-omlx.json"))
        )
        MAX_TOKENS = int(_env(prefix, "MAX_TOKENS", "1024"))
        RETRY_FAILURES = omlx.RETRY_FAILURES
        START_MODEL = omlx.START_MODEL
        WARMUP_ENABLED = omlx.WARMUP_ENABLED
        PRESETS_PATH = omlx.PRESETS_PATH
    MODELS = load_models(MODELS_PATH)


SUITE_FILES: Final[dict[str, str]] = {
    "tool-use": "tool_use.json",
    "logic": "logic.json",
    "combined": "combined.json",
}

SCHEMA_VERSION: Final = 2
LOGGER: Final = logging.getLogger("omlx-tools-benchmark")
MODELS: tuple[str, ...] = load_models(MODELS_PATH)


class FieldSpec(BaseModel):
    """Typed expectation for one answer field.

    `map` fields nest leaf specs under `fields`; grading flattens them to
    dotted names so each leaf earns partial credit independently.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["number", "enum", "string", "boolean", "set", "map"]
    value: float | bool | None = None
    values: tuple[str, ...] = ()
    expect: str = ""
    any_of: tuple[str, ...] = ()
    tolerance: float = 0.0
    fields: dict[str, FieldSpec] = Field(default_factory=dict)


class AnswerSpec(BaseModel):
    """Structured answer contract: the model must return these JSON keys."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, FieldSpec]


class Plan(BaseModel):
    """One legitimate tool-use strategy for a case."""

    model_config = ConfigDict(extra="forbid")

    name: str
    calls: int = Field(ge=1)
    tools: tuple[str, ...]


class Grading(BaseModel):
    """Deterministic grading contract for a case."""

    model_config = ConfigDict(extra="forbid")

    answer: AnswerSpec
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    plans: tuple[Plan, ...] = ()
    optimal_calls: int = Field(ge=1)
    optimal_turns: int = Field(ge=1)
    max_calls: int = Field(ge=1)
    max_turns: int = Field(ge=1)


class MockRule(BaseModel):
    """One mock dispatch rule: first matching rule wins."""

    model_config = ConfigDict(extra="forbid")

    match: dict[str, Any] | Literal["*"]
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None


class ToolCase(BaseModel):
    """One agentic tool-use test case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: str
    difficulty: Literal["floor", "standard", "hard"] = "standard"
    task: str
    available_tools: tuple[str, ...]
    mocks: dict[str, tuple[MockRule, ...]]
    grading: Grading
    rubric: str = ""


class ToolDef(BaseModel):
    """OpenAI-style tool definition from the shared registry."""

    model_config = ConfigDict(extra="forbid")

    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolUseSuite(BaseModel):
    """Top-level suite definition: shared tool registry plus cases."""

    model_config = ConfigDict(extra="forbid")

    suite: str
    version: int
    description: str = ""
    tools: dict[str, ToolDef]
    cases: tuple[ToolCase, ...]


class LogicCase(BaseModel):
    """One pure-reasoning case: prompt plus typed answer contract."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: str
    difficulty: Literal["floor", "standard", "hard"] = "standard"
    task: str
    answer: AnswerSpec
    notes: str = ""
    rubric: str = ""


class LogicSuite(BaseModel):
    """Top-level logic suite: no tools, no mocks — answer grading only."""

    model_config = ConfigDict(extra="forbid")

    suite: str
    version: int
    description: str = ""
    cases: tuple[LogicCase, ...]


def load_logic_suite(
    path: Path = TEST_DEFINITIONS_DIR / "logic.json",
) -> LogicSuite:
    """Load and validate the logic suite definition."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read suite {path}: {error}") from error
    try:
        return LogicSuite.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"Invalid suite {path}: {error}") from error


@dataclass
class CallRecord:
    """One tool call observed during a case run."""

    turn: int
    tool: str
    arguments: dict[str, Any] | None
    classification: str
    response_excerpt: str


@dataclass
class ToolCaseResult:
    """Per-case outcome with quality and efficiency metrics."""

    model: str
    case_id: str
    category: str
    quality: float
    call_efficiency: float
    turn_efficiency: float
    waste_ratio: float
    calls: int
    turns: int
    invalid_calls: int
    identical_retries: int
    off_plan_calls: int
    forbidden_hits: int
    json_answer: bool
    terminated: str
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    prompt_tokens_per_second: float
    output_tokens_per_second: float
    turn_seconds: tuple[float, ...] = ()
    error: str | None = None
    trace: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    answer_text: str = ""
    extracted_answer: dict[str, Any] | None = None


def load_suite(path: Path = SUITE_PATH) -> ToolUseSuite:
    """Load and validate the tool-use suite definition."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read suite {path}: {error}") from error
    try:
        suite = ToolUseSuite.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"Invalid suite {path}: {error}") from error
    for case in suite.cases:
        known = set(case.available_tools)
        for name in case.available_tools:
            if name not in suite.tools:
                raise ValueError(f"{case.case_id}: unknown tool {name}")
        for name in case.mocks:
            if name not in known:
                raise ValueError(f"{case.case_id}: mock for unavailable {name}")
        for name in (*case.grading.required_tools, *case.grading.forbidden_tools):
            if name not in known:
                raise ValueError(f"{case.case_id}: grading refs unavailable {name}")
        for plan in case.grading.plans:
            for name in plan.tools:
                if name not in known:
                    raise ValueError(
                        f"{case.case_id}: plan {plan.name} refs unavailable {name}"
                    )
    return suite


def validate_arguments(schema: dict[str, Any], args: Any) -> str | None:
    """Validate call arguments against a JSON-schema subset.

    Checks required keys, declared property types, enum membership, and
    string patterns. Extra (undeclared) arguments are permitted, matching
    non-strict function-calling behavior.
    """
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required argument: {key}"
    properties = schema.get("properties", {})
    for key, value in args.items():
        spec = properties.get(key)
        if spec is None:
            continue
        expected = spec.get("type")
        type_map: dict[str, tuple[type, ...]] = {
            "string": (str,),
            "number": (int, float),
            "integer": (int,),
            "boolean": (bool,),
            "object": (dict,),
            "array": (list,),
        }
        if expected in type_map:
            types = type_map[expected]
            if not isinstance(value, types) or (
                expected in {"number", "integer"} and isinstance(value, bool)
            ):
                return f"argument {key}: expected {expected}"
        if "enum" in spec and value not in spec["enum"]:
            return f"argument {key}: must be one of {spec['enum']}"
        if (
            "pattern" in spec
            and isinstance(value, str)
            and not re.search(spec["pattern"], value)
        ):
            return f"argument {key}: must match /{spec['pattern']}/"
    return None


def _args_match(match: dict[str, Any], args: dict[str, Any]) -> bool:
    """A mock rule matches when all of its key/value pairs appear in args."""
    return all(args.get(key) == value for key, value in match.items())


class MockDispatcher:
    """Stateless deterministic mock backend for one case."""

    def __init__(self, mocks: dict[str, tuple[MockRule, ...]]) -> None:
        self._mocks = mocks

    def dispatch(self, tool: str, args: dict[str, Any]) -> str:
        """Return the serialized tool message for one call."""
        for rule in self._mocks.get(tool, ()):
            if rule.match == "*" or _args_match(rule.match, args):
                if rule.error is not None:
                    return json.dumps({"error": rule.error})
                return json.dumps(rule.result or {})
        return json.dumps({
            "error": {
                "code": "no_mock",
                "message": f"no mock rule matched call to {tool}",
            }
        })


def _openai_tools(suite: ToolUseSuite, case: ToolCase) -> list[dict[str, Any]]:
    """Render case tools as OpenAI function-calling schema entries."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": suite.tools[name].description,
                "parameters": suite.tools[name].parameters,
            },
        }
        for name in case.available_tools
    ]


def _answer_contract(fields: dict[str, FieldSpec]) -> str:
    """Instruction appended to the task naming the required answer keys."""
    keys = ", ".join(sorted(fields))
    return (
        "When you have the answer, respond with a JSON object containing "
        f"exactly these keys: {keys}."
    )


def _canonical_args(args: dict[str, Any] | None) -> str:
    """Canonical call signature for duplicate detection."""
    if args is None:
        return ""
    return json.dumps(args, sort_keys=True, default=str)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse the model's final answer as a JSON object, tolerating prose."""
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1))
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _flatten_fields(
    fields: dict[str, FieldSpec], prefix: str = ""
) -> dict[str, FieldSpec]:
    """Expand map fields into dotted leaf names for grading."""
    flat: dict[str, FieldSpec] = {}
    for name, spec in fields.items():
        key = f"{prefix}{name}"
        if spec.type == "map":
            flat.update(_flatten_fields(spec.fields, prefix=f"{key}."))
        else:
            flat[key] = spec
    return flat


def _lookup_path(data: dict[str, Any], dotted: str) -> Any:
    """Resolve a dotted leaf name against a nested answer object."""
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _field_matches(spec: FieldSpec, raw: Any) -> bool:
    """Typed equality for one answer field."""
    if spec.type == "number":
        expected = spec.value
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            return False
        try:
            return abs(float(raw) - float(expected)) <= spec.tolerance
        except (TypeError, ValueError):
            return False
    if spec.type == "boolean":
        if isinstance(raw, str):
            raw = raw.strip().casefold() in {"true", "1", "yes"}
        return raw is spec.value
    if spec.type == "enum":
        accepted = {spec.expect.casefold(), *(o.casefold() for o in spec.any_of)}
        return str(raw).strip().casefold() in accepted
    if spec.type == "set":
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return False
        return {str(item).strip().casefold() for item in raw} == {
            str(item).casefold() for item in spec.values
        }
    text = str(raw).casefold()
    return any(option.casefold() in text for option in spec.any_of)


def _field_prose_match(name: str, spec: FieldSpec, text: str) -> bool:
    """Fallback: find the expected value near the field name in prose."""
    if spec.type == "number":
        expected = spec.value
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            return False
        needle = re.escape(f"{float(expected):g}")
    elif spec.type == "boolean":
        needle = "true" if spec.value else "false"
    elif spec.type == "enum":
        needle = re.escape(spec.expect)
    elif spec.type == "set":
        needle = "|".join(re.escape(option) for option in spec.values)
    else:
        needle = "|".join(re.escape(option) for option in spec.any_of)
    pattern = rf"{re.escape(name)}\W{{0,32}}(?:{needle})"
    return bool(re.search(pattern, text, re.IGNORECASE | re.DOTALL))


def _grade_answer(
    fields: dict[str, FieldSpec], final_text: str
) -> tuple[float, bool, dict[str, Any] | None]:
    """Score the final answer; return quality, JSON flag, parsed object."""
    flat = _flatten_fields(fields)
    parsed = _extract_json_object(final_text)
    if parsed is not None:
        matched = sum(
            _field_matches(spec, _lookup_path(parsed, name))
            for name, spec in flat.items()
        )
        return 100.0 * matched / len(flat), True, parsed
    matched = sum(
        _field_prose_match(name.split(".")[-1], spec, final_text)
        for name, spec in flat.items()
    )
    return 100.0 * matched / len(flat), False, None


def _classify_call(
    name: str,
    args: dict[str, Any] | None,
    schemas: dict[str, dict[str, Any]],
    seen: set[tuple[str, str]],
    plan_tools: frozenset[str],
    forbidden: frozenset[str],
) -> tuple[str, str | None]:
    """Classify a call and return (class, schema_error)."""
    if name in forbidden:
        return "forbidden", None
    if name not in schemas:
        return "invalid", f"unknown tool: {name}"
    schema_error = validate_arguments(schemas[name], args)
    if schema_error is not None:
        return "invalid", schema_error
    signature = (name, _canonical_args(args))
    if signature in seen:
        return "identical_retry", None
    seen.add(signature)
    if plan_tools and name not in plan_tools:
        return "off_plan", None
    return "valid", None


class ChatTransport(Protocol):
    """Transport surface satisfied by OmlxClient and GalileoClient."""

    _presets: dict[str, dict[str, Any]]

    def _request_with_retry(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def models(self) -> dict[str, Any]: ...


class ToolLoop:
    """Drives one model through a case: tool loop or single-turn logic."""

    def __init__(self, client: ChatTransport, model: str) -> None:
        self._client = client
        self._model = model

    def run_logic(self, case: LogicCase) -> ToolCaseResult:
        """Single-turn reasoning case: prompt plus answer contract, no tools."""
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"{case.task}\n\n{_answer_contract(case.answer.fields)}",
            }
        ]
        started = time.perf_counter()
        message, usage = self._chat(messages, None)
        elapsed = time.perf_counter() - started
        final_text = str(message.get("content") or "")
        quality, json_answer, extracted = _grade_answer(
            case.answer.fields, final_text
        )
        return ToolCaseResult(
            model=self._model,
            case_id=case.case_id,
            category=case.category,
            quality=round(quality, 2),
            call_efficiency=1.0,
            turn_efficiency=1.0,
            waste_ratio=0.0,
            calls=0,
            turns=0,
            invalid_calls=0,
            identical_retries=0,
            off_plan_calls=0,
            forbidden_hits=0,
            json_answer=json_answer,
            terminated="answer" if final_text.strip() else "empty",
            elapsed_seconds=round(elapsed, 3),
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            prompt_tokens_per_second=_as_float(
                usage.get("prompt_tokens_per_second")
            ),
            output_tokens_per_second=_as_float(
                usage.get("generation_tokens_per_second")
            ),
            turn_seconds=(round(elapsed, 3),),
            answer_text=final_text,
            extracted_answer=extracted,
        )

    def run(self, suite: ToolUseSuite, case: ToolCase) -> ToolCaseResult:
        """Execute the case loop and grade the resulting trace."""
        grading = case.grading
        dispatcher = MockDispatcher(case.mocks)
        schemas = {
            name: suite.tools[name].parameters for name in case.available_tools
        }
        plan_tools = frozenset(
            tool for plan in grading.plans for tool in plan.tools
        )
        forbidden = frozenset(grading.forbidden_tools)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"{case.task}\n\n"
                    f"{_answer_contract(grading.answer.fields)}"
                ),
            }
        ]
        tools = _openai_tools(suite, case)
        records: list[CallRecord] = []
        seen_calls: set[tuple[str, str]] = set()
        prompt_tokens = 0
        completion_tokens = 0
        prompt_rates: list[float] = []
        output_rates: list[float] = []
        turn_seconds: list[float] = []
        terminated = "max_turns"
        final_text = ""
        started = time.perf_counter()
        for turn in range(1, grading.max_turns + 1):
            if len(records) >= grading.max_calls:
                terminated = "max_calls"
                break
            turn_started = time.perf_counter()
            message, usage = self._chat(messages, tools)
            turn_seconds.append(round(time.perf_counter() - turn_started, 3))
            prompt_tokens += _as_int(usage.get("prompt_tokens"))
            completion_tokens += _as_int(usage.get("completion_tokens"))
            prompt_rate = _as_float(usage.get("prompt_tokens_per_second"))
            if prompt_rate > 0:
                prompt_rates.append(prompt_rate)
            rate = _as_float(usage.get("generation_tokens_per_second"))
            if rate > 0:
                output_rates.append(rate)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                final_text = str(message.get("content") or "")
                terminated = "answer"
                break
            messages.append(message)
            for call in tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                raw_args = function.get("arguments")
                args: dict[str, Any] | None
                try:
                    parsed = json.loads(raw_args or "{}")
                    args = parsed if isinstance(parsed, dict) else None
                except (TypeError, ValueError):
                    args = None
                classification, schema_error = _classify_call(
                    name, args, schemas, seen_calls, plan_tools, forbidden
                )
                if classification == "invalid":
                    response_text = json.dumps({
                        "error": {
                            "code": "invalid_params",
                            "message": schema_error or "malformed arguments",
                        }
                    })
                else:
                    response_text = dispatcher.dispatch(name, args or {})
                records.append(
                    CallRecord(
                        turn=turn,
                        tool=name,
                        arguments=args,
                        classification=classification,
                        response_excerpt=response_text[:400],
                    )
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or f"call_{len(records)}"),
                    "content": response_text,
                })
        elapsed = time.perf_counter() - started
        calls = len(records)
        forbidden_hits = sum(r.classification == "forbidden" for r in records)
        invalid = sum(r.classification == "invalid" for r in records)
        retries = sum(r.classification == "identical_retry" for r in records)
        off_plan = sum(r.classification == "off_plan" for r in records)
        required_hit = all(
            any(r.tool == name for r in records)
            for name in grading.required_tools
        )
        extracted: dict[str, Any] | None = None
        if terminated == "answer" and final_text.strip():
            quality, json_answer, extracted = _grade_answer(
                grading.answer.fields, final_text
            )
        else:
            quality, json_answer = 0.0, False
        if forbidden_hits or not required_hit:
            quality = 0.0
        # turn_efficiency compares decision turns (turns that issued calls);
        # the final answer turn carries no tool_calls and is excluded.
        decision_turns = max((r.turn for r in records), default=0)
        waste = invalid + retries + off_plan
        return ToolCaseResult(
            model=self._model,
            case_id=case.case_id,
            category=case.category,
            quality=round(quality, 2),
            call_efficiency=round(
                min(1.0, grading.optimal_calls / max(calls, 1)), 3
            ),
            turn_efficiency=round(
                min(1.0, grading.optimal_turns / max(decision_turns, 1)), 3
            ),
            waste_ratio=round(waste / calls, 3) if calls else 0.0,
            calls=calls,
            turns=decision_turns,
            invalid_calls=invalid,
            identical_retries=retries,
            off_plan_calls=off_plan,
            forbidden_hits=forbidden_hits,
            json_answer=json_answer,
            terminated=terminated,
            elapsed_seconds=round(elapsed, 3),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_per_second=(
                round(mean(prompt_rates), 2) if prompt_rates else 0.0
            ),
            output_tokens_per_second=(
                round(mean(output_rates), 2) if output_rates else 0.0
            ),
            turn_seconds=tuple(turn_seconds),
            answer_text=final_text,
            extracted_answer=extracted,
            trace=tuple(asdict(record) for record in records),
        )

    def _chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """One non-streaming chat round-trip, optionally with tool schemas."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        if BACKEND == "galileo":
            # llama.cpp dialect: native timing fields and the budget spelling.
            payload["timings_per_token"] = True
            payload["t_max_predict_ms"] = galileo.PREDICT_TIMEOUT_MS
            payload["thinking_budget_tokens"] = galileo.THINKING_BUDGET_TOKENS
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        sampling = self._client._presets.get(self._model.casefold(), {})
        if sampling:
            payload.update(sampling)
            if "enable_thinking" in sampling:
                thinking_on = bool(sampling["enable_thinking"])
                payload.pop("enable_thinking")
                payload["chat_template_kwargs"]["enable_thinking"] = thinking_on
                if not thinking_on and BACKEND == "galileo":
                    payload["thinking_budget_tokens"] = 0
        elif BACKEND == "omlx" and ":" in self._model:
            # oMLX "model:profile" aliases carry tuned server-side sampling.
            # Galileo ":TAG" aliases are router names — keep temperature.
            payload.pop("temperature")
        if (
            BACKEND == "galileo"
            and payload["chat_template_kwargs"].get("enable_thinking")
            and payload.get("thinking_budget_tokens", 0) >= payload["max_tokens"]
        ):
            # llama.cpp n_predict bounds reasoning+answer together — an
            # undersized cap starves the final content after thinking
            # consumes the budget. oMLX accounts thinking separately.
            LOGGER.warning(
                "thinking_budget_tokens=%s >= max_tokens=%s for %s: "
                "the answer may be truncated to empty",
                payload["thinking_budget_tokens"],
                payload["max_tokens"],
                self._model,
            )
        response = self._client._request_with_retry(
            "POST", "/chat/completions", payload
        )
        choices = response.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise TypeError("Chat response missing choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise TypeError("Chat response missing message")
        usage = response.get("usage")
        usage = dict(usage) if isinstance(usage, dict) else {}
        if BACKEND == "galileo":
            # llama.cpp reports under `timings` (authoritative) plus `usage`.
            timings = response.get("timings")
            if isinstance(timings, dict):
                usage["prompt_tokens"] = _as_int(
                    timings.get("prompt_n"), usage.get("prompt_tokens")
                )
                usage["completion_tokens"] = _as_int(
                    timings.get("predicted_n"), usage.get("completion_tokens")
                )
                usage["prompt_tokens_per_second"] = _as_float(
                    timings.get("prompt_per_second")
                )
                usage["generation_tokens_per_second"] = _as_float(
                    timings.get("predicted_per_second")
                )
        return message, usage


def summarize(results: list[ToolCaseResult]) -> dict[str, dict[str, float]]:
    """Aggregate quality and efficiency metrics by model."""
    summaries: dict[str, dict[str, float]] = {}
    for model in MODELS:
        rows = [result for result in results if result.model == model]
        if not rows:
            continue
        rates = [
            result.output_tokens_per_second
            for result in rows
            if result.output_tokens_per_second > 0
        ]
        prompt_rates = [
            result.prompt_tokens_per_second
            for result in rows
            if result.prompt_tokens_per_second > 0
        ]
        total_quality = sum(r.quality for r in rows)
        total_completion = sum(r.completion_tokens for r in rows)
        turn_times = [t for r in rows for t in r.turn_seconds]
        summaries[model] = {
            "completed_cases": float(len(rows)),
            "failed_cases": float(sum(r.error is not None for r in rows)),
            "quality": round(mean(r.quality for r in rows), 2),
            "call_efficiency": round(mean(r.call_efficiency for r in rows), 3),
            "turn_efficiency": round(mean(r.turn_efficiency for r in rows), 3),
            "waste_ratio": round(mean(r.waste_ratio for r in rows), 3),
            "json_answer_rate": round(
                mean(1.0 if r.json_answer else 0.0 for r in rows), 3
            ),
            "forbidden_hits": float(sum(r.forbidden_hits for r in rows)),
            "total_calls": float(sum(r.calls for r in rows)),
            "tokens_per_quality_point": (
                round(total_completion / total_quality, 1)
                if total_quality > 0
                else 0.0
            ),
            "mean_turn_seconds": (
                round(mean(turn_times), 3) if turn_times else 0.0
            ),
            "mean_prompt_tokens_per_second": (
                round(mean(prompt_rates), 2) if prompt_rates else 0.0
            ),
            "mean_output_tokens_per_second": (
                round(mean(rates), 2) if rates else 0.0
            ),
            "elapsed_seconds": round(sum(r.elapsed_seconds for r in rows), 3),
        }
    return summaries


def _backend_base_url() -> str:
    """Endpoint URL recorded in reports for the active backend."""
    return galileo.BASE_URL if BACKEND == "galileo" else omlx.BASE_URL


def _backend_timeout() -> float:
    """Request timeout for the active backend."""
    return galileo.TIMEOUT_SECONDS if BACKEND == "galileo" else omlx.TIMEOUT_SECONDS


def _backend_client(presets: dict[str, dict[str, Any]]) -> ChatTransport:
    """Construct the transport for the active backend."""
    if BACKEND == "galileo":
        return galileo.GalileoClient(presets)
    return omlx.OmlxClient(presets)


def _backend_presets(path: Path) -> dict[str, dict[str, Any]]:
    """Parse a preset file with the active backend's key dialect."""
    if BACKEND == "galileo":
        return galileo.load_presets(path)
    return omlx.load_presets(path)


def report_payload(
    results: list[ToolCaseResult],
    model_parameters: dict[str, dict[str, Any]],
    presets_path: str = "",
    suite_name: str = "tool-use",
    case_count: int = 0,
) -> dict[str, Any]:
    """Build the detailed machine-readable benchmark report."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": _backend_base_url(),
        "benchmark_parameters": {
            "backend": BACKEND,
            "suite": suite_name,
            "models": list(MODELS),
            "cases": case_count,
            "max_tokens": MAX_TOKENS,
            "request_timeout_seconds": _backend_timeout(),
            "stream": False,
            "judge": "none (deterministic grading)",
            "start_model": START_MODEL or None,
            "retry_failures": RETRY_FAILURES,
            "presets_path": presets_path or None,
        },
        "model_parameters": model_parameters,
        "summaries": summarize(results),
        "results": [asdict(result) for result in results],
    }


def write_csv(results: list[ToolCaseResult]) -> None:
    """Write flat per-case metrics for spreadsheet analysis."""
    ensure_parent_directories(CSV_PATH)
    flat_fields = [
        key
        for key in asdict(results[0])
        if key not in {"trace", "answer_text", "extracted_answer"}
    ]
    temporary_path = CSV_PATH.with_suffix(f"{CSV_PATH.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output, fieldnames=flat_fields, extrasaction="ignore"
        )
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))
    temporary_path.replace(CSV_PATH)


def write_markdown(payload: dict[str, Any]) -> None:
    """Write a readable benchmark report with rankings and parameters."""
    summaries = payload["summaries"]
    ranked = sorted(
        summaries.items(), key=lambda item: item[1]["quality"], reverse=True
    )
    lines = [
        "# oMLX Agentic Tool-Use Benchmark",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "Judge-free grading: deterministic trace classification plus typed",
        "answer-field matching per `docs/TOOLS_USE_TEST_SPEC.md`.",
        "",
        "## Results",
        "",
        (
            "| Rank | Model | Quality | Call eff | Turn eff | Waste | "
            "JSON ans | Tok/pt | Turn s | PP tok/s | Out tok/s | Fail |"
        ),
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, (model, summary) in enumerate(ranked, start=1):
        lines.append(
            f"| {rank} | `{model}` | {summary['quality']:.2f} | "
            f"{summary['call_efficiency']:.3f} | {summary['turn_efficiency']:.3f} | "
            f"{summary['waste_ratio']:.3f} | {summary['json_answer_rate']:.2f} | "
            f"{summary['tokens_per_quality_point']:.1f} | "
            f"{summary['mean_turn_seconds']:.2f} | "
            f"{summary['mean_prompt_tokens_per_second']:.2f} | "
            f"{summary['mean_output_tokens_per_second']:.2f} | "
            f"{summary['failed_cases']:.0f} |"
        )
    lines.extend(["", "## Benchmark parameters", "", "```json"])
    lines.append(json.dumps(payload["benchmark_parameters"], indent=2))
    lines.extend(["```", "", "## Model parameters", ""])
    for model, parameters in payload["model_parameters"].items():
        lines.extend([f"### `{model}`", "", "```json"])
        lines.append(json.dumps(parameters, indent=2))
        lines.extend(["```", ""])
    lines.extend([
        "## Case results",
        "",
        (
            "| Model | Case | Quality | Calls | Turns | Waste | Terminated "
            "| Seconds | Out tok/s | Error |"
        ),
        "|---|---|---:|---:|---:|---:|---|---:|---:|---|",
    ])
    for result in payload["results"]:
        lines.append(
            f"| `{result['model']}` | {result['case_id']} | "
            f"{result['quality']:.2f} | {result['calls']} | {result['turns']} | "
            f"{result['waste_ratio']:.3f} | {result['terminated']} | "
            f"{result['elapsed_seconds']:.2f} | "
            f"{result['output_tokens_per_second']:.2f} | {result['error'] or ''} |"
        )
    atomic_write(REPORT_PATH, "\n".join(lines) + "\n")


def save_reports(
    results: list[ToolCaseResult],
    model_parameters: dict[str, dict[str, Any]],
    presets_path: str = "",
    suite_name: str = "tool-use",
    case_count: int = 0,
) -> None:
    """Persist detailed JSON, CSV, and Markdown reports after every case."""
    payload = report_payload(
        results, model_parameters, presets_path, suite_name, case_count
    )
    atomic_write(RESULTS_PATH, json.dumps(payload, indent=2) + "\n")
    if results:
        write_csv(results)
    write_markdown(payload)


def load_results() -> tuple[list[ToolCaseResult], dict[str, dict[str, Any]]]:
    """Resume compatible partial results instead of repeating completed cases."""
    if not RESULTS_PATH.exists():
        return [], {}
    try:
        payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            LOGGER.warning("Ignoring incompatible prior results at %s", RESULTS_PATH)
            return [], {}
        results = [
            ToolCaseResult(**{
                **item,
                "trace": tuple(item.get("trace", ())),
            })
            for item in payload.get("results", [])
        ]
        parameters = payload.get("model_parameters", {})
        return results, parameters if isinstance(parameters, dict) else {}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        LOGGER.warning("Could not resume %s: %s", RESULTS_PATH, error)
        return [], {}


def archive_reports() -> Path | None:
    """Preserve the current report set before replacing failed measurements."""
    existing = tuple(
        path for path in (RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH) if path.exists()
    )
    if not existing:
        return None
    archive_directory = _archives_dir() / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    archive_directory.mkdir(parents=True, exist_ok=False)
    for path in existing:
        shutil.move(path, archive_directory / path.name)
    return archive_directory


def configure_logging() -> None:
    """Log benchmark progress to both the terminal and a persistent file."""
    ensure_parent_directories(LOG_PATH)
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH)):
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)


def print_summary(results: list[ToolCaseResult]) -> None:
    """Display final model rankings in the terminal."""
    LOGGER.info("Final results")
    LOGGER.info(
        "%-45s %8s %9s %9s %7s %8s %8s %7s %10s %10s %6s",
        "MODEL", "QUALITY", "CALL EFF", "TURN EFF", "WASTE", "JSON ANS",
        "TOK/PT", "TURN S", "PP TOK/S", "OUT TOK/S", "FAIL",
    )
    for model, summary in sorted(
        summarize(results).items(),
        key=lambda item: item[1]["quality"],
        reverse=True,
    ):
        LOGGER.info(
            "%-45s %8.2f %9.3f %9.3f %7.3f %8.2f %8.1f %7.2f %10.2f %10.2f %6.0f",
            model,
            summary["quality"],
            summary["call_efficiency"],
            summary["turn_efficiency"],
            summary["waste_ratio"],
            summary["json_answer_rate"],
            summary["tokens_per_quality_point"],
            summary["mean_turn_seconds"],
            summary["mean_prompt_tokens_per_second"],
            summary["mean_output_tokens_per_second"],
            summary["failed_cases"],
        )


def main(argv: list[str] | None = None) -> int:
    """Run or resume the tool-use suite with one active model at a time."""
    parser = argparse.ArgumentParser(
        description=(
            "Agentic tool-use/logic benchmark (judge-free, simulated tools) "
            "for models hosted on oMLX or Galileo."
        )
    )
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="omlx",
        help="Serving stack to benchmark: omlx (local MLX) or galileo (llama.cpp)",
    )
    parser.add_argument(
        "--presets",
        nargs="?",
        const="__default__",
        default=None,
        help="Load per-model sampling presets from an INI file "
        "(bare flag uses the backend's default preset file)",
    )
    parser.add_argument(
        "--suite",
        choices=sorted(SUITE_FILES),
        default="tool-use",
        help="Benchmark suite to run: simulated tool-use or single-turn logic",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    resolve_backend(args.backend)
    suite_name: str = args.suite
    resolve_report_paths(suite_name)
    tool_suite: ToolUseSuite | None = None
    suite_cases: tuple[ToolCase | LogicCase, ...]
    try:
        if suite_name == "logic":
            suite_cases = load_logic_suite().cases
        else:
            suite_file = (
                SUITE_PATH
                if suite_name == "tool-use"
                else TEST_DEFINITIONS_DIR / SUITE_FILES[suite_name]
            )
            tool_suite = load_suite(suite_file)
            suite_cases = tool_suite.cases
    except ValueError as error:
        print(f"Cannot load {suite_name} suite: {error}", file=sys.stderr)
        return 1
    presets_path = args.presets
    if presets_path == "__default__":
        presets_path = str(
            CONFIG_DIR / ("presets.ini" if BACKEND == "galileo" else "presets-omlx.ini")
        )
    elif presets_path is None:
        presets_path = PRESETS_PATH
    presets: dict[str, dict[str, Any]] = {}
    if presets_path:
        presets = _backend_presets(Path(presets_path))
    archive_directory: Path | None = None
    if RETRY_FAILURES:
        try:
            archive_directory = archive_reports()
        except OSError as error:
            print(f"Cannot archive existing reports: {error}", file=sys.stderr)
            return 1
    configure_logging()
    if archive_directory is not None:
        LOGGER.info("Pre-retry reports archived at %s", archive_directory.resolve())
    client = _backend_client(presets)
    results, model_parameters = load_results()
    completed = {
        (result.model, result.case_id)
        for result in results
        if not (RETRY_FAILURES and result.error is not None)
    }
    if START_MODEL:
        if START_MODEL not in MODELS:
            LOGGER.error(
                "%s_START_MODEL is not configured: %s",
                BACKEND.upper(),
                START_MODEL,
            )
            return 1
        active_models = MODELS[MODELS.index(START_MODEL) :]
    else:
        active_models = MODELS
    LOGGER.info(
        "Benchmark start endpoint=%s suite=%s models=%d cases=%d "
        "resumed=%d timeout=%.0fs",
        _backend_base_url(),
        suite_name,
        len(active_models),
        len(suite_cases),
        len(results),
        _backend_timeout(),
    )
    try:
        advertised_models = client.models()
    except (BenchmarkRequestError, TypeError) as error:
        LOGGER.error("Cannot list %s models: %s", BACKEND, error)
        return 1
    missing = set(MODELS) - set(advertised_models)
    if missing:
        LOGGER.error("Missing model aliases: %s", ", ".join(sorted(missing)))
        return 1
    for model_index, model in enumerate(active_models, start=1):
        model_parameters.setdefault(model, {"api_model": advertised_models[model]})
        if model.casefold() in presets:
            model_parameters[model]["sampling"] = presets[model.casefold()]
        LOGGER.info("Model %d/%d started: %s", model_index, len(active_models), model)
        loaded_once = any(result.model == model for result in results)
        if WARMUP_ENABLED and not loaded_once and hasattr(client, "warmup"):
            load_seconds = client.warmup(model)  # type: ignore[attr-defined]
            if load_seconds:
                model_parameters[model]["model_load_seconds"] = load_seconds
        loop = ToolLoop(client, model)
        case_iter: list[ToolCase | LogicCase] = list(suite_cases)
        for case_index, case in enumerate(case_iter, start=1):
            if (model, case.case_id) in completed:
                LOGGER.info(
                    "Case skipped from resume model=%s case=%s", model, case.case_id
                )
                continue
            try:
                if isinstance(case, LogicCase):
                    result = loop.run_logic(case)
                elif tool_suite is not None:
                    result = loop.run(tool_suite, case)
                else:
                    raise ValueError("tool-use case without loaded suite")
                load_seconds = getattr(client, "last_model_load_seconds", 0.0)
                if not loaded_once and load_seconds:
                    model_parameters[model]["model_load_seconds"] = load_seconds
                    loaded_once = True
                LOGGER.info(
                    "Case scored model=%s case=%s progress=%d/%d quality=%.2f "
                    "calls=%d turns=%d waste=%.2f terminated=%s",
                    model,
                    case.case_id,
                    case_index,
                    len(suite_cases),
                    result.quality,
                    result.calls,
                    result.turns,
                    result.waste_ratio,
                    result.terminated,
                )
            except (
                BenchmarkRequestError,
                IndexError,
                KeyError,
                TypeError,
                ValueError,
            ) as error:
                result = ToolCaseResult(
                    model=model,
                    case_id=case.case_id,
                    category=case.category,
                    quality=0.0,
                    call_efficiency=0.0,
                    turn_efficiency=0.0,
                    waste_ratio=0.0,
                    calls=0,
                    turns=0,
                    invalid_calls=0,
                    identical_retries=0,
                    off_plan_calls=0,
                    forbidden_hits=0,
                    json_answer=False,
                    terminated="error",
                    elapsed_seconds=0.0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    prompt_tokens_per_second=0.0,
                    output_tokens_per_second=0.0,
                    error=f"{type(error).__name__}: {error}",
                )
                LOGGER.warning(
                    "Case failed model=%s case=%s: %s", model, case.case_id, error
                )
            results.append(result)
            save_reports(
                results, model_parameters, presets_path, suite_name,
                len(suite_cases),
            )
    print_summary(results)
    LOGGER.info("JSON report: %s", RESULTS_PATH.resolve())
    LOGGER.info("CSV report: %s", CSV_PATH.resolve())
    LOGGER.info("Markdown report: %s", REPORT_PATH.resolve())
    LOGGER.info("Operation log: %s", LOG_PATH.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
