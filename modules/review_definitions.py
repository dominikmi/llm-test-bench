"""Static JSON review-case definitions shared by the benchmark runners.

Each language's cases live in ``test_definitions/<lang>.json`` and follow the
``DefinitionFile`` schema validated with pydantic. The file-level ``language``
field applies to every case unless a case overrides it explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .benchmark_paths import TEST_DEFINITIONS_DIR


@dataclass(frozen=True, slots=True)
class ExpectedFinding:
    """One expected review concept and accepted textual indicators."""

    name: str
    indicators: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewCase:
    """A fixed code-review case with deterministic expected findings."""

    case_id: str
    category: str
    language: str
    code: str
    findings: tuple[ExpectedFinding, ...]


class FindingDefinition(BaseModel):
    """One expected review concept inside a case definition file."""

    model_config = ConfigDict(extra="forbid")

    name: str
    indicators: list[str] = Field(default_factory=list)


class CaseDefinition(BaseModel):
    """One review case inside a language definition file."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: Literal["quality", "security"]
    language: str | None = None
    code: str
    findings: list[FindingDefinition] = Field(min_length=1)


class DefinitionFile(BaseModel):
    """Top-level structure of a test_definitions/<lang>.json file."""

    model_config = ConfigDict(extra="forbid")

    language: str
    cases: list[CaseDefinition] = Field(min_length=1)


LANGUAGE_EXTENSIONS: Final[dict[str, str]] = {
    "python": "py",
    "javascript": "js",
    "rust": "rs",
    "cpp": "cpp",
    "ruby": "rb",
}

LANGUAGE_DISPLAY_NAMES: Final[dict[str, str]] = {
    "python": "Python",
    "javascript": "JavaScript",
    "rust": "Rust",
    "cpp": "C++",
    "ruby": "Ruby",
}


def available_languages(
    directory: Path = TEST_DEFINITIONS_DIR,
) -> tuple[str, ...]:
    """List language names for which a static definition file exists."""
    return tuple(
        sorted(
            path.stem
            for path in directory.glob("*.json")
            if path.stem in LANGUAGE_EXTENSIONS
        )
    )


def language_suffix(language: str) -> str:
    """Return the artifact suffix; python keeps legacy unsuffixed names."""
    return "" if language == "python" else f"-{language}"


def _env_or(env: str | None, default: Path) -> Path:
    """Return the env-configured path override or the default."""
    override = os.environ.get(env) if env else None
    return Path(override) if override else default


def review_artifact_paths(
    results_dir: Path,
    logs_dir: Path,
    prefix: str,
    language: str,
    *,
    results_env: str | None = None,
    log_env: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    """Return results, CSV, Markdown, and log paths for one language.

    Case IDs repeat across languages, so each language writes to its own
    artifacts to keep resume keys unambiguous.
    """
    suffix = language_suffix(language)
    results = _env_or(
        results_env, results_dir / f"{prefix}-results{suffix}.json"
    )
    log = _env_or(log_env, logs_dir / f"{prefix}{suffix}.log")
    return results, results.with_suffix(".csv"), results.with_suffix(".md"), log


def load_case_definitions(path: Path) -> tuple[ReviewCase, ...]:
    """Load review cases from a static JSON definition file."""
    if not path.exists():
        raise FileNotFoundError(f"Case definitions not found: {path}")
    try:
        definitions = DefinitionFile.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValidationError as error:
        raise ValueError(f"Invalid case definitions in {path}: {error}") from error
    return tuple(
        ReviewCase(
            case_id=item.case_id,
            category=item.category,
            language=item.language or definitions.language,
            code=item.code,
            findings=tuple(
                ExpectedFinding(finding.name, tuple(finding.indicators))
                for finding in item.findings
            ),
        )
        for item in definitions.cases
    )
