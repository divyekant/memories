"""JSON reporter and human-readable summary formatter for eval reports."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.models import EvalReport


def save_report(report: EvalReport, results_dir: str) -> str:
    """Save report as JSON file. Creates results_dir if needed.

    Filename: efficacy-YYYY-MM-DDTHHMMSS.json (timestamp sanitized).
    Returns the file path.
    """
    os.makedirs(results_dir, exist_ok=True)

    # Sanitize timestamp for filename safety (replace colons and plus signs)
    safe_ts = report.timestamp.replace(":", "").replace("+", "p")
    filename = f"efficacy-{safe_ts}.json"
    path = os.path.join(results_dir, filename)

    with open(path, "w") as f:
        json.dump(report.model_dump(), f, indent=2, default=str)

    return path


def format_summary(report: EvalReport) -> str:
    """Format human-readable summary of the eval report."""
    lines: list[str] = []

    lines.append("=" * 50)
    lines.append("  MEMORIES EFFICACY REPORT")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Timestamp: {report.timestamp}")
    lines.append("")

    # Overall scores
    lines.append("--- Overall ---")
    lines.append(f"  With memory:    {report.overall_with_memory:.2f}")
    lines.append(f"  Without memory: {report.overall_without_memory:.2f}")
    lines.append(f"  Delta:          {report.overall_efficacy_delta:.2f}")
    lines.append("")

    # Per-category breakdown
    if report.categories:
        lines.append("--- Categories ---")
        for name, cat in sorted(report.categories.items()):
            lines.append(
                f"  {name:<20s}  with={cat.with_memory:.2f}  "
                f"without={cat.without_memory:.2f}  delta={cat.delta:.2f}"
            )
        lines.append("")

    # Test count
    lines.append(f"Tests run: {len(report.tests)}")
    lines.append("=" * 50)

    return "\n".join(lines)
