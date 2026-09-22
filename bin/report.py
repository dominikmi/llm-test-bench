#!/usr/bin/env python3
"""Build a Markdown analysis draft from a benchmark results JSON.

Usage:
    bin/report.py results/galileo-review/galileo-review-coder-results.json \
        [--baseline other-results.json] [--title "..."] [-o reports/NAME.md]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.report_builder import main

if __name__ == "__main__":
    raise SystemExit(main())
