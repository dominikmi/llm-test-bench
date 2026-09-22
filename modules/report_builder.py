"""Build a Markdown analysis draft from a benchmark results JSON.

Covers both result shapes in this repository:

- review runs  — keys: score, recall, precision, matched_findings,
  missed_findings, unsupported_findings, parsed_findings, reasoning_response
- agent suites — keys: quality, call_efficiency, turn_efficiency, waste_ratio,
  invalid_calls, off_plan_calls, forbidden_hits, identical_retries, terminated

The generator emits every section that can be computed mechanically
(leaderboard, telemetry, per-case spread, findings accounting, cost) plus
auto-flagged observations (thinking-channel detection, budget saturation,
systematic misses, sampling-noise caveats). Sections that require judgement
are marked with TODO comments so the analysis pass fills them rather than
guessing.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN: float = 4.0


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Run-level metadata extracted from a results payload."""

    params: dict[str, Any]
    model_parameters: dict[str, Any]
    results: list[dict[str, Any]]
    shape: str  # "review" | "agent"


def load_run(path: Path) -> RunMeta:
    """Load a results JSON and detect its shape from the first record."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise TypeError(f"Expected a results object with a 'results' list: {path}")
    results = [r for r in data["results"] if isinstance(r, dict)]
    if not results:
        raise ValueError(f"No results in {path}")
    first = results[0]
    if "score" in first or "recall" in first:
        shape = "review"
    elif "quality" in first or "call_efficiency" in first:
        shape = "agent"
    else:
        raise ValueError(f"Unrecognized result shape in {path}: {sorted(first)}")
    return RunMeta(
        params=data.get("benchmark_parameters") or {},
        model_parameters=data.get("model_parameters") or {},
        results=results,
        shape=shape,
    )


def _models(results: list[dict[str, Any]]) -> list[str]:
    """Unique model names in first-seen order."""
    seen: list[str] = []
    for r in results:
        model = str(r.get("model", ""))
        if model not in seen:
            seen.append(model)
    return seen


def _cases(results: list[dict[str, Any]]) -> list[str]:
    """Unique case ids in sorted order."""
    return sorted({str(r.get("case_id", "")) for r in results})


def _score(result: dict[str, Any], shape: str) -> float:
    """Uniform score accessor across result shapes."""
    key = "score" if shape == "review" else "quality"
    value = result.get(key)
    return float(value) if isinstance(value, int | float) else math.nan


def _sampling(run: RunMeta, model: str) -> dict[str, Any]:
    """Effective injected sampling for a model, if the run recorded it."""
    entry = run.model_parameters.get(model)
    if not isinstance(entry, dict):
        return {}
    sampling = entry.get("sampling")
    return dict(sampling) if isinstance(sampling, dict) else {}


@dataclass(slots=True)
class ModelStats:
    """Aggregated per-model facts."""

    model: str
    cases: int
    errors: int
    mean_score: float
    category_means: dict[str, float]
    matched: int
    missed: int
    unsupported: int
    prompt_tokens: int
    completion_tokens: int
    elapsed_seconds: float
    mean_out_tps: float
    mean_pp_tps: float
    reasoning_chars: int
    reasoning_per_case: list[int] = field(default_factory=list)
    # agent-suite extras
    call_efficiency: float = math.nan
    turn_efficiency: float = math.nan
    waste_ratio: float = math.nan
    invalid_calls: int = 0
    off_plan_calls: int = 0
    forbidden_hits: int = 0
    identical_retries: int = 0
    truncations: int = 0
    json_answer_rate: float = math.nan


def _count_field(rows: list[dict[str, Any]], key: str) -> int:
    """Sum a field that may be a list (count items) or a number."""
    total = 0
    for row in rows:
        value = row.get(key)
        if isinstance(value, list):
            total += len(value)
        elif isinstance(value, int | float):
            total += int(value)
    return total


def _mean_field(rows: list[dict[str, Any]], key: str) -> float:
    """Mean of a numeric field, NaN when absent."""
    nums = [
        float(v)
        for r in rows
        if isinstance((v := r.get(key)), int | float)
    ]
    return statistics.fmean(nums) if nums else math.nan


def per_model_stats(run: RunMeta) -> list[ModelStats]:
    """Aggregate every numeric/finding field available per model."""
    stats: list[ModelStats] = []
    for model in _models(run.results):
        rows = [r for r in run.results if r.get("model") == model]
        scores = [_score(r, run.shape) for r in rows]
        cats: dict[str, list[float]] = {}
        for r in rows:
            category = str(r.get("category", ""))
            if category:
                cats.setdefault(category, []).append(_score(r, run.shape))
        reasoning = [len(str(r.get("reasoning_response") or "")) for r in rows]
        terminated = [r.get("terminated") for r in rows]
        stats.append(
            ModelStats(
                model=model,
                cases=len(rows),
                errors=sum(1 for r in rows if r.get("error")),
                mean_score=statistics.fmean(scores) if scores else math.nan,
                category_means={c: statistics.fmean(v) for c, v in cats.items()},
                matched=_count_field(rows, "matched_findings"),
                missed=_count_field(rows, "missed_findings"),
                unsupported=_count_field(rows, "unsupported_findings"),
                prompt_tokens=sum(int(r.get("prompt_tokens") or 0) for r in rows),
                completion_tokens=sum(
                    int(r.get("completion_tokens") or 0) for r in rows
                ),
                elapsed_seconds=sum(float(r.get("elapsed_seconds") or 0) for r in rows),
                mean_out_tps=_mean_field(rows, "output_tokens_per_second"),
                mean_pp_tps=_mean_field(rows, "prompt_tokens_per_second"),
                reasoning_chars=sum(reasoning),
                reasoning_per_case=reasoning,
                call_efficiency=_mean_field(rows, "call_efficiency"),
                turn_efficiency=_mean_field(rows, "turn_efficiency"),
                waste_ratio=_mean_field(rows, "waste_ratio"),
                invalid_calls=_count_field(rows, "invalid_calls"),
                off_plan_calls=_count_field(rows, "off_plan_calls"),
                forbidden_hits=_count_field(rows, "forbidden_hits"),
                identical_retries=_count_field(rows, "identical_retries"),
                truncations=sum(1 for t in terminated if t == "cap"),
                json_answer_rate=_mean_field(rows, "json_answer"),
            )
        )
    stats.sort(key=lambda s: (math.isnan(s.mean_score), -s.mean_score))
    return stats


@dataclass(frozen=True, slots=True)
class CaseSpread:
    """Per-case score dispersion facts."""

    case_id: str
    minimum: float
    maximum: float
    spread: float
    mean: float
    all_missed: tuple[str, ...]


def per_case_spread(run: RunMeta) -> list[CaseSpread]:
    """Score dispersion and findings missed by every model, per case."""
    spreads: list[CaseSpread] = []
    for case_id in _cases(run.results):
        rows = [r for r in run.results if r.get("case_id") == case_id]
        scores = [_score(r, run.shape) for r in rows if not math.isnan(_score(r, run.shape))]
        missed_sets = [
            set(r["missed_findings"])
            for r in rows
            if isinstance(r.get("missed_findings"), list)
        ]
        common_missed = (
            tuple(sorted(set.intersection(*missed_sets))) if missed_sets else ()
        )
        spreads.append(
            CaseSpread(
                case_id=case_id,
                minimum=min(scores) if scores else math.nan,
                maximum=max(scores) if scores else math.nan,
                spread=(max(scores) - min(scores)) if scores else math.nan,
                mean=statistics.fmean(scores) if scores else math.nan,
                all_missed=common_missed,
            )
        )
    return spreads


@dataclass(frozen=True, slots=True)
class ThinkingTelemetry:
    """Thinking-channel facts and inferred capability per model."""

    model: str
    total_chars: int
    per_case_min: int
    per_case_max: int
    per_case_mean: float
    emitted_reasoning: bool
    thinking_keys_present: bool
    budget_tokens: int | None
    likely_budget_pinned: bool
    enable_thinking_flag: bool | None


def thinking_telemetry(run: RunMeta) -> list[ThinkingTelemetry]:
    """Detect thinking capability, budget pinning, and flag inertness.

    Inference rules:
    - emitted_reasoning: reasoning channel produced any text at all.
    - likely_budget_pinned: reasoning emitted AND even the shortest per-case
      reasoning approaches the configured thinking budget (every case burned
      ~the full cap — the budget, not the task, set the reasoning length).
    - thinking_keys_present: the injected sampling carried explicit thinking
      controls; if absent and reasoning was emitted, the model ran on the
      runner default budget (flagged as a parametrization finding).
    """
    telemetry: list[ThinkingTelemetry] = []
    for model in _models(run.results):
        rows = [r for r in run.results if r.get("model") == model]
        lens = [len(str(r.get("reasoning_response") or "")) for r in rows]
        sampling = _sampling(run, model)
        budget_keys = ("thinking_budget_tokens", "thinking_budget")
        budget = next(
            (
                int(sampling[k])
                for k in budget_keys
                if isinstance(sampling.get(k), int | float)
            ),
            None,
        )
        emitted = sum(lens) > 0
        pinned = (
            emitted
            and budget is not None
            and budget > 0
            and min(lens) / CHARS_PER_TOKEN >= budget * 0.7
        )
        flag = sampling.get("enable_thinking")
        telemetry.append(
            ThinkingTelemetry(
                model=model,
                total_chars=sum(lens),
                per_case_min=min(lens) if lens else 0,
                per_case_max=max(lens) if lens else 0,
                per_case_mean=statistics.fmean(lens) if lens else 0.0,
                emitted_reasoning=emitted,
                thinking_keys_present=any(k in sampling for k in budget_keys)
                or "enable_thinking" in sampling,
                budget_tokens=budget,
                likely_budget_pinned=pinned,
                enable_thinking_flag=bool(flag) if isinstance(flag, bool) else None,
            )
        )
    return telemetry


# reports/ is grouped by serving stack, not by benchmark name.
REPORT_DIRS: dict[str, str] = {
    "omlx": "omlx",
    "galileo": "llama-cpp-linux",
}


def report_dir(run: RunMeta, source_path: Path) -> Path:
    """reports/<stack>/ for a run — omlx runs vs llama.cpp-on-Linux runs."""
    backend = str(
        run.params.get("backend") or source_path.parent.name.split("-")[0]
    )
    return Path("reports") / REPORT_DIRS.get(backend, "")


_SUITE_SOURCES: dict[str, str] = {
    "tool-use": "test_definitions/tool_use.json",
    "tools": "test_definitions/tool_use.json",
    "logic": "test_definitions/logic.json",
    "combined": "test_definitions/combined.json",
}

_SUITE_BENCH_NAMES: dict[str, str] = {
    # bench.py dispatch names that differ from the recorded suite label
    "tool-use": "tools",
}


def _provenance(run: RunMeta, source_path: Path) -> dict[str, str]:
    """Resolve the inputs that produced this run.

    Returns references to the test-definition source, preset file, model
    list, and the recorded prompt/schema versions. Files that no longer
    exist at the recorded path are marked — a moved preset file means the
    recorded `model_parameters` section is the only surviving description
    of the injected regime.
    """
    params = run.params
    backend = str(params.get("backend") or source_path.parent.name.split("-")[0])
    suite = str(params.get("suite") or "")
    language = str(params.get("language") or "")

    if "math_cases" in params:
        cases_source = "modules/math_bench_galileo_reviews.py (MATH_CASES, embedded)"
    elif run.shape == "review" and language:
        cases_source = f"test_definitions/{language}.json"
    elif suite in _SUITE_SOURCES:
        cases_source = _SUITE_SOURCES[suite]
    elif "pipeline" in source_path.parent.name:
        cases_source = "opencode-agent-suite/cases/"
    else:
        cases_source = f"(unresolved — suite {suite!r})"

    models_file = (
        "config/models-omlx.json" if backend == "omlx" else "config/models.json"
    )

    def _exists(path_str: str) -> str:
        known = path_str.startswith(("test_definitions", "config"))
        if not known:
            return path_str
        if not Path(path_str).exists():
            return f"`{path_str}` **(missing — file moved or deleted)**"
        return f"`{path_str}`"

    models_ref = _exists(models_file)
    if backend in {"omlx", "galileo"}:
        models_ref += f" (or `{backend.upper()}_MODELS` env override)"

    return {
        "backend": backend,
        "suite": suite,
        "language": language,
        "cases_source": _exists(cases_source),
        "models_file": models_ref,
        "presets": str(params.get("presets_path") or "(none — runner defaults)"),
        "prompt_version": str(params.get("prompt_version", "—")),
        "response_format": str(params.get("response_format", "—")),
    }


_ENV_DEFAULTS: dict[str, tuple[str, float | str]] = {
    # param key -> (env suffix, default) — emit env var only when it differs.
    "request_timeout_seconds": ("TIMEOUT_SECONDS", 300),
    "prediction_timeout_ms": ("PREDICT_TIMEOUT_MS", 300000),
    "max_tokens": ("MAX_TOKENS", 700),
    "thinking_budget_tokens": ("THINKING_BUDGET", 256),
    "max_retries": ("MAX_RETRIES", 1),
}


def _reproduce_command(run: RunMeta, source_path: Path) -> list[str]:
    """Approximate reproduction command from recorded parameters.

    The results file does not record the argv or the endpoint URL (endpoint
    secrecy is deliberate); the command is reconstructed from the results
    directory name and any parameters that differ from runner defaults.
    """
    params = run.params
    prov = _provenance(run, source_path)
    backend = prov["backend"]
    suite = prov["suite"]
    bench = source_path.parent.name
    if suite:
        bench = f"{backend}-{_SUITE_BENCH_NAMES.get(suite, suite)}"
    prefix = "GALILEO" if backend == "galileo" else "OMLX"

    env: list[str] = []
    if backend == "galileo":
        env.append("GALILEO_BASE_URL=<endpoint>")
    for key, (suffix, default) in _ENV_DEFAULTS.items():
        value = params.get(key)
        if value is not None and value != default:
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            env.append(f"{prefix}_{suffix}={value}")
    if params.get("retry_failures"):
        env.append(f"{prefix}_RETRY_FAILURES=1")
    if params.get("start_model"):
        env.append(f"{prefix}_START_MODEL={params['start_model']}")
    artifact = (
        "MATH" if "math_cases" in params else ("BENCH" if suite else "REVIEW")
    )
    env.append(f"{prefix}_{artifact}_RESULTS=<fresh path>")
    env.append(f"{prefix}_{artifact}_LOG=<fresh path>")

    args = ["bin/bench.py", bench]
    if prov["language"]:
        args += ["--lang", prov["language"]]
    if params.get("presets_path"):
        args += ["--presets", str(params["presets_path"])]
    if params.get("judge_enabled"):
        args += ["--judge", "yes"]
    if params.get("judge_model"):
        env.append(f"{prefix}_JUDGE_MODEL={params['judge_model']}")

    rendered = []
    if env:
        rendered.append(" \\\n".join(env) + " \\")
    rendered.append(" ".join(args))
    return rendered


def _fmt(value: float, digits: int = 2) -> str:
    """Format a possibly-NaN metric."""
    return "—" if math.isnan(value) else f"{value:.{digits}f}"


def _short(model: str) -> str:
    """Compact model name for table cells."""
    return model.replace("coder-", "").replace(":LATEST", "").replace(":latest", "")


def render_markdown(
    run: RunMeta,
    source_path: Path,
    title: str,
    baseline: RunMeta | None = None,
    baseline_path: Path | None = None,
) -> str:
    """Render the full analysis draft as Markdown."""
    lines: list[str] = []
    params = run.params
    stats = per_model_stats(run)
    spreads = per_case_spread(run)
    telemetry = thinking_telemetry(run)
    mtime = datetime.fromtimestamp(
        source_path.stat().st_mtime, tz=UTC
    ).strftime("%Y-%m-%d")

    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Date:** {mtime}")
    lines.append(f"**Results:** `{source_path}` (local only, gitignored)")
    if params.get("language"):
        lines.append(f"**Language:** {params['language']}")
    if params.get("suite"):
        lines.append(f"**Suite:** {params['suite']}")
    if params.get("presets_path"):
        lines.append(f"**Presets:** `{params['presets_path']}`")
    if params.get("judge_enabled") or params.get("judge_model"):
        lines.append(
            f"**Judge:** {params.get('judge_model', 'enabled')} "
            f"(enabled={params.get('judge_enabled')})"
        )
    total_errors = sum(s.errors for s in stats)
    lines.append(
        f"**Outcome:** {sum(s.cases for s in stats)} cases scored, "
        f"{total_errors} recorded errors."
    )
    lines.append("")

    # -- provenance ------------------------------------------------------------
    prov = _provenance(run, source_path)
    lines.append("## Provenance")
    lines.append("")
    lines.append("| Input | Reference |")
    lines.append("|---|---|")
    lines.append(f"| Backend | {prov['backend']} |")
    lines.append(f"| Test cases | {prov['cases_source']} |")
    lines.append(f"| Model list | {prov['models_file']} |")
    lines.append(f"| Presets | `{prov['presets']}` |")
    lines.append(f"| Prompt version | {prov['prompt_version']} |")
    lines.append(f"| Response format | {prov['response_format']} |")
    judge = params.get("judge_model") or params.get("judge")
    if judge:
        lines.append(f"| Judge | `{judge}` |")
    lines.append("")
    lines.append("Approximate reproduction (endpoint URL is not recorded):")
    lines.append("")
    lines.append("```bash")
    lines.extend(_reproduce_command(run, source_path))
    lines.append("```")
    lines.append("")

    # -- injected regime ----------------------------------------------------
    lines.append("## Effective injected regime")
    lines.append("")
    lines.append(
        "Values below come from `model_parameters.<model>.sampling` — what was "
        "actually sent per request. `benchmark_parameters` records runner "
        "*defaults* and underreports preset overrides."
    )
    lines.append("")
    lines.append("| Model | temp | top_p | top_k | min_p | rep_pen | max_tokens | thinking |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for s in stats:
        smp = _sampling(run, s.model)
        thinking = "—"
        if "enable_thinking" in smp or "thinking_budget_tokens" in smp or "thinking_budget" in smp:
            flag = smp.get("enable_thinking")
            budget = smp.get("thinking_budget_tokens", smp.get("thinking_budget"))
            effort = smp.get("reasoning_effort")
            thinking = f"{'on' if flag else 'off'}"
            if budget is not None:
                thinking += f" @{budget}"
            if effort:
                thinking += f" effort={effort}"
        if not smp:
            thinking = "(not recorded)"
        lines.append(
            f"| {_short(s.model)} | {smp.get('temperature', '—')} | "
            f"{smp.get('top_p', '—')} | {smp.get('top_k', '—')} | "
            f"{smp.get('min_p', '—')} | {smp.get('repeat_penalty', '—')} | "
            f"{smp.get('max_tokens', '—')} | {thinking} |"
        )
    lines.append("")
    default_temp = params.get("temperature")
    injected = [_sampling(run, s.model).get("temperature") for s in stats]
    if default_temp == 0 and any(t not in (None, 0) for t in injected):
        lines.append(
            "> **Parametrization note:** `benchmark_parameters.temperature` is 0 "
            "but every model ran at an injected temperature — the recorded "
            "params describe the runner default, not the measured regime."
        )
        lines.append("")
    def _starved(model: str) -> bool:
        """Thinking budget ≥ max_tokens with thinking not disabled."""
        smp = _sampling(run, model)
        budget = smp.get("thinking_budget_tokens", smp.get("thinking_budget"))
        cap = smp.get("max_tokens")
        return (
            isinstance(budget, int | float)
            and isinstance(cap, int | float)
            and float(budget) >= float(cap)
            and budget > 0
            and smp.get("enable_thinking") is not False
        )

    starved = [s.model for s in stats if _starved(s.model)]
    if starved:
        lines.append(
            "> **Parametrization finding:** "
            + ", ".join(f"`{_short(m)}`" for m in starved)
            + " ran with thinking budget ≥ `max_tokens`. On backends where "
            "reasoning counts against the output cap (llama.cpp/Galileo) this "
            "starves the final answer — check for empty responses."
        )
        lines.append("")

    # -- leaderboard ---------------------------------------------------------
    lines.append("## Results")
    lines.append("")
    if run.shape == "review":
        cats = sorted({c for s in stats for c in s.category_means})
        header = "| Rank | Model | Overall | " + " | ".join(c.title() for c in cats)
        header += " | Matched | Missed | Unsupported | Out tok | Time |"
        lines.append(header)
        lines.append("|---:|---|---:|" + "---:|" * len(cats) + "---:|---:|---:|---:|---:|")
        for rank, s in enumerate(stats, 1):
            cat_cells = "".join(
                f"{_fmt(s.category_means.get(c, math.nan))} | " for c in cats
            )
            lines.append(
                f"| {rank} | {_short(s.model)} | {_fmt(s.mean_score)} | "
                f"{cat_cells}{s.matched} | {s.missed} | {s.unsupported} | "
                f"{s.completion_tokens} | {s.elapsed_seconds:.0f} s |"
            )
    else:
        lines.append(
            "| Rank | Model | Quality | Call eff | Turn eff | Waste | "
            "Invalid | Off-plan | Forbidden | Identical retry | Cap hits | "
            "JSON ans | Out tok | Time |"
        )
        lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for rank, s in enumerate(stats, 1):
            lines.append(
                f"| {rank} | {_short(s.model)} | {_fmt(s.mean_score)} | "
                f"{_fmt(s.call_efficiency, 3)} | {_fmt(s.turn_efficiency, 3)} | "
                f"{_fmt(s.waste_ratio, 3)} | {s.invalid_calls} | "
                f"{s.off_plan_calls} | {s.forbidden_hits} | "
                f"{s.identical_retries} | {s.truncations} | "
                f"{_fmt(s.json_answer_rate)} | {s.completion_tokens} | "
                f"{s.elapsed_seconds:.0f} s |"
            )
    lines.append("")
    tps_recorded = any(
        s.mean_pp_tps > 0 or s.mean_out_tps > 0
        for s in stats
        if not (math.isnan(s.mean_pp_tps) and math.isnan(s.mean_out_tps))
    )
    if tps_recorded:
        lines.append(
            "Throughput: PP "
            + "/".join(_fmt(s.mean_pp_tps, 1) for s in stats)
            + " tok/s, OUT "
            + "/".join(_fmt(s.mean_out_tps, 1) for s in stats)
            + " tok/s (leaderboard order)."
        )
        lines.append("")

    # -- thinking telemetry --------------------------------------------------
    reasoning_recorded = any("reasoning_response" in r for r in run.results)
    if reasoning_recorded and any(
        t.emitted_reasoning or t.thinking_keys_present for t in telemetry
    ):
        lines.append("## Thinking-channel telemetry")
        lines.append("")
        lines.append(
            "| Model | Reasoning chars | Per-case range | Budget | Inferred |"
        )
        lines.append("|---|---:|---|---|---|")
        for t in telemetry:
            if not t.emitted_reasoning and t.enable_thinking_flag is False:
                inferred = "template ignores enable_thinking (inert)"
            elif not t.emitted_reasoning and not t.thinking_keys_present:
                inferred = "no reasoning, no thinking keys sent"
            elif not t.emitted_reasoning:
                inferred = "no reasoning emitted"
            elif t.likely_budget_pinned:
                inferred = f"thinking, pinned at {t.budget_tokens}-token cap"
            elif not t.thinking_keys_present:
                inferred = "**thinking on runner-default budget — no explicit keys**"
            else:
                inferred = "thinking"
            rng = (
                f"{t.per_case_min}–{t.per_case_max}"
                if t.emitted_reasoning
                else "—"
            )
            lines.append(
                f"| {_short(t.model)} | {t.total_chars} | {rng} | "
                f"{t.budget_tokens if t.budget_tokens is not None else '—'} | "
                f"{inferred} |"
            )
        lines.append("")
        defaulted = [
            t.model
            for t in telemetry
            if t.emitted_reasoning and not t.thinking_keys_present
        ]
        if defaulted:
            lines.append(
                "> **Parametrization finding:** "
                + ", ".join(f"`{_short(m)}`" for m in defaulted)
                + " emitted reasoning with no explicit thinking keys — they ran "
                "on the runner-default budget, not the intended profile. Their "
                "scores are shallow-thinking numbers; a rerun isolates the "
                "budget effect."
            )
            lines.append("")

    # -- per-case discrimination ---------------------------------------------
    discriminators = [c for c in spreads if c.spread >= 40]
    saturated = [c for c in spreads if c.spread <= 20]
    systematic = [c for c in spreads if c.all_missed]
    lines.append("## Per-case discrimination")
    lines.append("")
    if discriminators:
        lines.append(
            "**Best discriminators** (spread ≥40): "
            + ", ".join(
                f"`{c.case_id}` ({c.minimum:.0f}→{c.maximum:.0f})"
                for c in discriminators
            )
            + "."
        )
        lines.append("")
    if saturated:
        lines.append(
            "**Near-saturated** (spread ≤20): "
            + ", ".join(f"`{c.case_id}`" for c in saturated)
            + "."
        )
        lines.append("")
    if systematic:
        lines.append(
            "**Systematic misses** — expected findings *every* model failed to "
            "match (grading-contract suspects, compare with the causal-07b "
            "artifact):"
        )
        lines.append("")
        for c in systematic:
            lines.append(f"- `{c.case_id}`: {', '.join(c.all_missed)}")
        lines.append("")

    # -- cost -----------------------------------------------------------------
    lines.append("## Cost")
    lines.append("")
    fastest = min(stats, key=lambda s: s.elapsed_seconds)
    slowest = max(stats, key=lambda s: s.elapsed_seconds)
    cheapest = min(stats, key=lambda s: s.completion_tokens)
    lines.append(
        f"- Fastest wall-clock: `{_short(fastest.model)}` "
        f"({fastest.elapsed_seconds:.0f} s); slowest: `{_short(slowest.model)}` "
        f"({slowest.elapsed_seconds:.0f} s) — "
        f"{slowest.elapsed_seconds / max(fastest.elapsed_seconds, 1):.1f}×."
    )
    lines.append(
        f"- Cheapest output: `{_short(cheapest.model)}` "
        f"({cheapest.completion_tokens} tokens)."
    )
    best = stats[0]
    for s in stats[1:]:
        if (
            not math.isnan(s.mean_score)
            and s.completion_tokens > 0
            and best.completion_tokens > 0
        ):
            lines.append(
                f"- `{_short(s.model)}` scored {s.mean_score - best.mean_score:+.2f} "
                f"vs `{_short(best.model)}` at "
                f"{s.completion_tokens / best.completion_tokens:.2f}× the output tokens."
            )
    lines.append("")

    # -- baseline diff ---------------------------------------------------------
    if baseline is not None and baseline_path is not None:
        lines.append("## vs. baseline")
        lines.append("")
        lines.append(f"Baseline: `{baseline_path}`")
        lines.append("")
        bstats = {s.model: s for s in per_model_stats(baseline)}
        lines.append("| Model | This run | Baseline | Δ |")
        lines.append("|---|---:|---:|---:|")
        for s in stats:
            b = bstats.get(s.model)
            if b is None or math.isnan(b.mean_score):
                continue
            lines.append(
                f"| {_short(s.model)} | {_fmt(s.mean_score)} | "
                f"{_fmt(b.mean_score)} | {s.mean_score - b.mean_score:+.2f} |"
            )
        missing = [m for m in bstats if m not in {s.model for s in stats}]
        lines.append("")
        if missing:
            lines.append(
                f"> Baseline-only models: {', '.join(missing)} — "
                "incomplete overlap; deltas are indicative, not clean."
            )
            lines.append("")

    # -- caveats ---------------------------------------------------------------
    lines.append("## Caveats")
    lines.append("")
    injected_temps = [
        _sampling(run, s.model).get("temperature") for s in stats
    ]
    if any(isinstance(t, int | float) and t > 0 for t in injected_temps):
        lines.append(
            "- Scores are single samples at temperature >0 — expect ±4–5 "
            "points of noise on a ~20-case suite; small deltas are not "
            "meaningful without reruns."
        )
    if params.get("presets_path"):
        lines.append(
            "- The preset file may have changed since this run — the results "
            "JSON's `model_parameters` section is the source of truth for what "
            "was actually injected."
        )
    if run.shape == "review" and params.get("judge_enabled"):
        lines.append(
            "- Judge verdicts depend on the judge model's own sampling; "
            "a different judge or judge timeout fallbacks degrade recall "
            "silently (verdict 'no' on failure)."
        )
    lines.append(
        "- TODO: per-model mechanism analysis (which cases moved and why), "
        "cross-suite comparison, and judgement calls go here — this file "
        "contains only computed facts."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: build a Markdown report draft from a results JSON."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Build a Markdown analysis draft from a benchmark results JSON."
    )
    parser.add_argument("results", type=Path, help="Path to a *-results.json file")
    parser.add_argument(
        "--baseline", type=Path, default=None, help="Optional baseline results JSON"
    )
    parser.add_argument("--title", default=None, help="Report title override")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help=(
            "Output path — a bare filename resolves under "
            "reports/<stack>/ (omlx/ or llama-cpp-linux/); "
            "default: stdout"
        ),
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    run = load_run(args.results)
    baseline = load_run(args.baseline) if args.baseline else None
    title = args.title or f"Benchmark analysis — {args.results.stem}"
    markdown = render_markdown(
        run, args.results, title, baseline=baseline, baseline_path=args.baseline
    )
    if args.out:
        out = args.out
        if out.parent == Path("."):
            out = report_dir(run, args.results) / out
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        print(f"Report written: {out}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
