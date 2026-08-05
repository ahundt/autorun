#!/usr/bin/env python3
"""Audit task-reminder cadence across canonical AI Session Search timelines.

This read-only analysis deliberately separates logical sessions from Claude
``agent-acompact`` transcript fragments.  Exact reminder hits come from
``aise messages search``; long-run call-gap distributions come from bounded,
read-only ``aise db query`` statements over canonical sessions.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
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
    run_json,
    search_marker,
    sql_quote,
    tool_args,
    weighted_quantile,
    write_json_output,
)


DEFAULT_THRESHOLDS = (15, 20, 25, 30, 35, 40, 45, 50, 60, 75)
CODEX_DIRECT_UPDATE_PLAN = re.compile(
    r"^\s*const\s+[^=\n]+=\s*await\s+tools\.update_plan\s*\(",
    re.IGNORECASE,
)
CODEX_PARALLEL_UPDATE_PLAN = re.compile(
    r"^\s*const\s+[^=\n]+=\s*await\s+Promise\.all\s*\(\s*\[.*?"
    r"(?:^|\n)\s*tools\.update_plan\s*\(",
    re.IGNORECASE | re.DOTALL,
)


def index_snapshot_status(aise: str) -> dict[str, Any]:
    """Capture the read-only index receipt used by the analysis."""
    status = run_json(
        [
            aise,
            "doctor",
            "--format",
            "json",
            "--index-refresh",
            "existing-only",
            "--skip-release-notification",
        ]
    )
    db_path = status.get("db_path")
    try:
        db_stat = Path(db_path).stat() if db_path else None
    except OSError:
        db_stat = None
    status["db_file"] = {
        "size_bytes": db_stat.st_size if db_stat else None,
        "mtime_ns": db_stat.st_mtime_ns if db_stat else None,
    }
    return status


def session_metadata(
    aise: str, session_ids: Iterable[str], timeout_ms: int
) -> dict[str, dict[str, Any]]:
    quoted = ",".join(sql_quote(value) for value in sorted(set(session_ids)))
    if not quoted:
        return {}
    rows = db_query(
        aise,
        f"""
        SELECT id, provider, parent_session_id, agent_label, cwd, repo_root,
               created_at, updated_at, parse_version, parse_warning
          FROM sessions
         WHERE id IN ({quoted})
        """,
        timeout_ms,
    )
    return {row["id"]: row for row in rows}


def is_actual_notice(hit: dict[str, Any], metadata: dict[str, Any]) -> bool:
    """Reject quoted source/transcript markers embedded in harness notices."""
    if metadata.get("agent_label") == "guardian":
        return False
    provider = hit["message_metadata"]["provider"]
    occurrence = hit.get("match", {}).get("literal_occurrence", {})
    position = occurrence.get("field_start_char")
    session_id = hit["message_ref"]["session_id"]
    if provider == "claude":
        return (
            "/agent-acompact-" in session_id
            and isinstance(position, int)
            and position < 1_000
        )
    return True


def canonical_root(session_id: str, metadata: dict[str, Any]) -> str:
    return metadata.get("parent_session_id") or session_id


def is_tool_call(row: dict[str, Any]) -> bool:
    return row["kind"] == "tool_call" or (
        row["kind"] == "unknown"
        and row["role"] == "tool"
        and row.get("tool_name") is not None
    )


def codex_exec_source(row: dict[str, Any]) -> str:
    """Return JavaScript from a Codex ``exec`` event, or an empty string."""
    try:
        payload = json.loads(row.get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    args = payload.get("args")
    return args if isinstance(args, str) else ""


def is_codex_progress_tool(row: dict[str, Any]) -> bool:
    """Recognize direct and current nested Codex ``update_plan`` events."""
    if row.get("provider") != "codex":
        return False
    name = normalized_tool(row)
    if name == "update_plan":
        return True
    if name != "exec":
        return False
    source = codex_exec_source(row)
    return bool(
        CODEX_DIRECT_UPDATE_PLAN.search(source)
        or CODEX_PARALLEL_UPDATE_PLAN.search(source)
    )


def progress_tool_sql(alias: str = "m") -> str:
    """Return the SQL equivalent of :func:`is_progress_tool`.

    AI Session Search currently stores Codex MCP calls beneath a top-level
    ``exec`` event. Its generated JavaScript either directly assigns an awaited
    call or places the call in an assigned ``Promise.all``. Requiring those
    wrappers avoids counting quoted ``tools.update_plan(`` searches.
    """
    args_text = (
        "lower(COALESCE(CASE WHEN json_valid({0}.content) "
        "THEN json_extract({0}.content, '$.args') END, ''))"
    ).format(alias)
    stripped_args = f"ltrim({args_text})"
    codex_nested = (
        f"({stripped_args} LIKE 'const %= await tools.update_plan(%' "
        f"OR {stripped_args} LIKE 'const %=await tools.update_plan(%' "
        f"OR (({stripped_args} LIKE 'const %= await promise.all([%' "
        f"OR {stripped_args} LIKE 'const %=await promise.all([%') "
        f"AND {args_text} LIKE '%' || char(10) || '%tools.update_plan(%'))"
    )
    return (
        f"(({alias}.provider IN ('claude','claude-desktop') "
        f"AND lower({alias}.tool_name) IN ('taskcreate','taskupdate','todowrite')) "
        f"OR ({alias}.provider = 'codex' AND ("
        f"lower({alias}.tool_name) = 'update_plan' OR "
        f"(lower({alias}.tool_name) = 'exec' AND {codex_nested}))) "
        f"OR ({alias}.provider = 'antigravity' "
        f"AND lower({alias}.tool_name) = 'manage_task'))"
    )


def is_progress_tool(row: dict[str, Any]) -> bool:
    name = normalized_tool(row)
    provider = row.get("provider")
    if provider in {"claude", "claude-desktop"}:
        return name in {"taskcreate", "taskupdate", "todowrite"}
    if provider == "codex":
        return is_codex_progress_tool(row)
    if provider == "antigravity":
        return name == "manage_task"
    return False


def is_review_tool(row: dict[str, Any]) -> bool:
    return normalized_tool(row) in {"tasklist", "todoread"}


def classify_progress(row: dict[str, Any] | None) -> str:
    if row is None:
        return "none_observed"
    if is_codex_progress_tool(row):
        return "plan_snapshot"
    name = normalized_tool(row)
    args = tool_args(row)
    if name == "taskcreate":
        return "new_work_recorded"
    if name in {"todowrite", "update_plan", "manage_task"}:
        return "plan_snapshot"
    if name == "taskupdate":
        lowered = {key.lower() for key in args}
        if lowered & TASKUPDATE_DETAIL_KEYS:
            return "work_or_dependency_detail"
        if "status" in lowered:
            return "status_only"
        return "other_task_mutation"
    return "other_progress_action"


def query_event_rows(
    aise: str,
    session_ids: Iterable[str],
    until: str,
    timeout_ms: int,
) -> dict[str, list[dict[str, Any]]]:
    quoted = ",".join(sql_quote(value) for value in sorted(set(session_ids)))
    if not quoted:
        return {}
    rows = db_query(
        aise,
        f"""
        SELECT session_id, provider, seq, role, ts, tool_name, kind, content
          FROM messages
         WHERE session_id IN ({quoted})
           AND ts <= {sql_quote(until)}
           AND (
               kind = 'tool_call'
               OR (kind = 'unknown' AND role = 'tool' AND tool_name IS NOT NULL)
               OR kind = 'harness_notice'
           )
         ORDER BY session_id, seq
        """,
        timeout_ms,
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["session_id"]].append(row)
    return grouped


def summarize_event(
    hit: dict[str, Any],
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    seq = hit["message_ref"]["message_seq"]
    calls = [row for row in rows if is_tool_call(row)]
    after = [row for row in calls if row["seq"] > seq]
    next_progress_index = next(
        (index for index, row in enumerate(after) if is_progress_tool(row)), None
    )
    next_progress = (
        after[next_progress_index] if next_progress_index is not None else None
    )
    next_interaction_index = next(
        (
            index
            for index, row in enumerate(after)
            if is_progress_tool(row) or is_review_tool(row)
        ),
        None,
    )
    reminder_row = next(
        (row for row in rows if row["seq"] == seq and row["kind"] == "harness_notice"),
        None,
    )
    return {
        "session_id": hit["message_ref"]["session_id"],
        "root_session_id": canonical_root(hit["message_ref"]["session_id"], metadata),
        "seq": seq,
        "ts": reminder_row.get("ts") if reminder_row else None,
        "provider": hit["message_metadata"]["provider"],
        "response_class": classify_progress(next_progress),
        "immediate_progress_response": next_progress_index == 0,
        "progress_within_three_calls": (
            next_progress_index is not None and next_progress_index < 3
        ),
        "review_before_progress": (
            next_interaction_index is not None
            and is_review_tool(after[next_interaction_index])
        ),
        "tool_calls_before_progress": (
            next_progress_index if next_progress_index is not None else len(after)
        ),
    }


def run_histogram(
    aise: str,
    since: str,
    until: str,
    timeout_ms: int,
    *,
    complete_interupdate_only: bool,
) -> list[dict[str, Any]]:
    """Return canonical positive call-gap lengths, grouped compactly.

    The complete-interupdate view keeps only intervals bounded by a progress
    reset on both sides. The broad view also includes leading and terminal
    spans, which can contain sessions where task enforcement was unavailable
    or inactive.
    """
    eligibility = (
        "AND run_group > 0 AND run_group < total_resets"
        if complete_interupdate_only
        else ""
    )
    return db_query(
        aise,
        f"""
        WITH base AS (
          SELECT m.provider, m.session_id, m.seq,
                 CASE WHEN {progress_tool_sql("m")} THEN 1 ELSE 0 END AS is_reset
            FROM messages AS m
            JOIN sessions AS s ON s.id = m.session_id
           WHERE m.ts >= {sql_quote(since)}
             AND m.ts <= {sql_quote(until)}
             AND (
                 m.kind = 'tool_call'
                 OR (m.kind = 'unknown' AND m.role = 'tool' AND m.tool_name IS NOT NULL)
             )
             AND m.provider <> 'gemini-cli'
             AND COALESCE(s.agent_label,'') NOT LIKE 'acompact-%'
             AND COALESCE(s.agent_label,'') <> 'guardian'
        ), tagged_initial AS (
          SELECT *,
                 SUM(is_reset) OVER (
                   PARTITION BY session_id ORDER BY seq ROWS UNBOUNDED PRECEDING
                 ) AS run_group
            FROM base
        ), tagged AS (
          SELECT *,
                 MAX(run_group) OVER (PARTITION BY session_id) AS total_resets
            FROM tagged_initial
        ), runs AS (
          SELECT provider, session_id, run_group, total_resets,
                 SUM(CASE WHEN is_reset = 0 THEN 1 ELSE 0 END) AS run_len
            FROM tagged
           GROUP BY provider, session_id, run_group, total_resets
        )
        SELECT provider, run_len, COUNT(*) AS runs
          FROM runs
         WHERE run_len > 0
           {eligibility}
         GROUP BY provider, run_len
         ORDER BY provider, run_len
        """,
        timeout_ms,
    )


def histogram_summary(
    rows: list[dict[str, Any]],
    thresholds: tuple[int, ...],
    *,
    window_days: int,
) -> dict[str, Any]:
    by_provider: dict[str, Counter[int]] = defaultdict(Counter)
    combined: Counter[int] = Counter()
    for row in rows:
        length = int(row["run_len"])
        count = int(row["runs"])
        by_provider[row["provider"]][length] += count
        combined[length] += count

    def summarize(histogram: Counter[int]) -> dict[str, Any]:
        runs = sum(histogram.values())
        calls = sum(length * count for length, count in histogram.items())
        estimates = []
        base_triggers = sum(
            count * (length // 25) for length, count in histogram.items()
        )
        for threshold in thresholds:
            reaching = sum(
                count for length, count in histogram.items() if length >= threshold
            )
            triggers = sum(
                count * (length // threshold) for length, count in histogram.items()
            )
            first_offense = sum(
                count * ((length // threshold + 1) // 2)
                for length, count in histogram.items()
            )
            second_offense = triggers - first_offense
            estimates.append(
                {
                    "threshold": threshold,
                    "runs_reaching": reaching,
                    "share_positive_runs_reaching": round(reaching / runs, 6)
                    if runs
                    else None,
                    "modeled_notification_crossings": triggers,
                    "modeled_first_offense_notices": first_offense,
                    "modeled_second_offense_notices": second_offense,
                    "estimated_hook_messages_lower_bound": triggers,
                    "estimated_hook_messages_upper_bound": triggers * 2,
                    "annualized_notification_crossings": round(
                        triggers * 365 / window_days
                    ),
                    "modeled_crossing_reduction_vs_25": (
                        round(1 - triggers / base_triggers, 6)
                        if base_triggers and threshold != 25
                        else 0.0
                    ),
                }
            )
        return {
            "positive_runs": runs,
            "non_task_calls": calls,
            "mean_run": round(calls / runs, 3) if runs else None,
            "p50": weighted_quantile(histogram, 0.50),
            "p75": weighted_quantile(histogram, 0.75),
            "p90": weighted_quantile(histogram, 0.90),
            "p95": weighted_quantile(histogram, 0.95),
            "max_run": max(histogram) if histogram else None,
            "candidate_estimates": estimates,
        }

    return {
        "all_providers": summarize(combined),
        "by_provider": {
            provider: summarize(histogram)
            for provider, histogram in sorted(by_provider.items())
        },
    }


def cohort_summary(
    aise: str,
    since: str,
    until: str,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    return db_query(
        aise,
        f"""
        SELECT m.provider,
               CASE WHEN s.parent_session_id IS NULL THEN 'user' ELSE 'subagent' END AS session_kind,
               COUNT(DISTINCT s.id) AS sessions,
               COUNT(*) AS messages
          FROM messages AS m
          JOIN sessions AS s ON s.id = m.session_id
         WHERE m.ts >= {sql_quote(since)}
           AND m.ts <= {sql_quote(until)}
           AND COALESCE(s.agent_label,'') NOT LIKE 'acompact-%'
           AND COALESCE(s.agent_label,'') <> 'guardian'
         GROUP BY m.provider, session_kind
         ORDER BY m.provider, session_kind
        """,
        timeout_ms,
    )


def startup_policy_summary(
    aise: str,
    since: str,
    until: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Replay startup and steady-state cadences on complete session timelines.

    Sessions must begin inside the window so ``run_group = 0`` is an actual
    post-start leading span rather than a left-truncated fragment.
    """
    rows = db_query(
        aise,
        f"""
        WITH base AS (
          SELECT m.provider, m.session_id, m.seq,
                 CASE WHEN s.parent_session_id IS NULL
                      THEN 'user' ELSE 'subagent' END AS session_kind,
                 CASE WHEN {progress_tool_sql("m")} THEN 1 ELSE 0 END AS is_reset
            FROM messages AS m
            JOIN sessions AS s ON s.id = m.session_id
           WHERE s.created_at >= {sql_quote(since)}
             AND s.created_at <= {sql_quote(until)}
             AND m.ts <= {sql_quote(until)}
             AND (
                 m.kind = 'tool_call'
                 OR (m.kind = 'unknown' AND m.role = 'tool'
                     AND m.tool_name IS NOT NULL)
             )
             AND m.provider <> 'gemini-cli'
             AND COALESCE(s.agent_label,'') NOT LIKE 'acompact-%'
             AND COALESCE(s.agent_label,'') <> 'guardian'
        ), tagged AS (
          SELECT *,
                 SUM(is_reset) OVER (
                   PARTITION BY session_id ORDER BY seq ROWS UNBOUNDED PRECEDING
                 ) AS run_group
            FROM base
        ), runs AS (
          SELECT provider, session_kind, session_id, run_group,
                 SUM(CASE WHEN is_reset = 0 THEN 1 ELSE 0 END) AS run_len
            FROM tagged
           GROUP BY provider, session_kind, session_id, run_group
        ), per_session AS (
          SELECT provider, session_kind, session_id,
                 MAX(run_group) AS reset_count,
                 SUM(CASE WHEN run_group = 0 THEN run_len ELSE 0 END) AS leading_calls,
                 SUM(CAST(run_len / 25 AS INTEGER)) AS fixed_25_crossings,
                 SUM(CAST(run_len / 50 AS INTEGER)) AS fixed_50_crossings,
                 SUM(
                   CASE
                     WHEN run_group = 0 AND run_len >= 25
                       THEN 1 + CAST((run_len - 25) / 50 AS INTEGER)
                     WHEN run_group > 0 THEN CAST(run_len / 50 AS INTEGER)
                     ELSE 0
                   END
                 ) AS staged_25_then_50_crossings,
                 SUM(
                   CASE WHEN run_group = 0
                        THEN CAST(run_len / 5 AS INTEGER) ELSE 0 END
                 ) AS leading_startup_5_exposures
            FROM runs
           GROUP BY provider, session_kind, session_id
        )
        SELECT provider, session_kind,
               COUNT(*) AS sessions_with_tool_calls,
               SUM(CASE WHEN reset_count = 0 THEN 1 ELSE 0 END)
                 AS sessions_without_progress_update,
               SUM(CASE WHEN leading_calls >= 5 THEN 1 ELSE 0 END)
                 AS leading_sessions_reaching_5,
               SUM(CASE WHEN leading_calls >= 25 THEN 1 ELSE 0 END)
                 AS leading_sessions_reaching_25,
               SUM(CASE WHEN leading_calls >= 50 THEN 1 ELSE 0 END)
                 AS leading_sessions_reaching_50,
               SUM(CASE WHEN reset_count > 0 AND leading_calls < 25
                        THEN 1 ELSE 0 END) AS first_update_before_25,
               SUM(CASE WHEN reset_count > 0 AND leading_calls BETWEEN 25 AND 49
                        THEN 1 ELSE 0 END) AS first_update_from_25_through_49,
               SUM(leading_calls) AS leading_calls,
               SUM(leading_startup_5_exposures) AS leading_startup_5_exposures,
               SUM(fixed_25_crossings) AS fixed_25_crossings,
               SUM(fixed_50_crossings) AS fixed_50_crossings,
               SUM(staged_25_then_50_crossings)
                 AS staged_25_then_50_crossings
          FROM per_session
         GROUP BY provider, session_kind
         ORDER BY provider, session_kind
        """,
        timeout_ms,
    )
    measures = (
        "sessions_with_tool_calls",
        "sessions_without_progress_update",
        "leading_sessions_reaching_5",
        "leading_sessions_reaching_25",
        "leading_sessions_reaching_50",
        "first_update_before_25",
        "first_update_from_25_through_49",
        "leading_calls",
        "leading_startup_5_exposures",
        "fixed_25_crossings",
        "fixed_50_crossings",
        "staged_25_then_50_crossings",
    )
    normalized_rows = []
    for row in rows:
        normalized = dict(row)
        for measure in measures:
            normalized[measure] = int(normalized[measure] or 0)
        normalized_rows.append(normalized)

    def aggregate(selected: Iterable[dict[str, Any]]) -> dict[str, int]:
        selected_rows = list(selected)
        return {
            measure: sum(row[measure] for row in selected_rows) for measure in measures
        }

    by_kind = {
        kind: aggregate(row for row in normalized_rows if row["session_kind"] == kind)
        for kind in ("user", "subagent")
    }
    combined = aggregate(normalized_rows)
    user_scoped_staged = (
        by_kind["user"]["staged_25_then_50_crossings"]
        + by_kind["subagent"]["fixed_50_crossings"]
    )

    def relative_delta(candidate: int, baseline: int) -> float | None:
        return round(candidate / baseline - 1, 6) if baseline else None

    return {
        "definition": (
            "Fixed policies reset at every provider-native progress action. "
            "The staged policy emits at call 25 in the leading post-start span, "
            "then every 50 calls; after the first progress action all spans use "
            "50. Only sessions created inside the window are included."
        ),
        "by_provider_and_session_kind": normalized_rows,
        "by_session_kind": by_kind,
        "all_sessions": combined,
        "comparisons": {
            "global_staged_crossings": combined["staged_25_then_50_crossings"],
            "global_staged_delta_vs_fixed_50": relative_delta(
                combined["staged_25_then_50_crossings"],
                combined["fixed_50_crossings"],
            ),
            "global_staged_delta_vs_fixed_25": relative_delta(
                combined["staged_25_then_50_crossings"],
                combined["fixed_25_crossings"],
            ),
            "user_staged_subagents_fixed_50_crossings": user_scoped_staged,
            "user_staged_subagents_fixed_50_delta_vs_all_fixed_50": relative_delta(
                user_scoped_staged, combined["fixed_50_crossings"]
            ),
        },
    }


def manual_task_maintenance_summary(
    aise: str,
    since: str,
    until: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Return a narrow lower bound on user-authored plan-maintenance prompts."""
    rows = db_query(
        aise,
        f"""
        WITH manual AS (
          SELECT m.provider, m.session_id
            FROM messages AS m
            JOIN sessions AS s ON s.id = m.session_id
           WHERE m.ts >= {sql_quote(since)}
             AND m.ts <= {sql_quote(until)}
             AND m.role = 'user'
             AND s.parent_session_id IS NULL
             AND COALESCE(s.agent_label,'') NOT LIKE 'acompact-%'
             AND COALESCE(s.agent_label,'') <> 'guardian'
             AND length(m.content) <= 500
             AND m.content NOT LIKE 'Stop hook feedback:%'
             AND m.content NOT LIKE '<system-reminder>%'
             AND m.content NOT LIKE '# Task Staleness%'
             AND (
               (lower(m.content) LIKE '%ongoing tasks%'
                AND lower(m.content) LIKE '%updat%'
                AND lower(m.content) LIKE '%task%')
               OR lower(m.content) LIKE '%keep your tasks list updated%'
               OR lower(m.content) LIKE '%keep your task list updated%'
               OR lower(m.content) LIKE '%update your task plan tasks%'
               OR lower(m.content) LIKE '%update your tasks plan%'
               OR lower(m.content) LIKE '%update your plan tasks%'
               OR lower(m.content) LIKE '%plan tasks%retained%updated%'
               OR lower(m.content) LIKE '%plan tasks%retaoned%updated%'
               OR lower(m.content) LIKE '%task breakdown%progress updated%'
               OR lower(m.content) LIKE '%update your tasks and proceed%'
             )
        )
        SELECT provider, COUNT(*) AS messages,
               COUNT(DISTINCT session_id) AS sessions
          FROM manual
         GROUP BY provider
         ORDER BY provider
        """,
        timeout_ms,
    )
    return {
        "messages": sum(int(row["messages"]) for row in rows),
        "sessions": sum(int(row["sessions"]) for row in rows),
        "by_provider": rows,
        "definition": (
            "Narrow deterministic lower bound on user-authored directives to "
            "maintain plan/task state. User sessions only; compact fragments, "
            "subagent-inherited messages, guardian records, hook notices, Stop "
            "feedback, long prompts, and looser paraphrases are excluded. Some "
            "matched directives also add substantive work, so this measures "
            "task-maintenance demand rather than pure reminder causality."
        ),
    }


def direct_block_summary(
    aise: str,
    since: str,
    until: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Count structurally identifiable denied tool calls, excluding quotations."""
    rows = db_query(
        aise,
        f"""
        WITH direct_blocks AS (
          SELECT provider, session_id, ts
            FROM messages
           WHERE ts >= {sql_quote(since)}
             AND ts <= {sql_quote(until)}
             AND (
               (
                 provider = 'claude'
                 AND kind IN ('tool_result','unknown')
                 AND content LIKE 'BLOCKED -- your %'
               )
               OR (
                 provider = 'codex'
                 AND kind = 'tool_result'
                 AND (
                   content LIKE 'Command blocked by PreToolUse hook: BLOCKED -- your %'
                   OR content LIKE 'Tool call blocked by PreToolUse hook: BLOCKED -- your %'
                 )
               )
             )
             AND content LIKE '%task list has not been updated despite a previous warning.%'
        )
        SELECT provider, COUNT(*) AS records,
               COUNT(DISTINCT session_id) AS sessions,
               MIN(ts) AS first_ts, MAX(ts) AS last_ts
          FROM direct_blocks
         GROUP BY provider
         ORDER BY provider
        """,
        timeout_ms,
    )
    return {
        "records": sum(int(row["records"]) for row in rows),
        "sessions": sum(int(row["sessions"]) for row in rows),
        "by_provider": rows,
        "definition": (
            "Direct tool-result records whose complete content begins with the "
            "runtime denial text; quoted code, searches, reports, and compact "
            "hook-notice mirrors are excluded."
        ),
    }


def build_analysis(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    index_status = index_snapshot_status(args.aise)
    as_of = args.as_of
    long_since = (as_of - timedelta(days=args.lookback_days)).isoformat()
    recent_since = (as_of - timedelta(days=args.recent_days)).isoformat()
    until = as_of.isoformat()
    raw_hits = {
        marker: search_marker(args.aise, marker, long_since, until)
        for marker in REMINDER_MARKERS
    }
    candidate_ids = {
        hit["message_ref"]["session_id"] for hits in raw_hits.values() for hit in hits
    }
    metadata = session_metadata(args.aise, candidate_ids, args.sql_timeout_ms)
    hits = {
        marker: [
            hit
            for hit in marker_hits
            if is_actual_notice(hit, metadata.get(hit["message_ref"]["session_id"], {}))
        ]
        for marker, marker_hits in raw_hits.items()
    }
    accepted_ids = {
        hit["message_ref"]["session_id"]
        for marker_hits in hits.values()
        for hit in marker_hits
    }
    event_rows = query_event_rows(
        args.aise,
        accepted_ids,
        until,
        args.sql_timeout_ms,
    )
    events_by_marker = {
        marker: [
            summarize_event(
                hit,
                event_rows[hit["message_ref"]["session_id"]],
                metadata.get(hit["message_ref"]["session_id"], {}),
            )
            for hit in marker_hits
        ]
        for marker, marker_hits in hits.items()
    }
    for events in events_by_marker.values():
        events.sort(
            key=lambda event: (
                event["ts"] or "",
                event["session_id"],
                event["seq"],
            )
        )
    primary_events = events_by_marker[REMINDER_MARKERS[0]]
    overdue_events = events_by_marker[REMINDER_MARKERS[1]]
    ordinals: Counter[str] = Counter()
    for event in primary_events:
        ordinals[event["root_session_id"]] += 1
        event["root_reminder_ordinal"] = ordinals[event["root_session_id"]]
    response_counts = Counter(event["response_class"] for event in primary_events)
    first_events = [
        event for event in primary_events if event["root_reminder_ordinal"] == 1
    ]
    repeat_events = [
        event for event in primary_events if event["root_reminder_ordinal"] > 1
    ]
    substantive_classes = {
        "new_work_recorded",
        "work_or_dependency_detail",
    }

    def response_slice(events: list[dict[str, Any]]) -> dict[str, Any]:
        substantive = sum(
            event["response_class"] in substantive_classes for event in events
        )
        return {
            "events": len(events),
            "substantive_plan_changes": substantive,
            "non_substantive_or_none": len(events) - substantive,
            "substantive_plan_change_rate": round(substantive / len(events), 6)
            if events
            else None,
            "immediate_counter_resets": sum(
                event["immediate_progress_response"] for event in events
            ),
            "immediate_substantive_plan_changes": sum(
                event["immediate_progress_response"]
                and event["response_class"] in substantive_classes
                for event in events
            ),
            "substantive_plan_changes_within_three_calls": sum(
                event["progress_within_three_calls"]
                and event["response_class"] in substantive_classes
                for event in events
            ),
        }

    thresholds = tuple(sorted(set(args.thresholds)))
    return {
        "schema_version": 6,
        "generated_at": generated_at.isoformat(),
        "source": {
            "tool": "aise",
            "marker_search_index_refresh": "existing-only",
            "sql_index_access": (
                "aise db query opens the current index read-only and rejects "
                "the --index-refresh option"
            ),
            "lookback_days": args.lookback_days,
            "recent_days": args.recent_days,
            "long_since": long_since,
            "recent_since": recent_since,
            "as_of": until,
            "sql_timeout_ms": args.sql_timeout_ms,
            "index_status": index_status,
        },
        "cohort": {
            "long_window": cohort_summary(
                args.aise,
                long_since,
                until,
                args.sql_timeout_ms,
            ),
            "recent_window": cohort_summary(
                args.aise,
                recent_since,
                until,
                args.sql_timeout_ms,
            ),
        },
        "startup_policy": {
            "long_window": startup_policy_summary(
                args.aise,
                long_since,
                until,
                args.sql_timeout_ms,
            ),
            "recent_window": startup_policy_summary(
                args.aise,
                recent_since,
                until,
                args.sql_timeout_ms,
            ),
        },
        "manual_task_maintenance": {
            "long_window": manual_task_maintenance_summary(
                args.aise,
                long_since,
                until,
                args.sql_timeout_ms,
            ),
            "recent_window": manual_task_maintenance_summary(
                args.aise,
                recent_since,
                until,
                args.sql_timeout_ms,
            ),
        },
        "reminders": {
            "marker_scope_warning": (
                "These preserved Claude hook-notice markers are an outcome sample, "
                "not a total reminder count. Codex developer-hook reminders are not "
                "durably represented by this marker path."
            ),
            "raw_search_hits": {
                marker: len(value) for marker, value in raw_hits.items()
            },
            "accepted_actual_notices": {
                marker: len(value) for marker, value in hits.items()
            },
            "required_parent_families": len(
                {event["root_session_id"] for event in primary_events}
            ),
            "required_by_parent_family": dict(
                sorted(
                    Counter(
                        event["root_session_id"] for event in primary_events
                    ).items()
                )
            ),
            "accepted_recent_60d": {
                marker: sum(
                    bool(event["ts"] and event["ts"] >= recent_since)
                    for event in events_by_marker[marker]
                )
                for marker in REMINDER_MARKERS
            },
        },
        "direct_blocked_tool_calls": {
            "long_window": direct_block_summary(
                args.aise,
                long_since,
                until,
                args.sql_timeout_ms,
            ),
            "recent_window": direct_block_summary(
                args.aise,
                recent_since,
                until,
                args.sql_timeout_ms,
            ),
        },
        "responses": {
            "binary_definition": (
                "Substantive means a new task/work item or new work/dependency "
                "detail. Status-only updates, other small mutations, read-only "
                "review, and no observed update are non-substantive."
            ),
            "classes": dict(sorted(response_counts.items())),
            "required_notices": response_slice(primary_events),
            "overdue_notices": response_slice(overdue_events),
            "required_first_in_parent_family": response_slice(first_events),
            "required_repeat_in_parent_family": response_slice(repeat_events),
        },
        "call_gaps": {
            "model_definition": (
                "A notification crossing is one multiple of the candidate threshold "
                "inside an observed non-progress call gap. Runtime can emit one "
                "PostTool notification and at most one follow-on PreTool warning or "
                "denial per crossing, so total hook-message overhead is bounded by "
                "one to two messages per modeled crossing."
            ),
            "long_window": {
                "broad_exposure": histogram_summary(
                    run_histogram(
                        args.aise,
                        long_since,
                        until,
                        args.sql_timeout_ms,
                        complete_interupdate_only=False,
                    ),
                    thresholds,
                    window_days=args.lookback_days,
                ),
                "complete_interupdate": histogram_summary(
                    run_histogram(
                        args.aise,
                        long_since,
                        until,
                        args.sql_timeout_ms,
                        complete_interupdate_only=True,
                    ),
                    thresholds,
                    window_days=args.lookback_days,
                ),
            },
            "recent_window": {
                "broad_exposure": histogram_summary(
                    run_histogram(
                        args.aise,
                        recent_since,
                        until,
                        args.sql_timeout_ms,
                        complete_interupdate_only=False,
                    ),
                    thresholds,
                    window_days=args.recent_days,
                ),
                "complete_interupdate": histogram_summary(
                    run_histogram(
                        args.aise,
                        recent_since,
                        until,
                        args.sql_timeout_ms,
                        complete_interupdate_only=True,
                    ),
                    thresholds,
                    window_days=args.recent_days,
                ),
            },
        },
        "events": {
            "required": primary_events,
            "overdue": overdue_events,
        },
        "limitations": [
            "For hook-active runs that triggered at 25, the untreated no-reminder continuation is counterfactually unobserved; actual post-reminder actions and longer gaps elsewhere remain observable.",
            "The index does not identify whether autorun hooks were installed for every eligible session.",
            "Claude agent-acompact fragments are excluded from baseline gap distributions to avoid transcript duplication, but old parent records can still be incomplete.",
            "The actual-notice filter is provider-aware because literal content search also finds quoted marker text inside guardian notices.",
            "Tool-call gaps are exposure proxies; they do not directly measure task correctness, rework, tokens, or wall-clock cost.",
            "Reminder outcome evidence is clustered in a small number of parent-session families.",
            "Startup-policy replay knows session ancestry from the index. Runtime scope instead uses the harness agent_id propagated by EventContext; agent_type alone is not a reliable subagent discriminator.",
            "The leading five-call count is an exposure replay, not an actual notice count, because indexed history cannot reconstruct active-task state and hook availability for every call.",
            "User-authored task-maintenance matching is deliberately narrow and cannot distinguish every reminder from a prompt that also introduces new work.",
            "Codex progress detection covers direct update_plan records plus the current AI Session Search exec wrappers: an assigned awaited call or an assigned Promise.all containing tools.update_plan. A future parser or wrapper-shape change requires updating both progress_tool_sql and is_codex_progress_tool.",
        ],
    }


def parse_timestamp(value: str) -> datetime:
    """Parse one timezone-aware ISO-8601 cutoff and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--as-of must be a timezone-aware ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            "--as-of must include Z or an explicit UTC offset"
        )
    return parsed.astimezone(timezone.utc)


class AuditHelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Show defaults while preserving the reproduction recipe's line breaks."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=AuditHelpFormatter,
        epilog="""Reproducible workflow:
  1. Refresh once before selecting the cutoff. With MCP, call
     get_index_status(index_refresh="auto") and record its refresh timestamp.
  2. Make every later MCP read use index_refresh="existing-only". This script
     forces existing-only for indexed marker searches. Its `aise db query`
     calls are already read-only and reject the CLI --index-refresh option.
  3. Pass an explicit --as-of and retain the JSON receipt. The recent window
     is nested inside the lookback window and both end at that inclusive cutoff.
     The receipt records `aise doctor` snapshot health plus database size/mtime;
     archive the SQLite index too when future bit-for-bit replay is required.

Example (the policy replay is 25 initial / 50 subsequent):
  uv run python scripts/audit_task_update_reminders_longitudinal.py \\
    --output /private/tmp/task-reminder-audit.json \\
    --lookback-days 150 --recent-days 60 \\
    --as-of 2026-08-03T18:11:14.000Z --sql-timeout-ms 120000

The output path is overwritten. SQL timeout failures are errors, not zero
results. --thresholds changes only the fixed-interval call-gap candidates; the
startup replay always compares fixed 25, fixed 50, and staged 25 then 50.
""",
    )
    parser.add_argument(
        "--aise",
        default="aise",
        help="AI Session Search executable name or path used for messages search and db query",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="JSON receipt path; parent directories are created and an existing file is overwritten",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=150,
        help="positive number of days in the full window ending at --as-of",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=60,
        help="positive number of days in the nested recent window; cannot exceed --lookback-days",
    )
    parser.add_argument(
        "--as-of",
        type=parse_timestamp,
        default=datetime.now(timezone.utc),
        help=(
            "inclusive timezone-aware ISO-8601 cutoff normalized to UTC; the "
            "default is current UTC, so pass this explicitly for reproducible totals"
        ),
    )
    parser.add_argument(
        "--sql-timeout-ms",
        type=int,
        default=120_000,
        help="positive timeout per read-only SQL query in milliseconds; it does not limit indexed marker searches",
    )
    parser.add_argument(
        "--thresholds",
        type=int,
        nargs="+",
        default=DEFAULT_THRESHOLDS,
        metavar="N",
        help="positive fixed-interval candidates for call-gap replay; values are deduplicated and must include 25",
    )
    args = parser.parse_args()
    if args.lookback_days < args.recent_days or args.recent_days < 1:
        parser.error("require lookback-days >= recent-days >= 1")
    if args.sql_timeout_ms < 1:
        parser.error("--sql-timeout-ms must be positive")
    if not args.thresholds or min(args.thresholds) < 1 or 25 not in args.thresholds:
        parser.error("--thresholds must be positive and include 25")
    return args


def main() -> None:
    args = parse_args()
    analysis = build_analysis(args)
    write_json_output(args.output, analysis)


if __name__ == "__main__":
    main()
