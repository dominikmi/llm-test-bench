"""OpenCode agent benchmark using Serena, Headroom, and managed context."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from statistics import mean
from typing import Any, Final

import benchmark_galileo_reviews as galileo
from benchmark_galileo_reviews import (
    JUDGE_MODEL,
    MODELS,
    PROMPT_VERSION,
    JudgeClient,
    ResponseMetrics,
    ReviewCase,
    build_review_prompt,
    score_response,
)
from benchmark_paths import (
    LOGS_DIR,
    OPENCODE_AGENT_RESULTS_DIR,
    PROJECT_ROOT,
    TEST_DEFINITIONS_DIR,
    ensure_parent_directories,
)
from review_definitions import (
    LANGUAGE_DISPLAY_NAMES,
    LANGUAGE_EXTENSIONS,
    available_languages,
    language_suffix,
    load_case_definitions,
)


def load_session_modes(value: str | None = None) -> tuple[str, ...]:
    """Load unique fresh/continuing benchmark modes from configuration."""
    configured = value if value is not None else os.getenv(
        "OPENCODE_SESSION_MODES", "continuing,fresh"
    )
    modes = tuple(dict.fromkeys(part.strip().casefold() for part in configured.split(",")))
    if not modes or any(mode not in {"fresh", "continuing"} for mode in modes):
        raise ValueError(
            "OPENCODE_SESSION_MODES must contain fresh, continuing, or both"
        )
    return modes


ROOT: Final = PROJECT_ROOT
SUITE_ROOT: Final = ROOT / "opencode-agent-suite"
# Rebound by resolve_language() once --lang is known.
CASES: tuple[ReviewCase, ...] = galileo.CASES


def _serena_command(workspace: Path) -> tuple[str, ...]:
    """Build the Serena MCP launch command for one workspace."""
    return (
        "uvx",
        "--from",
        "serena-agent",
        "serena",
        "start-mcp-server",
        "--project",
        str(workspace),
        "--context",
        "agent",
        "--open-web-dashboard",
        "False",
    )


# Rebound by resolve_language() once --lang is known.
WORKSPACE = SUITE_ROOT / "workspaces" / "review-project"
SERENA_COMMAND = _serena_command(WORKSPACE)
RESULTS_PATH = OPENCODE_AGENT_RESULTS_DIR / "opencode-agent-results.json"
CSV_PATH = OPENCODE_AGENT_RESULTS_DIR / "opencode-agent-results.csv"
REPORT_PATH = OPENCODE_AGENT_RESULTS_DIR / "opencode-agent-results.md"
LOG_PATH = LOGS_DIR / "opencode-agent-results.log"
EVENTS_PATH = OPENCODE_AGENT_RESULTS_DIR / "opencode-agent-events.jsonl"
LANGUAGE = "python"
CASE_TIMEOUT_SECONDS: Final = float(os.getenv("OPENCODE_CASE_TIMEOUT", "360"))
TERMINATION_GRACE_SECONDS: Final = float(os.getenv("OPENCODE_TERMINATION_GRACE", "10"))
MAX_AGENT_STEPS: Final = int(os.getenv("OPENCODE_MAX_AGENT_STEPS", "8"))
SESSION_MODES: Final = load_session_modes()


def resolve_language(language: str) -> None:
    """Rebind the workspace, Serena command, artifacts, and cases for --lang.

    Case IDs repeat across languages, so each language gets its own report
    files and Serena workspace to keep resume keys and fixtures unambiguous.
    """
    global WORKSPACE, SERENA_COMMAND, RESULTS_PATH, CSV_PATH, REPORT_PATH
    global LOG_PATH, EVENTS_PATH, LANGUAGE, CASES
    suffix = language_suffix(language)
    WORKSPACE = SUITE_ROOT / "workspaces" / f"review-project{suffix}"
    SERENA_COMMAND = _serena_command(WORKSPACE)
    RESULTS_PATH = OPENCODE_AGENT_RESULTS_DIR / f"opencode-agent-results{suffix}.json"
    CSV_PATH = OPENCODE_AGENT_RESULTS_DIR / f"opencode-agent-results{suffix}.csv"
    REPORT_PATH = OPENCODE_AGENT_RESULTS_DIR / f"opencode-agent-results{suffix}.md"
    LOG_PATH = LOGS_DIR / f"opencode-agent-results{suffix}.log"
    EVENTS_PATH = OPENCODE_AGENT_RESULTS_DIR / f"opencode-agent-events{suffix}.jsonl"
    LANGUAGE = language
    if language != "python":
        CASES = load_case_definitions(
            TEST_DEFINITIONS_DIR / f"{language}.json"
        )


RETRY_FAILURES: Final = os.getenv("OPENCODE_RETRY_FAILURES", "1").casefold() in {
    "1",
    "true",
    "yes",
}
SCHEMA_VERSION: Final = 4
MAX_STEPS_SENTINEL: Final = "CRITICAL - MAXIMUM STEPS REACHED"
LOGGER: Final = logging.getLogger("opencode-agent-benchmark")
JUDGE_CLIENT: Final = JudgeClient(JUDGE_MODEL)


@dataclass(frozen=True, slots=True)
class OpenCodeRun:
    """Parsed output from one non-interactive OpenCode turn."""

    session_id: str
    response: str
    tools: tuple[str, ...]
    successful_tools: tuple[str, ...]
    tool_errors: int
    elapsed_seconds: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    exit_code: int
    stderr: str


@dataclass(frozen=True, slots=True)
class AgentCaseResult:
    """Quality, tool-compliance, and efficiency result for one agent turn."""

    model: str
    session_mode: str
    case_id: str
    category: str
    session_id: str
    content_score: float
    tool_score: float
    overall_score: float
    recall: float
    precision: float
    matched_findings: tuple[str, ...]
    missed_findings: tuple[str, ...]
    unsupported_findings: int
    serena_used: bool
    headroom_used: bool
    tools: tuple[str, ...]
    successful_tools: tuple[str, ...]
    tool_errors: int
    elapsed_seconds: float
    agent_end_to_end_tokens_per_second: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    response: str
    error: str | None


class OpenCodeExecutionError(RuntimeError):
    """OpenCode failure carrying the partial run for diagnostics and resumption."""

    def __init__(self, message: str, run: OpenCodeRun) -> None:
        super().__init__(message)
        self.run = run


class OpenCodeRunner:
    """Run bounded OpenCode turns and parse JSON events."""

    def run(
        self,
        model: str,
        case: ReviewCase,
        session_id: str | None,
        session_mode: str = "continuing",
    ) -> OpenCodeRun:
        """Execute one case in the requested session mode."""
        command = build_command(model, case, session_id, session_mode)
        LOGGER.info(
            "OpenCode turn started mode=%s model=%s case=%s session=%s "
            "timeout=%.0fs steps=%d",
            session_mode,
            model,
            case.case_id,
            session_id or "new",
            CASE_TIMEOUT_SECONDS,
            MAX_AGENT_STEPS,
        )
        started = time.perf_counter()
        process = subprocess.Popen(
            command,
            cwd=SUITE_ROOT,
            env=build_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=CASE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            terminate_process_group(process)
            try:
                stdout, stderr = process.communicate(timeout=TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
            elapsed = time.perf_counter() - started
            append_events(model, case.case_id, stdout)
            exit_code = process.returncode if process.returncode is not None else -1
            run = parse_events(stdout, stderr, exit_code, elapsed)
            raise OpenCodeExecutionError(
                f"OpenCode exceeded {CASE_TIMEOUT_SECONDS:.0f}s; process group terminated",
                run,
            ) from error
        elapsed = time.perf_counter() - started
        append_events(model, case.case_id, stdout)
        run = parse_events(stdout, stderr, process.returncode, elapsed)
        LOGGER.info(
            "OpenCode turn finished mode=%s model=%s case=%s elapsed=%.2fs exit=%d "
            "input=%d output=%d cache_read=%d tools=%s successful=%s errors=%d",
            session_mode,
            model,
            case.case_id,
            elapsed,
            run.exit_code,
            run.input_tokens,
            run.output_tokens,
            run.cache_read_tokens,
            ",".join(run.tools) or "none",
            ",".join(run.successful_tools) or "none",
            run.tool_errors,
        )
        if process.returncode != 0:
            raise OpenCodeExecutionError(
                f"OpenCode exited {process.returncode}: {stderr.strip() or run.response}",
                run,
            )
        return run


def build_command(
    model: str,
    case: ReviewCase,
    session_id: str | None,
    session_mode: str = "continuing",
) -> list[str]:
    """Build an externally plugin-free OpenCode command."""
    command = [
        "opencode",
        "run",
        "--pure",
        "--format",
        "json",
        "--agent",
        "benchmark-reviewer",
        "--model",
        f"galileo/{model}",
        "--dir",
        str(WORKSPACE),
        "--title",
        f"agent-benchmark-{session_mode}-{model}",
    ]
    if session_id:
        command.extend(("--session", session_id))
    command.append(build_prompt(case))
    return command


def build_environment() -> dict[str, str]:
    """Isolate Serena and restrict the agent while retaining configured providers."""
    content = os.getenv("OPENCODE_CONFIG_CONTENT", "")
    try:
        configuration = json.loads(content) if content else {}
    except json.JSONDecodeError as error:
        raise ValueError("OPENCODE_CONFIG_CONTENT must contain valid JSON") from error
    if not isinstance(configuration, dict):
        raise TypeError("OPENCODE_CONFIG_CONTENT must contain a JSON object")
    agents = configuration.setdefault("agent", {})
    if not isinstance(agents, dict):
        raise TypeError("OPENCODE_CONFIG_CONTENT.agent must be a JSON object")
    reviewer = agents.setdefault("benchmark-reviewer", {})
    if not isinstance(reviewer, dict):
        raise TypeError(
            "OPENCODE_CONFIG_CONTENT.agent.benchmark-reviewer must be a JSON object"
        )
    reviewer["steps"] = MAX_AGENT_STEPS
    permissions = reviewer.get("permission", {})
    if not isinstance(permissions, dict):
        raise TypeError(
            "OPENCODE_CONFIG_CONTENT.agent.benchmark-reviewer.permission must be a JSON object"
        )
    reviewer["permission"] = {
        "*": "deny",
        "serena_read_file": "allow",
        "headroom_headroom_compress": "allow",
    }
    mcp = configuration.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        raise TypeError("OPENCODE_CONFIG_CONTENT.mcp must be a JSON object")
    mcp["serena"] = {
        "type": "local",
        "command": list(SERENA_COMMAND),
        "enabled": True,
    }
    return {
        **os.environ,
        "NO_COLOR": "1",
        "OPENCODE_CONFIG_CONTENT": json.dumps(configuration),
    }


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Request graceful termination of an OpenCode process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def build_prompt(case: ReviewCase) -> str:
    """Build a path-specific prompt that requires both MCP services."""
    extension = LANGUAGE_EXTENSIONS.get(case.language, case.language)
    workflow = f"""1. Call `serena_read_file` for `cases/{case.case_id}/target.{extension}`.
2. Call `serena_read_file` for `cases/{case.case_id}/project_context.md`.
3. Build a concise evidence summary containing candidate root causes and exact affected code.
4. Call `headroom_headroom_compress` exactly once on that evidence summary.
5. Use the inspected files as authoritative evidence and the compressed summary only as an
   organizational aid. Then make no further tool calls and produce the final JSON response.
Do not use `load_tool`, onboarding, project activation, task delegation, native file-reading,
or any file-modifying tool."""
    return build_review_prompt(case, workflow=workflow)


def parse_events(stdout: str, stderr: str, exit_code: int, elapsed: float) -> OpenCodeRun:
    """Parse text, tool calls, session identity, and token accounting."""
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    session_id = next(
        (str(event["sessionID"]) for event in events if event.get("sessionID")), ""
    )
    text_parts: list[str] = []
    tools: list[str] = []
    successful_tools: list[str] = []
    tool_errors = 0
    input_tokens = output_tokens = reasoning_tokens = 0
    cache_read_tokens = cache_write_tokens = 0
    for event in events:
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        if event.get("type") == "text" and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        if event.get("type") == "tool_use" and isinstance(part.get("tool"), str):
            tool = part["tool"]
            tools.append(tool)
            state = part.get("state", {})
            if isinstance(state, dict):
                if tool_state_failed(state):
                    tool_errors += 1
                elif state.get("status") == "completed":
                    successful_tools.append(tool)
        if event.get("type") == "step_finish":
            tokens = part.get("tokens", {})
            if not isinstance(tokens, dict):
                continue
            input_tokens += numeric_int(tokens.get("input"))
            output_tokens += numeric_int(tokens.get("output"))
            reasoning_tokens += numeric_int(tokens.get("reasoning"))
            cache = tokens.get("cache", {})
            if isinstance(cache, dict):
                cache_read_tokens += numeric_int(cache.get("read"))
                cache_write_tokens += numeric_int(cache.get("write"))
    return OpenCodeRun(
        session_id=session_id,
        response=text_parts[-1].strip() if text_parts else "",
        tools=tuple(tools),
        successful_tools=tuple(successful_tools),
        tool_errors=tool_errors,
        elapsed_seconds=elapsed,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        exit_code=exit_code,
        stderr=stderr.strip(),
    )


def tool_state_failed(state: dict[str, Any]) -> bool:
    """Recognize explicit failures and completed calls carrying semantic errors."""
    if state.get("status") == "error":
        return True
    output = state.get("output")
    if not isinstance(output, str):
        return False
    if "Unknown tool:" in output or "No instructions found for" in output:
        return True
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and bool(parsed.get("error"))


def numeric_int(value: Any) -> int:
    """Convert numeric event fields to integers without accepting strings."""
    return int(value) if isinstance(value, int | float) else 0


def append_events(model: str, case_id: str, stdout: str) -> None:
    """Append raw OpenCode events with benchmark identity for auditability."""
    ensure_parent_directories(EVENTS_PATH)
    with EVENTS_PATH.open("a", encoding="utf-8") as output:
        for line in stdout.splitlines():
            output.write(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "model": model,
                        "case_id": case_id,
                        "event": line,
                    }
                )
                + "\n"
            )


def successful_required_tools(run: OpenCodeRun) -> tuple[bool, bool]:
    """Return successful Serena-read and Headroom-compression compliance."""
    return (
        run.successful_tools.count("serena_read_file") >= 2,
        "headroom_headroom_compress" in run.successful_tools,
    )


def run_validation_error(run: OpenCodeRun) -> str | None:
    """Reject control responses and runs missing successful required MCP calls."""
    if not run.response:
        return "OpenCode returned no final text"
    if run.response.startswith(MAX_STEPS_SENTINEL):
        return "OpenCode reached the maximum agent step limit"
    serena_used, headroom_used = successful_required_tools(run)
    missing = [
        name
        for name, used in (
            ("serena_read_file", serena_used),
            ("headroom_headroom_compress", headroom_used),
        )
        if not used
    ]
    if missing:
        suffix = f"; {run.tool_errors} tool call(s) failed" if run.tool_errors else ""
        return f"Missing successful required tool call(s): {', '.join(missing)}{suffix}"
    return None


def score_run(
    model: str,
    case: ReviewCase,
    run: OpenCodeRun,
    session_mode: str = "continuing",
) -> AgentCaseResult:
    """Combine content F1 with successful required MCP workflow compliance."""
    error = run_validation_error(run)
    if error is not None:
        return failed_result(
            model,
            case,
            run.session_id,
            error,
            run,
            session_mode=session_mode,
        )
    direct_metrics = ResponseMetrics(
        text=run.response,
        reasoning_text="",
        elapsed_seconds=run.elapsed_seconds,
        prompt_tokens=run.input_tokens,
        completion_tokens=run.output_tokens,
        prompt_ms=0.0,
        generation_ms=0.0,
        prompt_tokens_per_second=0.0,
        output_tokens_per_second=0.0,
        draft_tokens=0,
        accepted_draft_tokens=0,
    )
    content = score_response(model, case, direct_metrics, JUDGE_CLIENT)
    serena_used, headroom_used = successful_required_tools(run)
    tool_score = (float(serena_used) + float(headroom_used)) / 2 * 100
    overall_score = content.score * 0.8 + tool_score * 0.2
    end_to_end_rate = (
        run.output_tokens / run.elapsed_seconds if run.elapsed_seconds else 0.0
    )
    return AgentCaseResult(
        model=model,
        session_mode=session_mode,
        case_id=case.case_id,
        category=case.category,
        session_id=run.session_id,
        content_score=content.score,
        tool_score=round(tool_score, 2),
        overall_score=round(overall_score, 2),
        recall=content.recall,
        precision=content.precision,
        matched_findings=content.matched_findings,
        missed_findings=content.missed_findings,
        unsupported_findings=content.unsupported_findings,
        serena_used=serena_used,
        headroom_used=headroom_used,
        tools=run.tools,
        successful_tools=run.successful_tools,
        tool_errors=run.tool_errors,
        elapsed_seconds=round(run.elapsed_seconds, 3),
        agent_end_to_end_tokens_per_second=round(end_to_end_rate, 3),
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        reasoning_tokens=run.reasoning_tokens,
        cache_read_tokens=run.cache_read_tokens,
        cache_write_tokens=run.cache_write_tokens,
        response=run.response,
        error=error,
    )


def failed_result(
    model: str,
    case: ReviewCase,
    session_id: str,
    error: str,
    run: OpenCodeRun | None = None,
    *,
    session_mode: str = "continuing",
) -> AgentCaseResult:
    """Create a zero score while preserving evidence from a failed agent turn."""
    tools = run.tools if run else ()
    successful_tools = run.successful_tools if run else ()
    serena_used, headroom_used = successful_required_tools(run) if run else (False, False)
    end_to_end_rate = (
        run.output_tokens / run.elapsed_seconds if run and run.elapsed_seconds else 0.0
    )
    return AgentCaseResult(
        model=model,
        session_mode=session_mode,
        case_id=case.case_id,
        category=case.category,
        session_id=run.session_id if run and run.session_id else session_id,
        content_score=0.0,
        tool_score=0.0,
        overall_score=0.0,
        recall=0.0,
        precision=0.0,
        matched_findings=(),
        missed_findings=tuple(finding.name for finding in case.findings),
        unsupported_findings=0,
        serena_used=serena_used,
        headroom_used=headroom_used,
        tools=tools,
        successful_tools=successful_tools,
        tool_errors=run.tool_errors if run else 0,
        elapsed_seconds=round(run.elapsed_seconds, 3) if run else 0.0,
        agent_end_to_end_tokens_per_second=round(end_to_end_rate, 3),
        input_tokens=run.input_tokens if run else 0,
        output_tokens=run.output_tokens if run else 0,
        reasoning_tokens=run.reasoning_tokens if run else 0,
        cache_read_tokens=run.cache_read_tokens if run else 0,
        cache_write_tokens=run.cache_write_tokens if run else 0,
        response=run.response if run else "",
        error=error,
    )


def materialize_fixtures() -> None:
    """Create deterministic read-only-style repository fixtures for Serena."""
    display_name = LANGUAGE_DISPLAY_NAMES.get(LANGUAGE, LANGUAGE)
    context = f"""# Project context

This is production {display_name} code processing externally supplied data. Review only the target
file named in the task. Correctness, maintainability, performance, resource lifecycle, and
security boundaries matter. Inputs can be malformed. Services may run concurrently and
partial failures must remain observable. Recommend concrete changes rather than broad style
preferences. Do not assume omitted framework behavior or report vulnerabilities unsupported
by the shown data flow. Preserve the public behavior unless a defect requires changing it.
"""
    for case in CASES:
        extension = LANGUAGE_EXTENSIONS.get(case.language, case.language)
        case_directory = WORKSPACE / "cases" / case.case_id
        case_directory.mkdir(parents=True, exist_ok=True)
        (case_directory / f"target.{extension}").write_text(
            case.code, encoding="utf-8"
        )
        (case_directory / "project_context.md").write_text(context, encoding="utf-8")


def aggregate(
    results: list[AgentCaseResult], session_mode: str | None = None
) -> dict[str, dict[str, float]]:
    """Aggregate quality, security, MCP compliance, and latency by model and mode."""
    summaries: dict[str, dict[str, float]] = {}
    for model in MODELS:
        rows = [
            result
            for result in results
            if result.model == model
            and (session_mode is None or result.session_mode == session_mode)
        ]
        if not rows:
            continue
        quality = [row.content_score for row in rows if row.category == "quality"]
        security = [row.content_score for row in rows if row.category == "security"]
        total_seconds = sum(row.elapsed_seconds for row in rows)
        end_to_end_rate = (
            sum(row.output_tokens for row in rows) / total_seconds if total_seconds else 0.0
        )
        summaries[model] = {
            "completed_cases": float(len(rows)),
            "failed_cases": float(sum(row.error is not None for row in rows)),
            "quality_score": round(mean(quality), 2) if quality else 0.0,
            "security_score": round(mean(security), 2) if security else 0.0,
            "content_score": round(mean(row.content_score for row in rows), 2),
            "tool_compliance_score": round(mean(row.tool_score for row in rows), 2),
            "overall_score": round(mean(row.overall_score for row in rows), 2),
            "serena_usage_percent": round(mean(float(row.serena_used) for row in rows) * 100, 2),
            "headroom_usage_percent": round(mean(float(row.headroom_used) for row in rows) * 100, 2),
            "mean_seconds": round(mean(row.elapsed_seconds for row in rows), 2),
            "agent_end_to_end_tokens_per_second": round(end_to_end_rate, 2),
            "total_input_tokens": float(sum(row.input_tokens for row in rows)),
            "total_output_tokens": float(sum(row.output_tokens for row in rows)),
            "total_cache_read_tokens": float(sum(row.cache_read_tokens for row in rows)),
        }
    return summaries


def log_case_score(
    result: AgentCaseResult,
    case_index: int,
    total_cases: int,
    scoring_seconds: float | None,
) -> None:
    """Log task-level scores, finding matches, and scoring latency."""
    scoring_display = (
        f"{scoring_seconds:.2f}" if scoring_seconds is not None else "unavailable"
    )
    LOGGER.info(
        "Case scored mode=%s model=%s case=%s category=%s progress=%d/%d overall=%.2f "
        "content=%.2f MCP=%.2f recall=%.2f precision=%.2f unsupported=%d "
        "agent_seconds=%.2f agent_e2e_tps=%.2f scoring_seconds=%s",
        result.session_mode,
        result.model,
        result.case_id,
        result.category,
        case_index,
        total_cases,
        result.overall_score,
        result.content_score,
        result.tool_score,
        result.recall,
        result.precision,
        result.unsupported_findings,
        result.elapsed_seconds,
        result.agent_end_to_end_tokens_per_second,
        scoring_display,
    )
    LOGGER.info(
        "Case findings mode=%s model=%s case=%s matched=%s missed=%s error=%s",
        result.session_mode,
        result.model,
        result.case_id,
        ",".join(result.matched_findings) or "none",
        ",".join(result.missed_findings) or "none",
        result.error or "none",
    )


def log_model_score(
    session_mode: str, model: str, results: list[AgentCaseResult]
) -> None:
    """Log the running aggregate for one model and session mode."""
    summary = aggregate(results, session_mode).get(model)
    if summary is None:
        return
    LOGGER.info(
        "Model score mode=%s model=%s completed=%.0f/%d failed=%.0f overall=%.2f "
        "content=%.2f quality=%.2f security=%.2f MCP=%.2f mean_seconds=%.2f "
        "agent_e2e_tps=%.2f",
        session_mode,
        model,
        summary["completed_cases"],
        len(CASES),
        summary["failed_cases"],
        summary["overall_score"],
        summary["content_score"],
        summary["quality_score"],
        summary["security_score"],
        summary["tool_compliance_score"],
        summary["mean_seconds"],
        summary["agent_end_to_end_tokens_per_second"],
    )


def atomic_write(path: Path, content: str) -> None:
    """Atomically replace one UTF-8 report."""
    ensure_parent_directories(path)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def save_reports(results: list[AgentCaseResult], sessions: dict[str, str]) -> None:
    """Persist resumable JSON plus CSV and Markdown reports."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "opencode_version": opencode_version(),
            "agent": "benchmark-reviewer",
            "mcp_servers": ["serena", "headroom"],
            "context_management": {
                "auto_compaction": True,
                "preserve_recent_tokens": 8000,
                "reserved_tokens": 32768,
                "session_modes": list(SESSION_MODES),
                "continuing_scope": "one continuing session per model",
                "fresh_scope": "one new session per case",
            },
            "case_timeout_seconds": CASE_TIMEOUT_SECONDS,
            "language": LANGUAGE,
            "review_prompt_version": PROMPT_VERSION,
            "max_agent_steps": MAX_AGENT_STEPS,
            "external_plugins_disabled": True,
            "serena_project": str(WORKSPACE),
            "allowed_agent_tools": [
                "serena_read_file",
                "headroom_headroom_compress",
            ],
            "retry_failures": RETRY_FAILURES,
            "preflight_cases_per_model_per_mode": 1,
            "models": list(MODELS),
            "cases_per_model": len(CASES),
            "content_weight": 0.8,
            "tool_compliance_weight": 0.2,
        },
        "sessions": sessions,
        "summaries": aggregate(results),
        "summaries_by_session_mode": {
            session_mode: aggregate(results, session_mode)
            for session_mode in SESSION_MODES
        },
        "results": [asdict(result) for result in results],
    }
    atomic_write(RESULTS_PATH, json.dumps(payload, indent=2) + "\n")
    write_csv(results)
    write_markdown(payload)


def write_csv(results: list[AgentCaseResult]) -> None:
    """Write flat case-level agent measurements."""
    if not results:
        return
    ensure_parent_directories(CSV_PATH)
    temporary = CSV_PATH.with_suffix(f"{CSV_PATH.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for result in results:
            row = asdict(result)
            for key in (
                "matched_findings",
                "missed_findings",
                "tools",
                "successful_tools",
            ):
                row[key] = " | ".join(row[key])
            writer.writerow(row)
    temporary.replace(CSV_PATH)


def write_markdown(payload: dict[str, Any]) -> None:
    """Write mode-specific rankings and complete benchmark configuration."""
    lines = [
        "# OpenCode + Serena + Headroom Benchmark",
        "",
        f"Generated: `{payload['generated_at']}`",
    ]
    for session_mode, summaries in payload["summaries_by_session_mode"].items():
        ranked = sorted(
            summaries.items(),
            key=lambda item: item[1]["overall_score"],
            reverse=True,
        )
        lines.extend(
            (
                "",
                f"## {session_mode.title()} sessions",
                "",
                (
                    "| Rank | Model | Overall | Content | Quality | Security | MCP | "
                    "Agent E2E t/s | Mean s | Fail |"
                ),
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            )
        )
        for rank, (model, summary) in enumerate(ranked, start=1):
            lines.append(
                f"| {rank} | `{model}` | {summary['overall_score']:.2f} | "
                f"{summary['content_score']:.2f} | {summary['quality_score']:.2f} | "
                f"{summary['security_score']:.2f} | "
                f"{summary['tool_compliance_score']:.2f} | "
                f"{summary['agent_end_to_end_tokens_per_second']:.2f} | "
                f"{summary['mean_seconds']:.2f} | {summary['failed_cases']:.0f} |"
            )
    lines.extend(("", "## Configuration", "", "```json"))
    lines.append(json.dumps(payload["configuration"], indent=2))
    lines.extend(("```", "", "## Sessions", "", "```json"))
    lines.append(json.dumps(payload["sessions"], indent=2))
    lines.extend(("```", "", "## Case results", ""))
    lines.append(
        "| Mode | Model | Case | Overall | Content | MCP | Recall | Precision | "
        "Agent E2E t/s | Input | Output | Cache read | Seconds | Error |"
    )
    lines.append(
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    )
    for result in payload["results"]:
        lines.append(
            f"| {result['session_mode']} | `{result['model']}` | {result['case_id']} | "
            f"{result['overall_score']:.2f} | {result['content_score']:.2f} | "
            f"{result['tool_score']:.2f} | {result['recall']:.2f} | "
            f"{result['precision']:.2f} | "
            f"{result['agent_end_to_end_tokens_per_second']:.2f} | "
            f"{result['input_tokens']} | {result['output_tokens']} | "
            f"{result['cache_read_tokens']} | {result['elapsed_seconds']:.2f} | "
            f"{result['error'] or ''} |"
        )
    atomic_write(REPORT_PATH, "\n".join(lines) + "\n")


def session_key(session_mode: str, model: str) -> str:
    """Return the persistent key for a mode-specific model session."""
    return f"{session_mode}:{model}"


def deserialize_result(row: dict[str, Any]) -> AgentCaseResult:
    """Load current results and migrate legacy continuing-session rows."""
    elapsed = row.get("elapsed_seconds", 0.0)
    output_tokens = row.get("output_tokens", 0)
    rate = (
        float(output_tokens) / float(elapsed)
        if isinstance(output_tokens, int | float)
        and isinstance(elapsed, int | float)
        and elapsed
        else 0.0
    )
    return AgentCaseResult(**{
        **row,
        "session_mode": row.get("session_mode", "continuing"),
        "agent_end_to_end_tokens_per_second": row.get(
            "agent_end_to_end_tokens_per_second", round(rate, 3)
        ),
        "matched_findings": tuple(row["matched_findings"]),
        "missed_findings": tuple(row["missed_findings"]),
        "tools": tuple(row["tools"]),
        "successful_tools": tuple(row["successful_tools"]),
    })


def load_state() -> tuple[list[AgentCaseResult], dict[str, str]]:
    """Load partial results and migrate legacy continuing-session state."""
    if not RESULTS_PATH.exists():
        return [], {}
    try:
        payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            return [], {}
        rows = payload.get("results", [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise TypeError("results must be an array of objects")
        results = [deserialize_result(row) for row in rows]
        raw_sessions = payload.get("sessions", {})
        if not isinstance(raw_sessions, dict):
            return results, {}
        sessions = {
            key
            if key.startswith(("fresh:", "continuing:"))
            else session_key("continuing", key): value
            for key, value in raw_sessions.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        return results, sessions
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        LOGGER.warning("Ignoring invalid resume state: %s", error)
        return [], {}


@cache
def opencode_version() -> str:
    """Return the installed OpenCode version once for reproducibility."""
    try:
        return subprocess.run(
            ("opencode", "--version"),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def configure_logging() -> None:
    """Log every operation to the terminal and an append-only file."""
    ensure_parent_directories(LOG_PATH)
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH)):
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    """Run fresh and continuing OpenCode session modes with resumable results."""
    parser = argparse.ArgumentParser(
        description=(
            "OpenCode agent benchmark using Serena, Headroom, and managed context."
        )
    )
    parser.add_argument(
        "--lang",
        default="python",
        choices=available_languages() or ["python"],
        help="Language whose static case definitions to benchmark",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        resolve_language(args.lang)
    except (FileNotFoundError, ValueError) as error:
        print(f"Cannot load case definitions: {error}", file=sys.stderr)
        return 1
    configure_logging()
    materialize_fixtures()
    results, sessions = load_state()
    completed = {
        (result.session_mode, result.model, result.case_id)
        for result in results
        if result.error is None or not RETRY_FAILURES
    }
    runner = OpenCodeRunner()
    LOGGER.info(
        "Agent benchmark started lang=%s modes=%s models=%d cases=%d resumed=%d "
        "MCP=serena,headroom pure=true steps=%d retry_failures=%s",
        LANGUAGE,
        ",".join(SESSION_MODES),
        len(MODELS),
        len(CASES),
        len(results),
        MAX_AGENT_STEPS,
        RETRY_FAILURES,
    )
    for session_mode in SESSION_MODES:
        LOGGER.info("Session mode started: %s", session_mode)
        for model_index, model in enumerate(MODELS, start=1):
            model_session_key = session_key(session_mode, model)
            session_id = (
                sessions.get(model_session_key)
                if session_mode == "continuing"
                else None
            )
            preflight_required = not any(
                result.session_mode == session_mode
                and result.model == model
                and result.error is None
                for result in results
            )
            LOGGER.info(
                "Model %d/%d started mode=%s model=%s",
                model_index,
                len(MODELS),
                session_mode,
                model,
            )
            for case_index, case in enumerate(CASES, start=1):
                result_key = (session_mode, model, case.case_id)
                if result_key in completed:
                    LOGGER.info(
                        "Resume skip mode=%s model=%s case=%s",
                        session_mode,
                        model,
                        case.case_id,
                    )
                    persisted = next(
                        result
                        for result in results
                        if (result.session_mode, result.model, result.case_id)
                        == result_key
                    )
                    log_case_score(persisted, case_index, len(CASES), None)
                    continue
                is_preflight = preflight_required
                scoring_started: float | None = None
                try:
                    run = runner.run(model, case, session_id, session_mode)
                    if run.session_id and session_mode == "continuing":
                        session_id = run.session_id
                        sessions[model_session_key] = session_id
                    scoring_started = time.perf_counter()
                    judge = JUDGE_MODEL if run_validation_error(run) is None else "skipped"
                    LOGGER.info(
                        "Case scoring started mode=%s model=%s case=%s judge=%s "
                        "expected=%d response_chars=%d",
                        session_mode,
                        model,
                        case.case_id,
                        judge,
                        len(case.findings),
                        len(run.response),
                    )
                    result = score_run(model, case, run, session_mode)
                except OpenCodeExecutionError as error:
                    if error.run.session_id and session_mode == "continuing":
                        session_id = error.run.session_id
                        sessions[model_session_key] = session_id
                    error_text = f"{type(error).__name__}: {error}"
                    LOGGER.error(
                        "Case failed mode=%s model=%s case=%s error=%s",
                        session_mode,
                        model,
                        case.case_id,
                        error_text,
                    )
                    result = failed_result(
                        model,
                        case,
                        session_id or "",
                        error_text,
                        error.run,
                        session_mode=session_mode,
                    )
                except (
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    subprocess.SubprocessError,
                ) as error:
                    error_text = f"{type(error).__name__}: {error}"
                    LOGGER.error(
                        "Case failed mode=%s model=%s case=%s error=%s",
                        session_mode,
                        model,
                        case.case_id,
                        error_text,
                    )
                    result = failed_result(
                        model,
                        case,
                        session_id or "",
                        error_text,
                        session_mode=session_mode,
                    )
                scoring_seconds = (
                    time.perf_counter() - scoring_started
                    if scoring_started is not None
                    else 0.0
                )
                results = [
                    existing
                    for existing in results
                    if (existing.session_mode, existing.model, existing.case_id)
                    != result_key
                ]
                results.append(result)
                if result.error is None or not RETRY_FAILURES:
                    completed.add(result_key)
                save_reports(results, sessions)
                log_case_score(result, case_index, len(CASES), scoring_seconds)
                LOGGER.info(
                    "Case tools mode=%s model=%s case=%s Serena=%s Headroom=%s "
                    "attempted=%s successful=%s errors=%d",
                    session_mode,
                    model,
                    case.case_id,
                    result.serena_used,
                    result.headroom_used,
                    ",".join(result.tools) or "none",
                    ",".join(result.successful_tools) or "none",
                    result.tool_errors,
                )
                log_model_score(session_mode, model, results)
                preflight_required = False
                if is_preflight and result.error is not None:
                    LOGGER.error(
                        "Model preflight failed; deferring remaining cases "
                        "mode=%s model=%s",
                        session_mode,
                        model,
                    )
                    break
            log_model_score(session_mode, model, results)
            LOGGER.info(
                "Model %d/%d finished mode=%s model=%s",
                model_index,
                len(MODELS),
                session_mode,
                model,
            )
        LOGGER.info("Session mode finished: %s", session_mode)
    LOGGER.info("Agent benchmark finished: %s", REPORT_PATH.resolve())
    return 1 if any(result.error for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
