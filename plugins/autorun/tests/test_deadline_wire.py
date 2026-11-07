"""The hook deadline crosses processes and languages as epoch seconds.

An absolute ``time.monotonic()`` instant is only meaningful to a reader on
the same clock, and Node's ``process.hrtime`` is not Python's on macOS: libuv
reads ``mach_continuous_time`` (counts sleep), Python ``mach_absolute_time``
(does not), so the two drift apart by every sleep since boot. Every process on
one host shares the wall clock, so the wire carries that and each receiver
converts to its own monotonic clock on arrival, as gRPC does at each hop.
"""

from __future__ import annotations

import math
import time

import pytest

from autorun.config import MAX_PLAUSIBLE_WRAPPER_SECONDS, monotonic_deadline


def test_a_future_epoch_deadline_becomes_the_same_remaining_monotonic_time():
    before = time.monotonic()
    deadline = monotonic_deadline(time.time() + 2.0)
    assert deadline is not None
    assert 1.9 < deadline - before <= 2.0 + (time.monotonic() - before)


def test_a_past_epoch_deadline_is_already_expired():
    deadline = monotonic_deadline(time.time() - 5.0)
    assert deadline is not None and deadline < time.monotonic()


@pytest.mark.parametrize("raw", [None, "", "soon", float("nan"), float("inf"), -1.0, [], {}])
def test_an_unusable_value_is_no_deadline(raw):
    assert monotonic_deadline(raw) is None


def test_a_deadline_too_far_ahead_to_be_ours_is_ignored():
    """A monotonic instant from an older client reads as an epoch in 1970,
    and a stale or foreign value can be far in the future; neither may set
    the budget."""
    assert monotonic_deadline(time.time() + MAX_PLAUSIBLE_WRAPPER_SECONDS + 60) is None


def test_a_string_from_an_environment_variable_is_accepted():
    deadline = monotonic_deadline(repr(time.time() + 1.0))
    assert deadline is not None and math.isfinite(deadline)
