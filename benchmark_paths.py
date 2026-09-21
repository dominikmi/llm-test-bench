"""Shared project layout for benchmark configuration and generated artifacts."""

from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parent
CONFIG_DIR: Final = PROJECT_ROOT / "config"
RESULTS_DIR: Final = PROJECT_ROOT / "results"
LOGS_DIR: Final = PROJECT_ROOT / "logs"
REPORTS_DIR: Final = PROJECT_ROOT / "reports"
ARCHIVES_DIR: Final = PROJECT_ROOT / "archives"
DOCS_DIR: Final = PROJECT_ROOT / "docs"

GALILEO_REVIEW_RESULTS_DIR: Final = RESULTS_DIR / "galileo-review"
GALILEO_MATH_RESULTS_DIR: Final = RESULTS_DIR / "galileo-math"
OPENCODE_AGENT_RESULTS_DIR: Final = RESULTS_DIR / "opencode-agent"
JUDGE_RESULTS_DIR: Final = RESULTS_DIR / "judge"
MODEL_SMOKE_RESULTS_DIR: Final = RESULTS_DIR / "model-smoke"
GALILEO_REVIEW_ARCHIVES_DIR: Final = ARCHIVES_DIR / "galileo-review"
GALILEO_MATH_ARCHIVES_DIR: Final = ARCHIVES_DIR / "galileo-math"
OMLX_REVIEW_RESULTS_DIR: Final = RESULTS_DIR / "omlx-review"
OMLX_REVIEW_ARCHIVES_DIR: Final = ARCHIVES_DIR / "omlx-review"
OMLX_TOOLS_RESULTS_DIR: Final = RESULTS_DIR / "omlx-tools"
OMLX_TOOLS_ARCHIVES_DIR: Final = ARCHIVES_DIR / "omlx-tools"
TEST_DEFINITIONS_DIR: Final = PROJECT_ROOT / "test_definitions"


def ensure_parent_directories(*paths: Path) -> None:
    """Create parent directories required by generated artifact paths."""
    for parent in {path.parent for path in paths}:
        parent.mkdir(parents=True, exist_ok=True)
