#!/usr/bin/env python3
"""Measure autorun task-reminder behavior from the AI Session Search index.

The script is read-only. It asks ``aise`` for exact reminder notices, then uses
bounded SQL queries through ``aise db query`` to reconstruct neighboring tool
calls for only the matched sessions. Raw transcripts are never scanned or
copied into the output.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import sys as _sys

# The shared helpers live beside this script; make the sibling importable
# no matter how this file is loaded (CLI from any cwd, or a test loading
# it via importlib from the repo's tests directory).
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_reminder_analysis_common import (
    REMINDER_MARKERS,
    TASKUPDATE_DETAIL_KEYS,
    db_query,
    normalized_tool,
    percentile,
    search_marker,
    sql_quote,
    tool_args,
    write_json_output,
)


DEFAULT_CANDIDATE_THRESHOLDS = (15, 20, 25, 30, 35, 40, 50)
DEFAULT_SAMPLE_SIZE = 12
PARALLEL_NOTICE_WINDOW_SECONDS = 5.0
TASK_TOOL_NAMES = {
    "taskcreate",
    "taskupdate",
    "tasklist",
    "todowrite",
    "update_plan",
}


def query_session_rows(aise: str, session_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Fetch tool calls and matched notices for the already identified sessions."""
    quoted_ids = ",".join(sql_quote(session_id) for session_id in session_ids)
    if not quoted_ids:
        return []
    marker_predicate = " OR ".join(
        f"m.content LIKE {sql_quote('%' + marker + '%')}" for marker in REMINDER_MARKERS
    )
    sql = f"""
        SELECT m.session_id, m.provider, m.seq, m.ts, m.tool_name, m.kind,
               m.content, s.cwd, s.repo_root
          FROM messages AS m
          JOIN sessions AS s ON s.id = m.session_id
         WHERE m.session_id IN ({quoted_ids})
           AND (
               m.kind = 'tool_call'
               OR (m.kind = 'harness_notice' AND ({marker_predicate}))
               OR (m.kind = 'conversation' AND m.content LIKE 'CRITICAL: Respond with TEXT ONLY%')
           )
         ORDER BY m.session_id, m.seq
    """
    return db_query(aise, sql)


def is_task_tool(row: dict[str, Any]) -> bool:
    """Return whether a normalized tool call represents task/checklist management."""
    return normalized_tool(row) in TASK_TOOL_NAMES


def classify_task_action(row: dict[str, Any] | None) -> str:
    """Classify the first task action after a reminder by information content."""
    if row is None:
        return "none_observed"
    name = normalized_tool(row)
    args = tool_args(row)
    if name == "taskcreate":
        return "new_work_recorded"
    if name in {"update_plan", "todowrite"}:
        return "checklist_snapshot"
    if name == "tasklist":
        return "review_only"
    if name == "taskupdate":
        lowered_keys = {key.lower() for key in args}
        if TASKUPDATE_DETAIL_KEYS & lowered_keys:
            return "work_or_dependency_detail"
        if lowered_keys <= {"taskid", "status"} and "status" in lowered_keys:
            return "status_only"
        return "other_task_mutation"
    return "unrecognized_task_tool"


def root_session_id(session_id: str) -> str:
    """Collapse a provider subagent transcript to its recorded parent-session family."""
    return session_id.split("/agent-", maxsplit=1)[0]


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an indexed RFC 3339 timestamp when present."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def summarize_event(
    reminder: dict[str, Any],
    session_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize tool-call behavior immediately surrounding one reminder."""
    seq = reminder["message_ref"]["message_seq"]
    tool_rows = [row for row in session_rows if row["kind"] == "tool_call"]
    before = [row for row in tool_rows if row["seq"] < seq]
    after = [row for row in tool_rows if row["seq"] > seq]
    prior_task_index = next(
        (
            index
            for index in range(len(before) - 1, -1, -1)
            if is_task_tool(before[index])
        ),
        -1,
    )
    calls_since_task = sum(
        not is_task_tool(row) for row in before[prior_task_index + 1 :]
    )
    next_task_offset = next(
        (index for index, row in enumerate(after) if is_task_tool(row)), None
    )
    next_task = after[next_task_offset] if next_task_offset is not None else None
    non_task_calls_before_response = (
        sum(not is_task_tool(row) for row in after[:next_task_offset])
        if next_task_offset is not None
        else len(after)
    )
    intervening_rows = [
        row
        for row in session_rows
        if row["seq"] > seq and (next_task is None or row["seq"] < next_task["seq"])
    ]
    compaction_boundary = any(
        row["kind"] == "conversation"
        and (row.get("content") or "").startswith("CRITICAL: Respond with TEXT ONLY")
        for row in intervening_rows
    )
    response_class = (
        "compaction_boundary"
        if next_task is None and compaction_boundary
        else classify_task_action(next_task)
    )
    reminder_row = next(
        (
            row
            for row in session_rows
            if row["seq"] == seq and row["kind"] == "harness_notice"
        ),
        None,
    )
    reminder_time = parse_timestamp(reminder_row.get("ts") if reminder_row else None)
    parallel_overdue = False
    if reminder_time:
        for row in intervening_rows:
            if (
                row["kind"] != "harness_notice"
                or "TASK UPDATE OVERDUE:" not in row["content"]
            ):
                continue
            overdue_time = parse_timestamp(row.get("ts"))
            if (
                overdue_time
                and 0
                <= (overdue_time - reminder_time).total_seconds()
                <= PARALLEL_NOTICE_WINDOW_SECONDS
            ):
                parallel_overdue = True
                break
    metadata = session_rows[0] if session_rows else {}
    return {
        "session_id": reminder["message_ref"]["session_id"],
        "seq": seq,
        "provider": reminder["message_metadata"]["provider"],
        "cwd": metadata.get("cwd"),
        "repo_root": metadata.get("repo_root"),
        "calls_since_prior_task_action": calls_since_task,
        "non_task_calls_before_response": non_task_calls_before_response,
        "immediate_task_response": next_task_offset == 0,
        "response_tool": next_task.get("tool_name") if next_task else None,
        "response_class": response_class,
        "parallel_overdue_within_5_seconds": parallel_overdue,
        "before_tools": [row.get("tool_name") for row in before[-3:]],
        "after_tools": [row.get("tool_name") for row in after[:3]],
    }


def choose_samples(
    events: list[dict[str, Any]], sample_size: int
) -> list[dict[str, Any]]:
    """Choose deterministic, class-stratified examples without copying transcript text."""
    selected: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_class[event["response_class"]].append(event)
    for response_class in sorted(by_class):
        for event in by_class[response_class]:
            if event["session_id"] not in seen_sessions:
                selected.append(event)
                seen_sessions.add(event["session_id"])
                break
    for event in events:
        if len(selected) >= sample_size:
            break
        key = (event["session_id"], event["seq"])
        if all((item["session_id"], item["seq"]) != key for item in selected):
            selected.append(event)
    return selected[:sample_size]


def non_task_runs(rows_by_session: dict[str, list[dict[str, Any]]]) -> list[int]:
    """Count non-task tool runs delimited by task actions within matched sessions."""
    runs: list[int] = []
    for rows in rows_by_session.values():
        count = 0
        for row in rows:
            if row["kind"] != "tool_call":
                continue
            if is_task_tool(row):
                if count:
                    runs.append(count)
                count = 0
            else:
                count += 1
        if count:
            runs.append(count)
    return runs


def build_analysis(
    aise: str,
    candidates: tuple[int, ...],
    sample_size: int,
) -> dict[str, Any]:
    """Collect reminder hits and compute descriptive and counterfactual metrics."""
    hits_by_marker = {marker: search_marker(aise, marker) for marker in REMINDER_MARKERS}
    primary_hits = hits_by_marker[REMINDER_MARKERS[0]]
    session_ids = sorted(
        {
            hit["message_ref"]["session_id"]
            for hits in hits_by_marker.values()
            for hit in hits
        }
    )
    rows = query_session_rows(aise, session_ids)
    rows_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_session[row["session_id"]].append(row)

    events = [
        summarize_event(hit, rows_by_session[hit["message_ref"]["session_id"]])
        for hit in primary_hits
    ]
    runs = non_task_runs(rows_by_session)
    tool_call_count = sum(row["kind"] == "tool_call" for row in rows)
    immediate_count = sum(event["immediate_task_response"] for event in events)
    response_counts = Counter(event["response_class"] for event in events)
    candidate_estimates = []
    for threshold in candidates:
        triggers = sum(run // threshold for run in runs)
        candidate_estimates.append(
            {
                "threshold_tool_calls": threshold,
                "same-timing_trigger_upper_bound": triggers,
                "triggers_per_1000_observed_tool_calls": (
                    round(1000 * triggers / tool_call_count, 2)
                    if tool_call_count
                    else None
                ),
                "counterfactual_censored_by_current_25_call_policy": threshold > 25,
            }
        )

    calls_since = [event["calls_since_prior_task_action"] for event in events]
    notice_times = sorted(
        timestamp
        for row in rows
        if row["kind"] == "harness_notice"
        if (timestamp := parse_timestamp(row.get("ts"))) is not None
    )
    reconstructed_current_triggers = (
        next(
            item["same-timing_trigger_upper_bound"]
            for item in candidate_estimates
            if item["threshold_tool_calls"] == 25
        )
        if 25 in candidates
        else None
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "tool": "aise",
            "index_refresh": "existing-only",
            "markers": list(REMINDER_MARKERS),
            "scope": "all indexed providers and workspaces",
        },
        "cohort": {
            "matched_sessions": len(session_ids),
            "first_level_transcript_fragments": len(
                {event["session_id"] for event in events}
            ),
            "first_level_parent_session_families": len(
                {root_session_id(event["session_id"]) for event in events}
            ),
            "first_level_reminders_by_provider": dict(
                sorted(Counter(event["provider"] for event in events).items())
            ),
            "first_level_reminders": len(primary_hits),
            "overdue_reminders": len(hits_by_marker[REMINDER_MARKERS[1]]),
            "no_checklist_reminders": len(hits_by_marker[REMINDER_MARKERS[2]]),
            "tool_calls_in_matched_sessions": tool_call_count,
            "non_task_runs": len(runs),
            "first_notice_at": notice_times[0].isoformat() if notice_times else None,
            "last_notice_at": notice_times[-1].isoformat() if notice_times else None,
        },
        "response": {
            "immediate_task_responses": immediate_count,
            "immediate_task_response_rate": (
                round(immediate_count / len(events), 4) if events else None
            ),
            "response_class_counts": dict(sorted(response_counts.items())),
            "parallel_overdue_within_5_seconds": sum(
                event["parallel_overdue_within_5_seconds"] for event in events
            ),
            "non_task_calls_before_response_p50": percentile(
                [event["non_task_calls_before_response"] for event in events], 0.5
            ),
            "non_task_calls_before_response_p90": percentile(
                [event["non_task_calls_before_response"] for event in events], 0.9
            ),
        },
        "cadence": {
            "calls_since_prior_task_action_p25": percentile(calls_since, 0.25),
            "calls_since_prior_task_action_p50": percentile(calls_since, 0.5),
            "calls_since_prior_task_action_p75": percentile(calls_since, 0.75),
            "calls_since_prior_task_action_p90": percentile(calls_since, 0.9),
            "non_task_run_p50": percentile(runs, 0.5),
            "non_task_run_p75": percentile(runs, 0.75),
            "non_task_run_p90": percentile(runs, 0.9),
            "non_task_run_p95": percentile(runs, 0.95),
            "candidate_estimates": candidate_estimates,
            "observed_vs_reconstructed_25_call_reminders": {
                "observed": len(primary_hits),
                "fragment_local_reconstruction": reconstructed_current_triggers,
                "reconstruction_is_valid_for_counterfactual_rate": (
                    reconstructed_current_triggers == len(primary_hits)
                ),
            },
        },
        "representative_samples": choose_samples(events, sample_size),
        "events": events,
        "limitations": [
            "Search results are observational and do not identify a causal optimum.",
            "Behavior above 25 calls is censored because the current policy intervenes at 25.",
            "Historical reminder counters do not reconstruct from transcript-local tool calls; parent state, compacted fragments, concurrent hooks, or historical implementation behavior may contribute.",
            "The indexed first-level cohort may not represent newer or unindexed harness behavior.",
            "Tool-call counts do not measure token cost, wall-clock cost, or task correctness directly.",
            "Subagent and mirrored records are not treated as independent user sessions in causal claims.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aise", default="aise", help="AI Session Search executable")
    parser.add_argument(
        "--output", type=Path, required=True, help="JSON evidence output"
    )
    parser.add_argument(
        "--candidate-thresholds",
        type=int,
        nargs="+",
        default=DEFAULT_CANDIDATE_THRESHOLDS,
        help="Tool-call thresholds to compare descriptively",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="Maximum deterministic representative-event sample",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.sample_size < 1:
        raise SystemExit("--sample-size must be at least 1")
    candidates = tuple(sorted(set(args.candidate_thresholds)))
    if not candidates or candidates[0] < 1:
        raise SystemExit("--candidate-thresholds must contain positive integers")
    analysis = build_analysis(args.aise, candidates, args.sample_size)
    write_json_output(args.output, analysis)


if __name__ == "__main__":
    main()
