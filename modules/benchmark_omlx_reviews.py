"""Code-quality and defensive-security benchmark for models hosted on local oMLX.

Reuses the review cases, prompts, scoring, and judge machinery from
benchmark_galileo_reviews while targeting the macOS oMLX server
(OpenAI-compatible API on MLX) instead of the remote llama.cpp Galileo host.

Differences from the Galileo runner:
- Requests stream (stream=true, stream_options.include_usage=true) because
  oMLX reports prompt/generation throughput only in the terminal usage chunk.
- Uses oMLX request fields (thinking_budget, repetition_penalty); llama.cpp
  extensions (timings_per_token, t_max_predict_ms) are not sent.
- Thinking is enabled by default (OMLX_THINKING=1, budget
  OMLX_THINKING_BUDGET=256) and response_format is dropped for that mode:
  oMLX returns an empty array when thinking meets a constrained grammar.
  Set OMLX_THINKING=0 for schema-constrained non-thinking runs.
- "model:profile" aliases keep the server-side oMLX profile sampling unless a
  presets-omlx.ini entry overrides it.
- A warm-up request per model absorbs one-time model_load_duration so case
  latency is not polluted by model loading.
- The judge defaults to a Galileo critic alias so judge calls do not force
  model reloads on the benchmarked oMLX server; override with --judge-url /
  --judge-model or the OMLX_JUDGE_* environment variables.
- OMLX_NO_THINKING_MODELS / OMLX_NO_SCHEMA_MODELS take comma-separated model
  IDs for per-model escape hatches (e.g. models whose chat template leaks
  reasoning into content instead of emitting think markers).
- Review cases are static JSON under test_definitions/<lang>.json selected
  with --lang; each language writes to its own report files because case IDs
  repeat across languages.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Final

from . import benchmark_galileo_reviews as galileo
from .benchmark_galileo_reviews import (
    BenchmarkRequestError,
    CaseResult,
    JudgeClient,
    ResponseMetrics,
    atomic_write,
    build_review_prompt,
    failed_result,
    hard_timeout,
    load_models,
    score_response,
)
from .benchmark_paths import (
    CONFIG_DIR,
    LOGS_DIR,
    OMLX_REVIEW_ARCHIVES_DIR,
    OMLX_REVIEW_RESULTS_DIR,
    TEST_DEFINITIONS_DIR,
    ensure_parent_directories,
)
from .review_definitions import (
    ReviewCase,
    available_languages,
    load_case_definitions,
    review_artifact_paths,
)

_as_int = galileo._as_int
_as_float = galileo._as_float

BASE_URL: Final = os.getenv("OMLX_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
API_KEY: Final = os.getenv("OMLX_API_KEY", galileo._load_local_judge_key())
TIMEOUT_SECONDS: Final = float(os.getenv("OMLX_TIMEOUT_SECONDS", "300"))
MAX_TOKENS: Final = int(os.getenv("OMLX_MAX_TOKENS", "1024"))
MAX_RETRIES: Final = int(os.getenv("OMLX_MAX_RETRIES", "1"))
THINKING_ENABLED: Final = os.getenv("OMLX_THINKING", "1").casefold() in {
    "1",
    "true",
    "yes",
}
THINKING_BUDGET_TOKENS: Final = int(os.getenv("OMLX_THINKING_BUDGET", "256"))
START_MODEL: Final = os.getenv("OMLX_START_MODEL", "")
RETRY_FAILURES: Final = os.getenv("OMLX_RETRY_FAILURES", "0").casefold() in {
    "1",
    "true",
    "yes",
}
WARMUP_ENABLED: Final = os.getenv("OMLX_WARMUP", "1").casefold() in {
    "1",
    "true",
    "yes",
}
PRESETS_PATH: Final = os.getenv("OMLX_PRESETS", "")
MODELS_PATH: Final = Path(
    os.getenv("OMLX_MODELS", str(CONFIG_DIR / "models-omlx.json"))
)
JUDGE_MODEL: Final = os.getenv("OMLX_JUDGE_MODEL", "critic-ornith:LATEST")
JUDGE_BASE_URL: Final = os.getenv(
    "OMLX_JUDGE_BASE_URL", "http://127.0.0.1:8080/v1"
).rstrip("/")
JUDGE_API_KEY: Final = os.getenv("OMLX_JUDGE_API_KEY") or (
    galileo.JUDGE_API_KEY if JUDGE_BASE_URL == galileo.JUDGE_BASE_URL else "sk-noauth"
)
JUDGE_TIMEOUT: Final = float(
    os.getenv("OMLX_JUDGE_TIMEOUT", str(galileo.JUDGE_TIMEOUT))
)

PRESET_SAMPLING_KEYS: Final[dict[str, str]] = {
    "temp": "temperature",
    "temperature": "temperature",
    "top-p": "top_p",
    "top-k": "top_k",
    "min-p": "min_p",
    "repeat-penalty": "repetition_penalty",
    "repetition-penalty": "repetition_penalty",
    "repeat-context-size": "repetition_context_size",
    "presence-penalty": "presence_penalty",
    "frequency-penalty": "frequency_penalty",
    "max-tokens": "max_tokens",
    "thinking-budget": "thinking_budget",
    "reasoning-effort": "reasoning_effort",
    "enable-thinking": "enable_thinking",
    "specprefill": "specprefill",
    "specprefill-keep-pct": "specprefill_keep_pct",
    "specprefill-threshold": "specprefill_threshold",
    "seed": "seed",
    "xtc-probability": "xtc_probability",
    "xtc-threshold": "xtc_threshold",
}
INT_SAMPLING_KEYS: Final = frozenset({
    "top_k",
    "max_tokens",
    "thinking_budget",
    "repetition_context_size",
    "specprefill_threshold",
    "seed",
})
BOOL_SAMPLING_KEYS: Final = frozenset({"enable_thinking", "specprefill"})
RAW_SAMPLING_KEYS: Final = frozenset({"reasoning_effort"})
NO_THINKING_MODELS: Final[frozenset[str]] = frozenset(
    model.strip().casefold()
    for model in os.getenv("OMLX_NO_THINKING_MODELS", "").split(",")
    if model.strip()
)
NO_SCHEMA_MODELS: Final[frozenset[str]] = frozenset(
    model.strip().casefold()
    for model in os.getenv("OMLX_NO_SCHEMA_MODELS", "").split(",")
    if model.strip()
)
def report_paths_for(language: str) -> tuple[Path, Path, Path, Path]:
    """Return results, CSV, Markdown, and log paths for a language."""
    return review_artifact_paths(
        OMLX_REVIEW_RESULTS_DIR,
        LOGS_DIR,
        "omlx-review",
        language,
        results_env="OMLX_REVIEW_RESULTS",
        log_env="OMLX_REVIEW_LOG",
    )


# Rebound by resolve_report_paths() once --lang is known.
RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH = report_paths_for("python")


def resolve_report_paths(language: str) -> None:
    """Rebind module report paths to per-language artifacts."""
    global RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH
    RESULTS_PATH, CSV_PATH, REPORT_PATH, LOG_PATH = report_paths_for(language)
SCHEMA_VERSION: Final = 3
PROMPT_VERSION: Final = galileo.PROMPT_VERSION
LOGGER: Final = logging.getLogger("omlx-benchmark")

MODELS: Final[tuple[str, ...]] = load_models(MODELS_PATH)
CASES: Final = load_case_definitions(TEST_DEFINITIONS_DIR / "python.json")


class ConfigurableJudgeClient(JudgeClient):
    """Judge client bound to an explicit endpoint, key, and timeout."""

    def __init__(
        self, model: str, base_url: str, api_key: str, timeout: float
    ) -> None:
        super().__init__(model)
        self._judge_base_url = base_url
        self._judge_api_key = api_key
        self._judge_timeout = timeout

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one judge request to the configured endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._judge_api_key:
            headers["Authorization"] = f"Bearer {self._judge_api_key}"
        request = urllib.request.Request(
            f"{self._judge_base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            method="POST",
            headers=headers,
        )
        # Endpoint URL is operator-configured via *_BASE_URL env vars.
        with (
            hard_timeout(self._judge_timeout),
            urllib.request.urlopen(  # nosec B310
                request, timeout=self._judge_timeout
            ) as response,
        ):
            parsed = json.load(response)
        if not isinstance(parsed, dict):
            raise TypeError(f"Expected object response, got {type(parsed).__name__}")
        return parsed

    def _judge(self, prompt: str) -> str:
        """Run one judge prompt with thinking disabled for the verdict."""
        cache_key = f"{self._model}\n{prompt}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": galileo.JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": galileo.JUDGE_MAX_TOKENS,
            "thinking_budget": 0,
            "stream": False,
            "response_format": galileo.JUDGE_SCHEMA,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            response = self._request(payload)
            verdict = self._parse_verdict(response)
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            LOGGER.warning("Judge request failed, falling back to no: %s", error)
            verdict = "no"
        self._cache[cache_key] = verdict
        return verdict


class OmlxClient:
    """Streaming OpenAI-compatible client for the local oMLX server."""

    def __init__(self, presets: dict[str, dict[str, Any]] | None = None) -> None:
        self._presets = presets if presets is not None else {}
        self.last_model_load_seconds = 0.0

    def models(self) -> dict[str, dict[str, Any]]:
        """Return model metadata advertised by oMLX."""
        response = self._request_with_retry("GET", "/models")
        data = response.get("data", [])
        if not isinstance(data, list):
            raise TypeError("Models response data is not an array")
        return {
            str(item["id"]): item
            for item in data
            if isinstance(item, dict) and "id" in item
        }

    def build_payload(self, model: str, case: ReviewCase) -> dict[str, Any]:
        """Assemble one oMLX streaming chat request for a review case."""
        prompt = build_review_prompt(case)
        # oMLX emits an empty array when thinking mode meets a response_format
        # grammar, so thinking (OMLX_THINKING=1) requires dropping the schema.
        use_schema = (
            not THINKING_ENABLED and model.casefold() not in NO_SCHEMA_MODELS
        )
        disable_thinking = use_schema or model.casefold() in NO_THINKING_MODELS
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "stream": True,
            "stream_options": {"include_usage": True},
            "thinking_budget": 0 if disable_thinking else THINKING_BUDGET_TOKENS,
            "chat_template_kwargs": {"enable_thinking": not disable_thinking},
        }
        if use_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "review_findings",
                    "strict": True,
                    "schema": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "issue",
                                "affected_code",
                                "impact",
                                "remediation",
                            ],
                            "properties": {
                                field: {"type": "string", "minLength": 1}
                                for field in (
                                    "issue",
                                    "affected_code",
                                    "impact",
                                    "remediation",
                                )
                            },
                        },
                    },
                },
            }
        sampling = self._presets.get(model.casefold(), {})
        if sampling:
            payload.update(sampling)
            if "enable_thinking" in sampling:
                # oMLX rejects contradictory top-level and template controls.
                payload["chat_template_kwargs"]["enable_thinking"] = bool(
                    sampling["enable_thinking"]
                )
                if not sampling["enable_thinking"]:
                    payload["thinking_budget"] = 0
                if use_schema and sampling["enable_thinking"]:
                    LOGGER.warning(
                        "Preset enables thinking under response_format for %s; "
                        "oMLX returns empty arrays for that combination",
                        model,
                    )
        elif ":" in model:
            # oMLX "model:profile" aliases carry tuned server-side sampling.
            payload.pop("temperature")
        return payload

    def review(self, model: str, case: ReviewCase) -> ResponseMetrics:
        """Stream one bounded review request and map oMLX usage metrics."""
        payload = self.build_payload(model, case)
        LOGGER.info("Request started model=%s case=%s", model, case.case_id)
        started = time.perf_counter()
        content, reasoning_text, usage = self._stream_with_retry(payload)
        elapsed = time.perf_counter() - started
        text = content.strip()
        if not text:
            raise TypeError("Model returned no final response content")
        self.last_model_load_seconds = _as_float(usage.get("model_load_duration"))
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        completion_tokens = _as_int(usage.get("completion_tokens"))
        metrics = ResponseMetrics(
            text=text,
            reasoning_text=reasoning_text.strip(),
            elapsed_seconds=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_ms=_as_float(usage.get("prompt_eval_duration")) * 1000,
            generation_ms=_as_float(usage.get("generation_duration")) * 1000,
            prompt_tokens_per_second=_as_float(
                usage.get("prompt_tokens_per_second")
            ),
            output_tokens_per_second=_as_float(
                usage.get("generation_tokens_per_second")
            ),
            draft_tokens=0,
            accepted_draft_tokens=0,
        )
        LOGGER.info(
            "Request finished model=%s case=%s score_pending elapsed=%.2fs "
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

    def warmup(self, model: str) -> float:
        """Send a minimal request so model loading stays out of the first case."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK"}],
            "temperature": 0,
            "max_tokens": 4,
            "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            _, _, usage = self._stream_with_retry(payload)
        except BenchmarkRequestError as error:
            LOGGER.warning("Warm-up request failed for %s: %s", model, error)
            return 0.0
        load_seconds = _as_float(usage.get("model_load_duration"))
        LOGGER.info("Warm-up finished model=%s load=%.2fs", model, load_seconds)
        return load_seconds

    @staticmethod
    def _collect_stream(stream: Iterable[bytes]) -> tuple[str, str, dict[str, Any]]:
        """Accumulate SSE deltas and capture the terminal usage chunk."""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict[str, Any] = {}
        for raw_line in stream:
            line = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else raw_line
            ).strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if not isinstance(chunk, dict):
                continue
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict):
                usage = chunk_usage
            choices = chunk.get("choices")
            if not isinstance(choices, list):
                continue
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                if isinstance(delta.get("content"), str):
                    content_parts.append(delta["content"])
                if isinstance(delta.get("reasoning_content"), str):
                    reasoning_parts.append(delta["reasoning_content"])
        return "".join(content_parts), "".join(reasoning_parts), usage

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

    def _stream_with_retry(
        self, payload: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]]:
        last_error: BenchmarkRequestError | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._stream_chat(payload)
            except BenchmarkRequestError as error:
                last_error = error
                if not error.retryable or attempt == MAX_RETRIES:
                    raise
                delay = 2**attempt
                LOGGER.warning(
                    "Transient stream failure attempt=%d/%d retry_in=%ds: %s",
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

    def _stream_chat(self, payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        request = urllib.request.Request(
            f"{BASE_URL}/chat/completions",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
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
                return self._collect_stream(response)
        except urllib.error.HTTPError as error:
            details = error.read().decode(errors="replace")
            raise BenchmarkRequestError(
                f"HTTP {error.code}: {details}",
                # 409: oMLX reports a transient busy/unload-pending state.
                retryable=error.code in {409, 429} or error.code >= 500,
                status_code=error.code,
            ) from error
        except (TimeoutError, urllib.error.URLError) as error:
            raise BenchmarkRequestError(
                f"Cannot reach {BASE_URL}: {error}", retryable=True
            ) from error
        except json.JSONDecodeError as error:
            raise BenchmarkRequestError(
                f"Invalid JSON stream chunk: {error}", retryable=True
            ) from error


def load_presets(path: Path) -> dict[str, dict[str, Any]]:
    """Load oMLX-native per-request sampling presets keyed by model alias."""
    if not path.exists():
        LOGGER.warning("Presets file not found: %s", path)
        return {}
    raw = path.read_text(encoding="utf-8")
    # Bare key/value pairs before the first section break configparser; wrap them.
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
                    parsed: bool | int | float | str = int(value)
                elif api_key in BOOL_SAMPLING_KEYS:
                    parsed = value.casefold() in {"1", "true", "yes", "on"}
                elif api_key in RAW_SAMPLING_KEYS:
                    parsed = value
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


def summarize(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    """Aggregate quality, security, latency, and throughput by model."""
    summaries: dict[str, dict[str, float]] = {}
    for model in MODELS:
        model_results = [result for result in results if result.model == model]
        if not model_results:
            continue
        quality = [result.score for result in model_results if result.category == "quality"]
        security = [result.score for result in model_results if result.category == "security"]
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
            "quality_score": round(mean(quality), 2) if quality else 0.0,
            "security_score": round(mean(security), 2) if security else 0.0,
            "overall_score": round(mean(result.score for result in model_results), 2),
            "mean_prompt_tokens_per_second": round(mean(pp_rates), 2) if pp_rates else 0.0,
            "mean_output_tokens_per_second": (
                round(mean(output_rates), 2) if output_rates else 0.0
            ),
            "elapsed_seconds": round(sum(result.elapsed_seconds for result in model_results), 3),
        }
    return summaries


def report_payload(
    results: list[CaseResult],
    model_parameters: dict[str, dict[str, Any]],
    presets_path: str = "",
    judge_enabled: bool = False,
    judge_model: str = "",
    judge_base_url: str = "",
    language: str = "python",
    cases: tuple[ReviewCase, ...] = CASES,
) -> dict[str, Any]:
    """Build the detailed machine-readable benchmark report."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": BASE_URL,
        "benchmark_parameters": {
            "backend": "omlx",
            "models": list(MODELS),
            "language": language,
            "cases": len(cases),
            "quality_cases": sum(case.category == "quality" for case in cases),
            "security_cases": sum(case.category == "security" for case in cases),
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "request_timeout_seconds": TIMEOUT_SECONDS,
            "thinking_budget_tokens": THINKING_BUDGET_TOKENS,
            "thinking_enabled": THINKING_ENABLED,
            "stream": True,
            "warmup_enabled": WARMUP_ENABLED,
            "no_thinking_models": sorted(NO_THINKING_MODELS),
            "no_schema_models": sorted(NO_SCHEMA_MODELS),
            "start_model": START_MODEL or None,
            "retry_failures": RETRY_FAILURES,
            "max_retries": MAX_RETRIES,
            "parallel_requests": 1,
            "prompt_version": PROMPT_VERSION,
            "response_format": "strict JSON schema, maximum six distinct findings",
            "presets_path": presets_path or None,
            "judge_enabled": judge_enabled,
            "judge_model": judge_model or None,
            "judge_base_url": judge_base_url or None,
        },
        "model_parameters": model_parameters,
        "summaries": summarize(results),
        "results": [asdict(result) for result in results],
    }


def save_reports(
    results: list[CaseResult],
    model_parameters: dict[str, dict[str, Any]],
    presets_path: str = "",
    judge_enabled: bool = False,
    judge_model: str = "",
    judge_base_url: str = "",
    language: str = "python",
    cases: tuple[ReviewCase, ...] = CASES,
) -> None:
    """Persist detailed JSON, CSV, and Markdown reports after every case."""
    payload = report_payload(
        results,
        model_parameters,
        presets_path,
        judge_enabled=judge_enabled,
        judge_model=judge_model,
        judge_base_url=judge_base_url,
        language=language,
        cases=cases,
    )
    atomic_write(RESULTS_PATH, json.dumps(payload, indent=2) + "\n")
    write_csv(results)
    write_markdown(payload)


def write_csv(results: list[CaseResult]) -> None:
    """Write flat per-case metrics for spreadsheet analysis."""
    ensure_parent_directories(CSV_PATH)
    temporary_path = CSV_PATH.with_suffix(f"{CSV_PATH.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output:
        flat_fields = [
            key
            for key in asdict(results[0])
            if key not in {"response", "reasoning_response", "parsed_findings"}
        ]
        writer = csv.DictWriter(
            output, fieldnames=flat_fields, extrasaction="ignore"
        )
        writer.writeheader()
        for result in results:
            row = asdict(result)
            row["matched_findings"] = " | ".join(result.matched_findings)
            row["missed_findings"] = " | ".join(result.missed_findings)
            writer.writerow(row)
    temporary_path.replace(CSV_PATH)


def write_markdown(payload: dict[str, Any]) -> None:
    """Write a readable benchmark report with rankings and parameters."""
    summaries = payload["summaries"]
    ranked = sorted(
        summaries.items(), key=lambda item: item[1]["overall_score"], reverse=True
    )
    lines = [
        "# oMLX Model Review Benchmark",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Results",
        "",
        "| Rank | Model | Overall | Quality | Security | PP tok/s | Output tok/s | Failures |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, (model, summary) in enumerate(ranked, start=1):
        lines.append(
            f"| {rank} | `{model}` | {summary['overall_score']:.2f} | "
            f"{summary['quality_score']:.2f} | {summary['security_score']:.2f} | "
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
        "| Model | Case | Score | Recall | Precision | PP tok/s | Output tok/s | Seconds | Error |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for result in payload["results"]:
        lines.append(
            f"| `{result['model']}` | {result['case_id']} | {result['score']:.2f} | "
            f"{result['recall']:.2f} | {result['precision']:.2f} | "
            f"{result['prompt_tokens_per_second']:.2f} | "
            f"{result['output_tokens_per_second']:.2f} | "
            f"{result['elapsed_seconds']:.2f} | {result['error'] or ''} |"
        )
    atomic_write(REPORT_PATH, "\n".join(lines) + "\n")


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
                "matched_findings": tuple(item["matched_findings"]),
                "missed_findings": tuple(item["missed_findings"]),
                "parsed_findings": tuple(item.get("parsed_findings", ())),
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
    archive_directory = OMLX_REVIEW_ARCHIVES_DIR / datetime.now(UTC).strftime(
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
        "%-45s %8s %8s %8s %10s %10s %6s",
        "MODEL",
        "OVERALL",
        "QUALITY",
        "SECURITY",
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
            "%-45s %8.2f %8.2f %8.2f %10.2f %10.2f %6.0f",
            model,
            summary["overall_score"],
            summary["quality_score"],
            summary["security_score"],
            summary["mean_prompt_tokens_per_second"],
            summary["mean_output_tokens_per_second"],
            summary["failed_cases"],
        )


def main(argv: list[str] | None = None) -> int:
    """Run or resume all cases with one active oMLX model at a time."""
    parser = argparse.ArgumentParser(
        description=(
            "Code-quality and defensive-security benchmark for models hosted "
            "on the local oMLX server."
        )
    )
    parser.add_argument(
        "--presets",
        nargs="?",
        const=str(CONFIG_DIR / "presets-omlx.ini"),
        default=PRESETS_PATH,
        help="Load per-model sampling presets from an INI file",
    )
    parser.add_argument(
        "--judge",
        choices=["yes", "no"],
        default="no",
        help="Use an external LLM judge for semantic scoring instead of keyword matching",
    )
    parser.add_argument(
        "--judge-model",
        default=JUDGE_MODEL,
        help="Model alias to use as the judge when --judge=yes",
    )
    parser.add_argument(
        "--judge-url",
        default=JUDGE_BASE_URL,
        help=(
            "Base URL of the judge endpoint when --judge=yes; point at a "
            "remote host (e.g. Galileo) to avoid oMLX model reloads"
        ),
    )
    parser.add_argument(
        "--lang",
        default="python",
        choices=available_languages() or ["python"],
        help="Language whose static case definitions to benchmark",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    language: str = args.lang
    resolve_report_paths(language)
    try:
        cases = load_case_definitions(TEST_DEFINITIONS_DIR / f"{language}.json")
    except (FileNotFoundError, ValueError) as error:
        print(f"Cannot load case definitions: {error}", file=sys.stderr)
        return 1
    presets_path = args.presets
    presets: dict[str, dict[str, Any]] = {}
    if presets_path:
        presets = load_presets(Path(presets_path))
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
    client = OmlxClient(presets)
    judge: JudgeClient | None = None
    judge_url = args.judge_url.rstrip("/")
    if args.judge == "yes":
        judge_api_key = API_KEY if judge_url == BASE_URL else JUDGE_API_KEY
        judge = ConfigurableJudgeClient(
            args.judge_model, judge_url, judge_api_key, JUDGE_TIMEOUT
        )
        LOGGER.info("Judge enabled: %s at %s", args.judge_model, judge_url)
        if judge_url == BASE_URL:
            LOGGER.warning(
                "Judge shares the benchmarked oMLX server; expect model "
                "reloads between judge calls"
            )
    results, model_parameters = load_results()
    completed = {
        (result.model, result.case_id)
        for result in results
        if not (RETRY_FAILURES and result.error is not None)
    }
    if START_MODEL:
        if START_MODEL not in MODELS:
            LOGGER.error("OMLX_START_MODEL is not configured: %s", START_MODEL)
            return 1
        active_models = MODELS[MODELS.index(START_MODEL) :]
    else:
        active_models = MODELS
    LOGGER.info(
        "Benchmark start endpoint=%s lang=%s models=%d cases=%d resumed=%d timeout=%.0fs",
        BASE_URL,
        language,
        len(active_models),
        len(cases),
        len(results),
        TIMEOUT_SECONDS,
    )
    try:
        advertised_models = client.models()
    except (BenchmarkRequestError, TypeError) as error:
        LOGGER.error("Cannot list oMLX models: %s", error)
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
        if WARMUP_ENABLED and not loaded_once:
            load_seconds = client.warmup(model)
            if load_seconds:
                model_parameters[model]["model_load_seconds"] = load_seconds
        for case_index, case in enumerate(cases, start=1):
            if (model, case.case_id) in completed:
                LOGGER.info("Case skipped from resume model=%s case=%s", model, case.case_id)
                continue
            try:
                metrics = client.review(model, case)
                result = score_response(model, case, metrics, judge)
                if not loaded_once:
                    if client.last_model_load_seconds:
                        model_parameters[model]["model_load_seconds"] = (
                            client.last_model_load_seconds
                        )
                    loaded_once = True
                LOGGER.info(
                    "Case scored model=%s case=%s progress=%d/%d score=%.2f "
                    "recall=%.2f precision=%.2f",
                    model,
                    case.case_id,
                    case_index,
                    len(cases),
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
                save_reports(
                    results,
                    model_parameters,
                    presets_path,
                    judge_enabled=judge is not None,
                    judge_model=args.judge_model if judge is not None else "",
                    judge_base_url=judge_url if judge is not None else "",
                    language=language,
                    cases=cases,
                )
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
