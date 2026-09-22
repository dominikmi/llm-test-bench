"""Advanced math benchmark for Galileo or oMLX models with a sharp-answer harness."""

from __future__ import annotations

import argparse
import configparser
import csv
import json
import logging
import math
import os
import shutil
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Final

from . import benchmark_omlx_reviews as omlx
from .benchmark_paths import (
    CONFIG_DIR,
    GALILEO_MATH_ARCHIVES_DIR,
    GALILEO_MATH_RESULTS_DIR,
    LOGS_DIR,
    OMLX_MATH_ARCHIVES_DIR,
    OMLX_MATH_RESULTS_DIR,
    ensure_parent_directories,
)

BACKENDS: Final = ("galileo", "omlx")
BACKEND_NAMES: Final = {"galileo": "Galileo", "omlx": "oMLX"}

# Rebound by resolve_backend() once --backend is known; defaults are Galileo.
BACKEND = "galileo"
BASE_URL = os.getenv("GALILEO_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
API_KEY = os.getenv("GALILEO_API_KEY", "sk-noauth")
TIMEOUT_SECONDS = float(os.getenv("GALILEO_TIMEOUT_SECONDS", "300"))
MAX_TOKENS = int(os.getenv("GALILEO_MAX_TOKENS", "700"))
MAX_RETRIES = int(os.getenv("GALILEO_MAX_RETRIES", "1"))
PREDICT_TIMEOUT_MS = int(os.getenv("GALILEO_PREDICT_TIMEOUT_MS", "300000"))
THINKING_BUDGET_TOKENS = int(os.getenv("GALILEO_THINKING_BUDGET", "256"))
START_MODEL = os.getenv("GALILEO_START_MODEL", "")
RETRY_FAILURES = os.getenv("GALILEO_RETRY_FAILURES", "0").casefold() in {
    "1",
    "true",
    "yes",
}
PRESETS_PATH = os.getenv("GALILEO_PRESETS", "")
PRESET_SAMPLING_KEYS: Final[dict[str, str]] = {
    "temp": "temperature",
    "top-p": "top_p",
    "top-k": "top_k",
    "min-p": "min_p",
    "repeat-penalty": "repeat_penalty",
    "presence-penalty": "presence_penalty",
    "frequency-penalty": "frequency_penalty",
    "max-tokens": "max_tokens",
    "max_tokens": "max_tokens",
    "thinking-budget": "thinking_budget_tokens",
    "enable-thinking": "enable_thinking",
}
INT_SAMPLING_KEYS: Final = frozenset(
    {"top_k", "max_tokens", "thinking_budget_tokens"}
)
BOOL_SAMPLING_KEYS: Final = frozenset({"enable_thinking"})
NO_THINKING_MODELS: frozenset[str] = frozenset()
NO_SCHEMA_MODELS: frozenset[str] = frozenset()
RESULTS_PATH = Path(
    os.getenv(
        "GALILEO_MATH_RESULTS",
        str(GALILEO_MATH_RESULTS_DIR / "galileo-math-results.json"),
    )
)
CSV_PATH = RESULTS_PATH.with_suffix(".csv")
REPORT_PATH = RESULTS_PATH.with_suffix(".md")
LOG_PATH = Path(
    os.getenv("GALILEO_MATH_LOG", str(LOGS_DIR / "galileo-math.log"))
)
MATH_ARCHIVES_DIR = GALILEO_MATH_ARCHIVES_DIR
SCHEMA_VERSION: Final = 2
PROMPT_VERSION: Final = 2
LOGGER: Final = logging.getLogger("galileo-math-benchmark")


def load_models(path: Path | None = None) -> tuple[str, ...]:
    """Load the ordered list of model aliases from a JSON file."""
    target = path if path is not None else CONFIG_DIR / "models.json"
    if not target.exists():
        raise FileNotFoundError(f"Models file not found: {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError(f"Models file must contain a JSON array of strings: {target}")
    if not data:
        raise ValueError(f"Models file is empty: {target}")
    return tuple(data)


MODELS: tuple[str, ...] = load_models()


def resolve_backend(backend: str) -> None:
    """Rebind endpoint, dialect, model, and artifact globals for --backend.

    The module-level defaults already describe Galileo, so only the oMLX
    branch needs to rebind: the oMLX request dialect, model list, artifact
    paths, and the env-driven no-thinking/no-schema model sets.
    """
    global BACKEND, BASE_URL, API_KEY, TIMEOUT_SECONDS, MAX_TOKENS, MAX_RETRIES
    global PREDICT_TIMEOUT_MS, THINKING_BUDGET_TOKENS, START_MODEL
    global RETRY_FAILURES, PRESETS_PATH, NO_THINKING_MODELS, NO_SCHEMA_MODELS
    global RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH, MATH_ARCHIVES_DIR
    global MODELS
    BACKEND = backend
    if backend != "omlx":
        return
    BASE_URL = omlx.BASE_URL
    API_KEY = omlx.API_KEY
    TIMEOUT_SECONDS = omlx.TIMEOUT_SECONDS
    MAX_TOKENS = int(os.getenv("OMLX_MAX_TOKENS", "700"))
    MAX_RETRIES = int(os.getenv("OMLX_MAX_RETRIES", "1"))
    PREDICT_TIMEOUT_MS = int(os.getenv("OMLX_PREDICT_TIMEOUT_MS", "300000"))
    THINKING_BUDGET_TOKENS = omlx.THINKING_BUDGET_TOKENS
    START_MODEL = omlx.START_MODEL
    RETRY_FAILURES = omlx.RETRY_FAILURES
    PRESETS_PATH = omlx.PRESETS_PATH
    NO_THINKING_MODELS = omlx.NO_THINKING_MODELS
    NO_SCHEMA_MODELS = omlx.NO_SCHEMA_MODELS
    RESULTS_PATH = Path(
        os.getenv(
            "OMLX_MATH_RESULTS",
            str(OMLX_MATH_RESULTS_DIR / "omlx-math-results.json"),
        )
    )
    CSV_PATH = RESULTS_PATH.with_suffix(".csv")
    REPORT_PATH = RESULTS_PATH.with_suffix(".md")
    LOG_PATH = Path(
        os.getenv("OMLX_MATH_LOG", str(LOGS_DIR / "omlx-math.log"))
    )
    MATH_ARCHIVES_DIR = OMLX_MATH_ARCHIVES_DIR
    MODELS = load_models(
        Path(os.getenv("OMLX_MODELS", str(CONFIG_DIR / "models-omlx.json")))
    )


def _load_backend_presets(path: Path) -> dict[str, dict[str, Any]]:
    """Parse a preset file with the active backend's key dialect."""
    if BACKEND == "omlx":
        return omlx.load_presets(path)
    return load_presets(path)


@dataclass(frozen=True, slots=True)
class MathCase:
    """One advanced math problem with accepted answer strings."""

    case_id: str
    category: str
    problem: str
    accepted: tuple[str, ...]
    is_numeric: bool = False
    tolerance: float = 0.01


@dataclass(frozen=True, slots=True)
class ResponseMetrics:
    """Raw model response and timing measurements."""

    text: str
    reasoning_text: str
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    prompt_ms: float
    generation_ms: float
    prompt_tokens_per_second: float
    output_tokens_per_second: float


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Scored response for one model and math case."""

    model: str
    case_id: str
    category: str
    score: float
    recall: float
    precision: float
    model_answer: str
    accepted: tuple[str, ...]
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    prompt_ms: float
    generation_ms: float
    prompt_tokens_per_second: float
    output_tokens_per_second: float
    end_to_end_tokens_per_second: float
    response: str
    reasoning_response: str
    error: str | None = None


MATH_CASES: Final = (
    MathCase(
        "math-01",
        "linear-algebra",
        "Compute the determinant of the 3x3 matrix [[2, -1, 3], [1, 4, 2], [0, 3, 1]].",
        ("6", "6.0"),
        is_numeric=True,
    ),
    MathCase(
        "math-02",
        "calculus",
        "Find the derivative of f(x) = x^3 * e^x.",
        ("x^2*e^x*(x+3)", "x^2 e^x (x+3)", "e^x x^2 (x+3)", "x^2*(x+3)*e^x"),
    ),
    MathCase(
        "math-03",
        "algebra",
        "Solve 2^(x+1) + 2^(x-1) = 40 for real x.",
        ("4", "4.0"),
        is_numeric=True,
    ),
    MathCase(
        "math-04",
        "probability",
        "What is the exact probability of being dealt a flush in a 5-card poker hand?",
        ("33/16660", "0.001981"),
        is_numeric=True,
        tolerance=1e-5,
    ),
    MathCase(
        "math-05",
        "series",
        "Evaluate the sum from n=1 to infinity of 1/(n*(n+2)).",
        ("3/4", "0.75"),
        is_numeric=True,
    ),
    MathCase(
        "math-06",
        "complex",
        "What is the real part of (1 + i)^10?",
        ("0", "0.0"),
        is_numeric=True,
    ),
    MathCase(
        "math-07",
        "calculus",
        "Evaluate the definite integral of x^2 * ln(x) from 1 to e.",
        ("(2e^3+1)/9", "(2e^3 + 1)/9", "(1+2e^3)/9"),
    ),
    MathCase(
        "math-08",
        "number-theory",
        "What is the smallest prime p such that p^2 + 2 is also prime?",
        ("3", "3.0"),
        is_numeric=True,
    ),
    MathCase(
        "math-09",
        "multivariable",
        "Compute the gradient of f(x,y) = x^2*y + y^3 at the point (1, 2).",
        ("(4, 13)", "<4,13>", "4i+13j", "4i + 13j"),
    ),
    MathCase(
        "math-10",
        "number-theory",
        "How many distinct positive divisors does 720 have?",
        ("30", "30.0"),
        is_numeric=True,
    ),
)


def build_math_prompt(case: MathCase) -> str:
    """Build a verification-oriented prompt without exposing accepted answers."""
    safe_problem = case.problem.replace("</problem>", "< /problem>")
    return f"""Solve the following {case.category} problem accurately.

Reason through the problem before producing the final response. Check arithmetic and signs,
honor all domain or real-solution constraints, and verify the result by substitution or an
independent calculation when practical. Prefer an exact simplified form over a decimal unless
the problem requests an approximation. Include every requested solution, vector component, or
quantity in one answer string. Keep all derivation and verification out of the final response.

<problem>
{safe_problem}
</problem>

Final response: return exactly one JSON object with one non-empty string field named "answer".
The field value must contain only the final answer, without labels, explanation, units not
requested by the problem, Markdown, or additional fields.
"""


class TotalRequestTimeout(TimeoutError):
    """Request exceeded the configured total wall-clock deadline."""


@contextmanager
def hard_timeout(seconds: float) -> Iterator[None]:
    """Enforce a total macOS wall-clock deadline around a blocking request."""
    previous_handler = signal.getsignal(signal.SIGALRM)

    def raise_timeout(_signal_number: int, _frame: Any) -> None:
        raise TotalRequestTimeout(f"request exceeded {seconds:.0f}s wall-clock limit")

    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


class BenchmarkRequestError(RuntimeError):
    """API request failure with retry classification."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class GalileoClient:
    """OpenAI-compatible client with bounded retries and native timing capture."""

    def __init__(self, presets: dict[str, dict[str, Any]] | None = None) -> None:
        self._presets = presets if presets is not None else {}

    def models(self) -> dict[str, dict[str, Any]]:
        """Return model metadata advertised by Galileo."""
        response = self._request_with_retry("GET", "/models")
        data = response.get("data", [])
        if not isinstance(data, list):
            raise TypeError("Models response data is not an array")
        return {
            str(item["id"]): item
            for item in data
            if isinstance(item, dict) and "id" in item
        }

    def properties(self, model: str) -> dict[str, Any]:
        """Return best-effort runtime properties for a loaded router model."""
        encoded_model = urllib.parse.quote(model, safe="")
        try:
            return self._request_with_retry("GET", f"/props?model={encoded_model}")
        except BenchmarkRequestError as error:
            if error.status_code == 404:
                LOGGER.info("Runtime /props unavailable in router mode for %s", model)
                return {"available": False, "reason": "router endpoint returned HTTP 404"}
            LOGGER.warning("Could not retrieve /props for %s: %s", model, error)
            return {"available": False, "reason": str(error)}
        except TypeError as error:
            LOGGER.warning("Invalid /props response for %s: %s", model, error)
            return {"available": False, "reason": str(error)}

    def solve(self, model: str, case: MathCase) -> ResponseMetrics:
        """Submit one math problem and extract the model's JSON answer."""
        prompt = build_math_prompt(case)
        disable_thinking = model in NO_THINKING_MODELS
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": not disable_thinking},
        }
        if BACKEND == "galileo":
            # llama.cpp dialect: native timing fields and the budget spelling.
            payload["timings_per_token"] = True
            payload["t_max_predict_ms"] = PREDICT_TIMEOUT_MS
            payload["thinking_budget_tokens"] = (
                0 if disable_thinking else THINKING_BUDGET_TOKENS
            )
        else:
            payload["thinking_budget"] = (
                0 if disable_thinking else THINKING_BUDGET_TOKENS
            )
        sampling = self._presets.get(model.casefold(), {})
        if sampling:
            payload.update(sampling)
        elif BACKEND == "omlx" and ":" in model:
            # oMLX "model:profile" aliases carry tuned server-side sampling.
            # Galileo ":TAG" aliases are router names — keep temperature.
            payload.pop("temperature")
        budget_key = (
            "thinking_budget_tokens" if BACKEND == "galileo" else "thinking_budget"
        )
        if "enable_thinking" in payload:
            thinking_on = bool(payload.pop("enable_thinking"))
            payload["chat_template_kwargs"]["enable_thinking"] = thinking_on
            if not thinking_on:
                payload[budget_key] = 0
        thinking_on = bool(payload["chat_template_kwargs"]["enable_thinking"])
        if (
            BACKEND == "galileo"
            and thinking_on
            and payload.get(budget_key, 0) >= payload["max_tokens"]
        ):
            # llama.cpp n_predict bounds reasoning+answer together — an
            # undersized cap starves the final content after thinking
            # consumes the budget. oMLX accounts thinking separately.
            LOGGER.warning(
                "%s=%s >= max_tokens=%s for %s: "
                "the answer may be truncated to empty",
                budget_key,
                payload.get(budget_key),
                payload["max_tokens"],
                model,
            )
        # oMLX emits an empty array when thinking mode meets a response_format
        # grammar, so thinking runs must parse the answer from plain text.
        use_schema = model not in NO_SCHEMA_MODELS and not (
            BACKEND == "omlx" and thinking_on
        )
        if use_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "math_answer",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["answer"],
                        "properties": {
                            "answer": {"type": "string", "minLength": 1},
                        },
                    },
                },
            }
        LOGGER.info("Request started model=%s case=%s", model, case.case_id)
        started = time.perf_counter()
        response = self._request_with_retry("POST", "/chat/completions", payload)
        elapsed = time.perf_counter() - started
        message = self._extract_message(response)
        content = message.get("content")
        text = content.strip() if isinstance(content, str) else ""
        reasoning_text = "".join(
            value
            for key in ("reasoning_content", "reasoning")
            if isinstance((value := message.get(key)), str) and value
        ).strip()
        if not text:
            raise TypeError("Model returned no final response content")
        usage = response.get("usage", {})
        timings = response.get("timings", {})
        if not isinstance(usage, dict):
            usage = {}
        if not isinstance(timings, dict):
            timings = {}
        prompt_tokens = _as_int(timings.get("prompt_n"), usage.get("prompt_tokens"))
        completion_tokens = _as_int(
            timings.get("predicted_n"), usage.get("completion_tokens")
        )
        # llama.cpp reports under `timings`; oMLX reports under `usage` with
        # durations in seconds and rates under different key names.
        prompt_ms = _as_float(timings.get("prompt_ms")) or (
            _as_float(usage.get("prompt_eval_duration")) * 1000
        )
        generation_ms = _as_float(timings.get("predicted_ms")) or (
            _as_float(usage.get("generation_duration")) * 1000
        )
        metrics = ResponseMetrics(
            text=text,
            reasoning_text=reasoning_text,
            elapsed_seconds=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_ms=prompt_ms,
            generation_ms=generation_ms,
            prompt_tokens_per_second=_as_float(timings.get("prompt_per_second"))
            or _as_float(usage.get("prompt_tokens_per_second")),
            output_tokens_per_second=_as_float(timings.get("predicted_per_second"))
            or _as_float(usage.get("generation_tokens_per_second")),
        )
        LOGGER.info(
            "Request finished model=%s case=%s elapsed=%.2fs "
            "prompt=%d PP=%.2f tok/s output=%d TG=%.2f tok/s",
            model,
            case.case_id,
            elapsed,
            prompt_tokens,
            metrics.prompt_tokens_per_second,
            completion_tokens,
            metrics.output_tokens_per_second,
        )
        return metrics

    @staticmethod
    def _extract_message(response: dict[str, Any]) -> dict[str, Any]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise TypeError("Response has no non-empty choices array")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise TypeError("Response choice is not an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise TypeError("Response message is not an object")
        return message

    def _request_with_retry(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: BenchmarkRequestError | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._request(method, path, payload)
            except BenchmarkRequestError as error:
                last_error = error
                if not error.retryable or attempt == MAX_RETRIES:
                    raise
                delay = 2**attempt
                LOGGER.warning(
                    "Transient request failure path=%s attempt=%d/%d retry_in=%ds: %s",
                    path,
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                    error,
                )
                time.sleep(delay)
        raise BenchmarkRequestError(
            "Request failed without a captured error", retryable=False
        ) from last_error

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
        )
        try:
            # Endpoint URL is operator-configured via *_BASE_URL env vars.
            with (
                hard_timeout(TIMEOUT_SECONDS),
                urllib.request.urlopen(  # nosec B310
                    request, timeout=TIMEOUT_SECONDS
                ) as response,
            ):
                parsed = json.load(response)
        except urllib.error.HTTPError as error:
            details = error.read().decode(errors="replace")
            raise BenchmarkRequestError(
                f"HTTP {error.code}: {details}",
                retryable=error.code == 429 or error.code >= 500,
                status_code=error.code,
            ) from error
        except (TimeoutError, urllib.error.URLError) as error:
            raise BenchmarkRequestError(
                f"Cannot reach {BASE_URL}: {error}", retryable=True
            ) from error
        except json.JSONDecodeError as error:
            raise BenchmarkRequestError(
                f"Invalid JSON response: {error}", retryable=True
            ) from error
        if not isinstance(parsed, dict):
            raise TypeError(f"Expected object response, got {type(parsed).__name__}")
        return parsed


def _as_int(primary: Any, fallback: Any = 0) -> int:
    """Convert a numeric API field to int with a fallback value."""
    value = primary if isinstance(primary, int | float) else fallback
    return int(value) if isinstance(value, int | float) else 0


def _as_float(value: Any) -> float:
    """Convert a numeric API field to float or zero."""
    return float(value) if isinstance(value, int | float) else 0.0


def parse_answer(response: str) -> str:
    """Extract the 'answer' field from the model's JSON, or fall back to the raw text."""
    candidate = response.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]) if len(lines) > 2 else candidate
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return response.strip()
    if isinstance(parsed, dict):
        answer = parsed.get("answer")
        if isinstance(answer, str):
            return answer.strip()
    return response.strip()


def _normalize(text: str) -> str:
    """Normalize a candidate math answer for comparison."""
    return (
        text.strip()
        .lower()
        .replace(" ", "")
        .replace("*", "")
        .replace("$", "")
        .replace("\\", "")
        .replace("(", "")
        .replace(")", "")
    )


def _matches_numeric(answer: str, expected: str, tolerance: float) -> bool:
    """Compare two numeric strings with a relative tolerance."""
    try:
        a = float(answer)
        b = float(expected)
    except ValueError:
        return False
    return math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance)


def _matches_answer(answer: str, case: MathCase) -> bool:
    """Return whether the candidate answer satisfies any accepted form."""
    if not answer:
        return False
    norm = _normalize(answer)
    for accepted in case.accepted:
        if case.is_numeric and _matches_numeric(answer, accepted, case.tolerance):
            return True
        if norm == _normalize(accepted) or _normalize(accepted) in norm:
            return True
    return False


def score_response(model: str, case: MathCase, metrics: ResponseMetrics) -> CaseResult:
    """Score the model's answer against the accepted answer set."""
    answer = parse_answer(metrics.text)
    correct = _matches_answer(answer, case)
    end_to_end_rate = (
        metrics.completion_tokens / metrics.elapsed_seconds
        if metrics.elapsed_seconds
        else 0.0
    )
    return CaseResult(
        model=model,
        case_id=case.case_id,
        category=case.category,
        score=100.0 if correct else 0.0,
        recall=100.0 if correct else 0.0,
        precision=100.0 if correct or not answer else 0.0,
        model_answer=answer,
        accepted=case.accepted,
        elapsed_seconds=round(metrics.elapsed_seconds, 3),
        prompt_tokens=metrics.prompt_tokens,
        completion_tokens=metrics.completion_tokens,
        prompt_ms=round(metrics.prompt_ms, 3),
        generation_ms=round(metrics.generation_ms, 3),
        prompt_tokens_per_second=round(metrics.prompt_tokens_per_second, 3),
        output_tokens_per_second=round(metrics.output_tokens_per_second, 3),
        end_to_end_tokens_per_second=round(end_to_end_rate, 3),
        response=metrics.text,
        reasoning_response=metrics.reasoning_text,
    )


def failed_result(model: str, case: MathCase, error: str) -> CaseResult:
    """Represent a failed request as a zero-scored result."""
    return CaseResult(
        model=model,
        case_id=case.case_id,
        category=case.category,
        score=0.0,
        recall=0.0,
        precision=0.0,
        model_answer="",
        accepted=case.accepted,
        elapsed_seconds=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        prompt_ms=0.0,
        generation_ms=0.0,
        prompt_tokens_per_second=0.0,
        output_tokens_per_second=0.0,
        end_to_end_tokens_per_second=0.0,
        response="",
        reasoning_response="",
        error=error,
    )


def summarize(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    """Aggregate scores and latency by model."""
    summaries: dict[str, dict[str, float]] = {}
    for model in MODELS:
        model_results = [result for result in results if result.model == model]
        if not model_results:
            continue
        scores = [result.score for result in model_results]
        successful = [result for result in model_results if result.error is None]
        pp_rates = [
            result.prompt_tokens_per_second
            for result in successful
            if result.prompt_tokens_per_second > 0
        ]
        output_rates = [
            result.output_tokens_per_second
            for result in successful
            if result.output_tokens_per_second > 0
        ]
        summaries[model] = {
            "completed_cases": float(len(model_results)),
            "failed_cases": float(sum(result.error is not None for result in model_results)),
            "math_score": round(mean(scores), 2) if scores else 0.0,
            "overall_score": round(mean(scores), 2) if scores else 0.0,
            "mean_prompt_tokens_per_second": round(mean(pp_rates), 2) if pp_rates else 0.0,
            "mean_output_tokens_per_second": (
                round(mean(output_rates), 2) if output_rates else 0.0
            ),
            "elapsed_seconds": round(
                sum(result.elapsed_seconds for result in model_results), 3
            ),
        }
    return summaries


def report_payload(
    results: list[CaseResult],
    model_parameters: dict[str, dict[str, Any]],
    presets_path: str = "",
) -> dict[str, Any]:
    """Build the detailed machine-readable benchmark report."""
    parameters: dict[str, Any] = {
        "backend": BACKEND,
        "models": list(MODELS),
        "math_cases": len(MATH_CASES),
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "request_timeout_seconds": TIMEOUT_SECONDS,
        "thinking_budget_tokens": THINKING_BUDGET_TOKENS,
        "no_thinking_models": sorted(NO_THINKING_MODELS),
        "no_schema_models": sorted(NO_SCHEMA_MODELS),
        "start_model": START_MODEL or None,
        "retry_failures": RETRY_FAILURES,
        "max_retries": MAX_RETRIES,
        "parallel_requests": 1,
        "prompt_version": PROMPT_VERSION,
        "response_format": "strict JSON object with one non-empty answer field",
        "presets_path": presets_path or None,
    }
    if BACKEND == "galileo":
        parameters["prediction_timeout_ms"] = PREDICT_TIMEOUT_MS
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": BASE_URL,
        "benchmark_parameters": parameters,
        "model_parameters": model_parameters,
        "summaries": summarize(results),
        "results": [asdict(result) for result in results],
    }


def atomic_write(path: Path, content: str) -> None:
    """Atomically replace a UTF-8 report file."""
    ensure_parent_directories(path)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def save_reports(
    results: list[CaseResult],
    model_parameters: dict[str, dict[str, Any]],
    presets_path: str = "",
) -> None:
    """Persist detailed JSON, CSV, and Markdown reports after every case."""
    payload = report_payload(results, model_parameters, presets_path)
    atomic_write(RESULTS_PATH, json.dumps(payload, indent=2) + "\n")
    write_csv(results)
    write_markdown(payload)


def write_csv(results: list[CaseResult]) -> None:
    """Write flat per-case metrics for spreadsheet analysis."""
    ensure_parent_directories(CSV_PATH)
    temporary_path = CSV_PATH.with_suffix(f"{CSV_PATH.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for result in results:
            row = asdict(result)
            row["accepted"] = " | ".join(result.accepted)
            writer.writerow(row)
    temporary_path.replace(CSV_PATH)


def write_markdown(payload: dict[str, Any]) -> None:
    """Write a readable benchmark report with rankings and parameters."""
    summaries = payload["summaries"]
    ranked = sorted(
        summaries.items(), key=lambda item: item[1]["overall_score"], reverse=True
    )
    lines = [
        f"# {BACKEND_NAMES[BACKEND]} Math Benchmark",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Results",
        "",
        "| Rank | Model | Overall | Math Score | PP tok/s | Output tok/s | Failures |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for rank, (model, summary) in enumerate(ranked, start=1):
        lines.append(
            f"| {rank} | `{model}` | {summary['overall_score']:.2f} | "
            f"{summary['math_score']:.2f} | "
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
        "| Model | Case | Category | Score | Recall | Precision | Model Answer | Accepted | PP tok/s | Output tok/s | Seconds | Error |",
        "|---|---|---|---:|---:|---:|---|---|---:|---:|---:|---|",
    ])
    for result in payload["results"]:
        lines.append(
            f"| `{result['model']}` | {result['case_id']} | {result['category']} | "
            f"{result['score']:.2f} | {result['recall']:.2f} | {result['precision']:.2f} | "
            f"{result['model_answer'] or ''} | {' | '.join(result['accepted'])} | "
            f"{result['prompt_tokens_per_second']:.2f} | {result['output_tokens_per_second']:.2f} | "
            f"{result['elapsed_seconds']:.2f} | {result['error'] or ''} |"
        )
    atomic_write(REPORT_PATH, "\n".join(lines) + "\n")


def load_presets(path: Path) -> dict[str, dict[str, Any]]:
    """Load llama.cpp server-style sampling presets keyed by model alias."""
    if not path.exists():
        LOGGER.warning("Presets file not found: %s", path)
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.lstrip().startswith("["):
        raw = f"[__metadata__]\n{raw}"
    parser = configparser.ConfigParser()
    parser.read_string(raw)
    presets: dict[str, dict[str, Any]] = {}
    for section in parser.sections():
        normalized = section.casefold()
        if normalized in {"__metadata__", "*"}:
            continue
        options = {option.casefold() for option in parser.options(section)}
        sampling: dict[str, Any] = {}
        for preset_key, api_key in PRESET_SAMPLING_KEYS.items():
            if preset_key not in options:
                continue
            value = parser.get(section, preset_key).strip()
            if not value:
                continue
            try:
                if api_key in INT_SAMPLING_KEYS:
                    parsed: bool | int | float = int(value)
                elif api_key in BOOL_SAMPLING_KEYS:
                    parsed = value.casefold() in {"1", "true", "yes", "on"}
                else:
                    parsed = float(value)
            except ValueError:
                LOGGER.warning(
                    "Invalid %s value %r in preset [%s]; ignoring",
                    preset_key,
                    value,
                    section,
                )
                continue
            sampling[api_key] = parsed
        if sampling:
            presets[normalized] = sampling
    return presets


def load_results() -> tuple[list[CaseResult], dict[str, dict[str, Any]]]:
    """Resume compatible partial results instead of repeating completed requests."""
    if not RESULTS_PATH.exists():
        return [], {}
    try:
        payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            LOGGER.warning("Ignoring incompatible prior results at %s", RESULTS_PATH)
            return [], {}
        results = [
            CaseResult(**{
                **item,
                "accepted": tuple(item["accepted"]),
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
    existing_paths = tuple(
        path
        for path in (RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH)
        if path.exists()
    )
    if not existing_paths:
        return None
    archive_directory = MATH_ARCHIVES_DIR / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    archive_directory.mkdir(parents=True, exist_ok=False)
    for path in existing_paths:
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


def print_summary(results: list[CaseResult]) -> None:
    """Display final model rankings in the terminal."""
    LOGGER.info("Final results")
    LOGGER.info(
        "%-45s %8s %8s %10s %10s %6s",
        "MODEL",
        "OVERALL",
        "MATH",
        "PP TOK/S",
        "OUT TOK/S",
        "FAIL",
    )
    for model, summary in sorted(
        summarize(results).items(),
        key=lambda item: item[1]["overall_score"],
        reverse=True,
    ):
        LOGGER.info(
            "%-45s %8.2f %8.2f %10.2f %10.2f %6.0f",
            model,
            summary["overall_score"],
            summary["math_score"],
            summary["mean_prompt_tokens_per_second"],
            summary["mean_output_tokens_per_second"],
            summary["failed_cases"],
        )


def main(argv: list[str] | None = None) -> int:
    """Run or resume all math cases with one active model at a time."""
    parser = argparse.ArgumentParser(
        description="Advanced math benchmark for Galileo or oMLX models."
    )
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="galileo",
        help="Serving stack to benchmark: galileo (llama.cpp) or omlx (local MLX)",
    )
    parser.add_argument(
        "--presets",
        nargs="?",
        const="__default__",
        default=None,
        help="Load per-model sampling presets from an INI file "
        "(bare flag uses the backend's default preset file)",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    resolve_backend(args.backend)
    presets_path = args.presets
    if presets_path == "__default__":
        presets_path = str(
            CONFIG_DIR
            / ("presets-omlx.ini" if BACKEND == "omlx" else "presets.ini")
        )
    elif presets_path is None:
        presets_path = PRESETS_PATH
    presets: dict[str, dict[str, Any]] = {}
    if presets_path:
        presets = _load_backend_presets(Path(presets_path))
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
    client = GalileoClient(presets)
    results, model_parameters = load_results()
    completed = {
        (result.model, result.case_id)
        for result in results
        if not (RETRY_FAILURES and result.error is not None)
    }
    if START_MODEL:
        if START_MODEL not in MODELS:
            LOGGER.error(
                "%s_START_MODEL is not configured: %s", BACKEND.upper(), START_MODEL
            )
            return 1
        active_models = MODELS[MODELS.index(START_MODEL) :]
    else:
        active_models = MODELS
    LOGGER.info(
        "Benchmark start endpoint=%s models=%d cases=%d resumed=%d timeout=%.0fs",
        BASE_URL,
        len(active_models),
        len(MATH_CASES),
        len(results),
        TIMEOUT_SECONDS,
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
        for case_index, case in enumerate(MATH_CASES, start=1):
            if (model, case.case_id) in completed:
                LOGGER.info("Case skipped from resume model=%s case=%s", model, case.case_id)
                continue
            try:
                metrics = client.solve(model, case)
                result = score_response(model, case, metrics)
                if not loaded_once:
                    model_parameters[model]["runtime_props"] = client.properties(model)
                    loaded_once = True
                LOGGER.info(
                    "Case scored model=%s case=%s progress=%d/%d score=%.2f "
                    "recall=%.2f precision=%.2f",
                    model,
                    case.case_id,
                    case_index,
                    len(MATH_CASES),
                    result.score,
                    result.recall,
                    result.precision,
                )
            except (BenchmarkRequestError, IndexError, KeyError, TypeError, ValueError) as error:
                error_text = f"{type(error).__name__}: {error}"
                LOGGER.error("Case failed model=%s case=%s: %s", model, case.case_id, error_text)
                result = failed_result(model, case, error_text)
            results = [
                existing
                for existing in results
                if (existing.model, existing.case_id) != (model, case.case_id)
            ]
            results.append(result)
            completed.add((model, case.case_id))
            try:
                save_reports(results, model_parameters, presets_path)
            except OSError as error:
                LOGGER.error("Cannot save reports: %s", error)
                return 1
        LOGGER.info("Model %d/%d finished: %s", model_index, len(active_models), model)
    print_summary(results)
    LOGGER.info("JSON report: %s", RESULTS_PATH.resolve())
    LOGGER.info("CSV report: %s", CSV_PATH.resolve())
    LOGGER.info("Markdown report: %s", REPORT_PATH.resolve())
    LOGGER.info("Operation log: %s", LOG_PATH.resolve())
    return 1 if any(result.error for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
