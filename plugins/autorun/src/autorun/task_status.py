"""Canonical task-status semantics shared by lifecycle and persistence."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class TaskStatusPolicy:
    blocks_stop: bool
    prunable: bool
    completed: bool = False
    # Set only by autorun (/ar:task ignore and pause, the delegation marker,
    # the SubagentStop gate); no harness task list reports it, so a resync
    # from that list keeps it unless the list reports the task completed.
    autorun_owned: bool = False
    # Open, but waiting on someone other than the agent: the Stop gate lists
    # it as waiting instead of blocking on it, and a task blocked by it waits
    # too.
    waiting: bool = False


AWAITING_USER_STATUS = "awaiting-user"

STATUS_POLICY = MappingProxyType({
    "pending": TaskStatusPolicy(blocks_stop=True, prunable=False),
    "in_progress": TaskStatusPolicy(blocks_stop=True, prunable=False),
    "paused": TaskStatusPolicy(blocks_stop=False, prunable=False, autorun_owned=True),
    "delegated": TaskStatusPolicy(blocks_stop=False, prunable=False, autorun_owned=True, waiting=True),
    # A delegated task whose subagent finished: blocking again, because the
    # parent must verify the child's results before completing — child-side
    # claims never drive parent state. Set by autorun's SubagentStop gate,
    # never via the harness task tools.
    "delegation-returned": TaskStatusPolicy(blocks_stop=True, prunable=False, autorun_owned=True),
    "completed": TaskStatusPolicy(
        blocks_stop=False, prunable=True, completed=True
    ),
    "deleted": TaskStatusPolicy(
        blocks_stop=False, prunable=True, completed=True
    ),
    "ignored": TaskStatusPolicy(blocks_stop=False, prunable=True, autorun_owned=True),
    # Waiting on a decision only the user can make, named by the agent's
    # AUTORUN_TASK_AWAITING_USER marker. The next prompt the user types (or
    # the agent's own update of the task) restores the status it had.
    AWAITING_USER_STATUS: TaskStatusPolicy(blocks_stop=False, prunable=False, autorun_owned=True, waiting=True),
})

BLOCKING_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.blocks_stop
)
NON_BLOCKING_TASK_STATUSES = frozenset(STATUS_POLICY) - BLOCKING_TASK_STATUSES
PRUNABLE_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.prunable
)
AUTORUN_OWNED_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.autorun_owned
)
COMPLETED_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.completed
)
WAITING_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.waiting
)
# Statuses after which a task no longer holds anything: finished, removed, or
# dismissed. The Stop gate never reads these rows, and a task blocked only by
# them is free to start. ``ignored`` is finished for tracking purposes: the
# agent explicitly dismissed it, so treating it as unfinished would resurrect
# work the user chose not to pursue. This does not make it "completed" for
# reporting; COMPLETED_TASK_STATUSES remains only completed/deleted.
SETTLED_TASK_STATUSES = PRUNABLE_TASK_STATUSES
FINISHED_TASK_STATUSES = SETTLED_TASK_STATUSES
UNFINISHED_TASK_STATUSES = frozenset(STATUS_POLICY) - FINISHED_TASK_STATUSES


def task_status_is_unfinished(status: str) -> bool:
    """Whether a tracked row still represents work, independent of Stop policy."""
    task_status_policy(status)
    return status in UNFINISHED_TASK_STATUSES


def task_status_policy(status: str) -> TaskStatusPolicy:
    """Return semantics for a supported status; reject silent policy gaps."""
    try:
        return STATUS_POLICY[status]
    except KeyError as exc:
        raise ValueError(f"Unknown task status {status!r}") from exc
