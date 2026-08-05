#!/usr/bin/env python3
"""Shared plumbing for the two read-only task-reminder measurement scripts.

Used by ``scripts/analyze_task_update_reminders.py`` and
``scripts/audit_task_update_reminders_longitudinal.py``, which measure autorun
task-reminder behavior from the AI Session Search (``aise``) index. No runtime
autorun code imports this module.

``REMINDER_MARKERS`` holds frozen literals: they match the reminder text
preserved in HISTORICAL transcripts that aise indexed. The live templates in
``plugins/autorun/src/autorun/config.py`` changed on 2026-08-04 (templated
bodies; the "NO CHECKLIST EXISTS:" wording no longer appears there at all), so
these literals must NOT be "fixed" to track the current templates — doing so
would silently empty the historical search results these scripts measure.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


REMINDER_MARKERS = (
    "TASK UPDATE REQUIRED:",
    "TASK UPDATE OVERDUE:",
    "NO CHECKLIST EXISTS:",
)

# TaskUpdate argument keys that record new work or dependency detail rather
# than a bare status flip. Both scripts' taskupdate classifiers share this set
# but deliberately keep their own status-only rules.
TASKUPDATE_DETAIL_KEYS = frozenset(
    {"description", "subject", "addblockedby", "blockedby", "dependencies"}
)


def run_json(command: list[str]) -> Any:
    """Run one checked aise command and decode its JSON output."""
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"aise command failed: {detail}") from error
    return json.loads(completed.stdout)


def sql_quote(value: str) -> str:
    """Quote one trusted index identifier as a SQLite string literal."""
    return "'" + value.replace("'", "''") + "'"


def db_query(aise: str, sql: str, timeout_ms: int | None = None) -> list[dict[str, Any]]:
    """Run one bounded read-only ``aise db query`` statement."""
    command = [aise, "db", "query", sql, "--limit", "0"]
    if timeout_ms is not None:
        command += ["--timeout-ms", str(timeout_ms)]
    return run_json(command + ["--format", "json"])


def search_marker(
    aise: str,
    marker: str,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Return every exact harness-notice match for one reminder marker."""
    command = [aise, "messages", "search", marker, "--kind", "harness-notice"]
    if since is not None:
        command += ["--since", since]
    if until is not None:
        command += ["--until", until]
    command += [
        "--all-results",
        "--context-before",
        "0",
        "--context-after",
        "0",
        "--lines-per-message",
        "1",
        "--include",
        "normalized_session_metadata",
        "--format",
        "json",
        "--index-refresh",
        "existing-only",
        "--skip-release-notification",
    ]
    return run_json(command)["results"]


def normalized_tool(row: dict[str, Any]) -> str:
    """Normalize harness-specific separators while retaining the final tool name."""
    return (row.get("tool_name") or "").replace("-", "_").split("__")[-1].lower()


def tool_args(row: dict[str, Any]) -> dict[str, Any]:
    """Extract normalized tool arguments without failing on provider-specific text."""
    try:
        payload = json.loads(row.get("content") or "{}")
    except json.JSONDecodeError:
        return {}
    args = payload.get("args")
    return args if isinstance(args, dict) else {}


def percentile(values: list[int], quantile: float) -> float | None:
    """Linearly interpolated percentile over a raw sample list.

    Not interchangeable with :func:`weighted_quantile`: this interpolates
    between neighbors and returns a float.
    """
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def weighted_quantile(histogram: Counter[int], quantile: float) -> int | None:
    """Ceiling-rank quantile over a weighted run-length histogram.

    Not interchangeable with :func:`percentile`: this returns an observed
    integer value without interpolation.
    """
    total = sum(histogram.values())
    if not total:
        return None
    target = max(1, int(total * quantile + 0.999999999))
    seen = 0
    for value in sorted(histogram):
        seen += histogram[value]
        if seen >= target:
            return value
    return max(histogram)


def write_json_output(path: Path, payload: dict[str, Any]) -> None:
    """Write one JSON receipt, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
