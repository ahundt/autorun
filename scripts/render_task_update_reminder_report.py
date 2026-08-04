#!/usr/bin/env python3
"""Render a decision-ready Markdown report from reminder-analysis JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def percent(value: float | None) -> str:
    return "not measured" if value is None else f"{100 * value:.1f}%"


def number(value: float | None) -> str:
    """Render measured values without floating-point interpolation noise."""
    if value is None:
        return "not measured"
    return f"{value:.1f}"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def render(data: dict[str, Any], evidence_path: Path) -> str:
    cohort = data["cohort"]
    response = data["response"]
    cadence = data["cadence"]
    candidate_rows = [
        [
            item["threshold_tool_calls"],
            item["same-timing_trigger_upper_bound"],
            item["triggers_per_1000_observed_tool_calls"],
            "yes"
            if item["counterfactual_censored_by_current_25_call_policy"]
            else "no",
        ]
        for item in cadence["candidate_estimates"]
    ]
    class_rows = [
        [name, count] for name, count in response["response_class_counts"].items()
    ]
    sample_rows = [
        [
            sample["provider"],
            sample["repo_root"] or sample["cwd"] or "unknown",
            f"{sample['session_id']} seq {sample['seq']}",
            sample["calls_since_prior_task_action"],
            sample["response_class"],
            sample["non_task_calls_before_response"],
        ]
        for sample in data["representative_samples"]
    ]
    return (
        f"""# Task-update reminder cadence analysis

## Decision dashboard

{
            table(
                ["Field", "Answer"],
                [
                    [
                        "Artifact role",
                        "Decision brief backed by reproducible observational evidence",
                    ],
                    [
                        "Purpose",
                        "Estimate whether autorun's 25-tool-call task-update reminder balances drift risk and bookkeeping overhead",
                    ],
                    ["Audience", "Autorun maintainers"],
                    [
                        "Current status",
                        "Selected: keep 25 calls; no runtime or configuration change was made",
                    ],
                    [
                        "Evidence baseline",
                        f"AI Session Search existing-only snapshot; generated {data['generated_at']}",
                    ],
                    [
                        "In scope",
                        "Exact task-update harness notices and neighboring tool calls across indexed local providers/workspaces",
                    ],
                    [
                        "Out of scope",
                        "Causal task-quality claims, token accounting, wall-clock benchmarking, and runtime changes",
                    ],
                    [
                        "Decision needed",
                        "None now; optionally approve a controlled 30-call trial if lower interruption frequency remains desirable",
                    ],
                ],
            )
        }

## Before / now / target / consequence

{
            table(
                ["Area", "Before", "Now", "Target", "User consequence"],
                [
                    [
                        "Reminder cadence",
                        "25 tool calls selected as the project default without a session-history cadence study in this report",
                        "25 calls is measured against indexed reminder responses; evidence above 25 remains censored",
                        "Use the lowest-overhead cadence that still catches material plan drift before substantial rework",
                        "Too low forces bookkeeping; too high lets the agent work from a stale plan longer",
                    ]
                ],
            )
        }

## Measured cohort

{
            table(
                ["Measure", "Observed value"],
                [
                    [
                        "Matched transcript fragments (all marker types)",
                        cohort["matched_sessions"],
                    ],
                    [
                        "First-level transcript fragments / parent-session families",
                        f"{cohort['first_level_transcript_fragments']} / {cohort['first_level_parent_session_families']}",
                    ],
                    [
                        "First-level reminders by provider",
                        json.dumps(
                            cohort["first_level_reminders_by_provider"], sort_keys=True
                        ),
                    ],
                    [
                        "Notice time range",
                        f"{cohort['first_notice_at']} to {cohort['last_notice_at']}",
                    ],
                    ["First-level reminders", cohort["first_level_reminders"]],
                    ["Overdue reminders", cohort["overdue_reminders"]],
                    ["No-checklist reminders", cohort["no_checklist_reminders"]],
                    [
                        "Tool calls in matched sessions",
                        cohort["tool_calls_in_matched_sessions"],
                    ],
                    [
                        "Immediate task response rate",
                        percent(response["immediate_task_response_rate"]),
                    ],
                    [
                        "Non-task calls before response, p50 / p90",
                        f"{number(response['non_task_calls_before_response_p50'])} / {number(response['non_task_calls_before_response_p90'])}",
                    ],
                    [
                        "Required→overdue pairs within 5 seconds",
                        response["parallel_overdue_within_5_seconds"],
                    ],
                ],
            )
        }

### Response content

{table(["First task action after reminder", "Count"], class_rows)}

## Candidate cadence exposure — diagnostic only

These values replay transcript-fragment-local non-task runs. They are **not a valid rate forecast**: the replay reconstructs {
            cadence["observed_vs_reconstructed_25_call_reminders"][
                "fragment_local_reconstruction"
            ]
        } reminders at the current threshold while the index contains {
            cadence["observed_vs_reconstructed_25_call_reminders"]["observed"]
        }. Parent-session state, compacted transcript fragments, historical behavior, and concurrent hook completion can all break a local replay. Values above 25 are additionally censored by the existing intervention.

{
            table(
                [
                    "Threshold (tool calls)",
                    "Same-timing trigger upper bound",
                    "Triggers / 1,000 observed tool calls",
                    "Censored by current policy",
                ],
                candidate_rows,
            )
        }

Fragment-local non-task-run percentiles were p50={
            number(cadence["non_task_run_p50"])
        }, p75={number(cadence["non_task_run_p75"])}, p90={
            number(cadence["non_task_run_p90"])
        }, and p95={
            number(cadence["non_task_run_p95"])
        } tool calls. These describe the sampled transcripts; they do not reproduce the runtime counter.

## Decision register

{
            table(
                [
                    "ID",
                    "Question",
                    "Recommendation",
                    "Serious alternative",
                    "Consequence",
                    "Status",
                ],
                [
                    [
                        "DEC-1",
                        "Should the default move away from 25 calls now?",
                        "Keep 25 as the default; treat 25–30 calls as the plausible efficient band and test 30 prospectively before any change",
                        "Raise directly to 30 to reduce interruptions, or lower to 20 to catch drift earlier",
                        "30 reduces idealized checkpoint frequency by 16.7% but permits 20% more unreviewed calls; 20 creates 25% more frequent checkpoints than 25",
                        "Selected for now; confidence moderate-low because the historical counter is not reconstructible",
                    ]
                ],
            )
        }

## Representative sample for manual review

The sample is deterministic and stratified by observed response class, then filled in session-sequence order. It contains references and tool-level summaries, not copied transcript content.

{
            table(
                [
                    "Provider",
                    "Workspace",
                    "Evidence reference",
                    "Calls since task action",
                    "Response class",
                    "Non-task calls before response",
                ],
                sample_rows,
            )
        }

## Method and reproducibility

1. `scripts/analyze_task_update_reminders.py` uses exact `harness-notice` searches for the three configured reminder markers.
2. It queries only matched session IDs through `aise db query`, reconstructs tool-call runs, and classifies the first task action after each first-level reminder.
3. `scripts/render_task_update_reminder_report.py` renders this decision brief from the aggregate JSON.
4. Reproduce with:

```bash
uv run python scripts/analyze_task_update_reminders.py --output {evidence_path}
uv run python scripts/render_task_update_reminder_report.py --input {
            evidence_path
        } --output notes/<dated-report>.md
```

## Limitations

"""
        + "\n".join(f"- {item}" for item in data["limitations"])
        + """

## Exact next action

Keep the configured default at 25. If lower interruption frequency is still desired, run an A/B or sequential trial at 30 that records task-list changes, rework after reminders, forced task calls, and user corrections; do not infer the change from historical trigger counts alone.
"""
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Analysis JSON")
    parser.add_argument("--output", type=Path, required=True, help="Markdown report")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(data, args.input), encoding="utf-8")


if __name__ == "__main__":
    main()
