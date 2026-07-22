"""Canonical task-status semantics shared by lifecycle and persistence."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class TaskStatusPolicy:
    blocks_stop: bool
    prunable: bool
    completed: bool = False


STATUS_POLICY = MappingProxyType({
    "pending": TaskStatusPolicy(blocks_stop=True, prunable=False),
    "in_progress": TaskStatusPolicy(blocks_stop=True, prunable=False),
    "paused": TaskStatusPolicy(blocks_stop=False, prunable=False),
    "delegated": TaskStatusPolicy(blocks_stop=False, prunable=False),
    "completed": TaskStatusPolicy(
        blocks_stop=False, prunable=True, completed=True
    ),
    "deleted": TaskStatusPolicy(
        blocks_stop=False, prunable=True, completed=True
    ),
    "ignored": TaskStatusPolicy(blocks_stop=False, prunable=True),
})

BLOCKING_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.blocks_stop
)
NON_BLOCKING_TASK_STATUSES = frozenset(STATUS_POLICY) - BLOCKING_TASK_STATUSES
PRUNABLE_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.prunable
)
COMPLETED_TASK_STATUSES = frozenset(
    status for status, policy in STATUS_POLICY.items() if policy.completed
)


def task_status_policy(status: str) -> TaskStatusPolicy:
    """Return semantics for a supported status; reject silent policy gaps."""
    try:
        return STATUS_POLICY[status]
    except KeyError as exc:
        raise ValueError(f"Unknown task status {status!r}") from exc
