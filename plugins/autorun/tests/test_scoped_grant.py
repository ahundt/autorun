"""Pattern-neutral scope parsing and logical-call claims."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from autorun.scoped_allow import (
    ScopeSpec,
    ScopedAllow,
    ScopedGrant,
    parse_scope_tokens,
)


def test_scope_tokens_use_explicit_default_and_compose_count_with_duration():
    default = ScopeSpec(ttl_seconds=300.0)

    assert parse_scope_tokens([], default_scope=default, count_unit="logical Stop") == default
    assert parse_scope_tokens(
        ["3", "5m"],
        default_scope=default,
        count_unit="logical Stop",
    ) == ScopeSpec(ttl_seconds=300.0, remaining_uses=3)
    assert parse_scope_tokens(
        ["perm"],
        default_scope=default,
        count_unit="logical Stop",
    ).permanent


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (["0"], "logical Stop count must be greater than 0"),
        (["-1"], "scope token must be"),
        (["5m", "10m"], "duration may be provided once"),
        (["2", "3"], "logical Stop count may be provided once"),
        (["perm", "5m"], "perm cannot be combined"),
        (["nonsense"], "scope token must be"),
    ],
)
def test_scope_tokens_reject_ambiguous_or_invalid_values(tokens, message):
    with pytest.raises(ValueError, match=message):
        parse_scope_tokens(
            tokens,
            default_scope=ScopeSpec(ttl_seconds=300.0),
            count_unit="logical Stop",
        )


def test_scope_spec_rejects_non_positive_and_non_finite_values():
    for value in (0, -1, float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="ttl_seconds must be a finite number greater than 0"):
            ScopeSpec(ttl_seconds=value)

    with pytest.raises(ValueError, match="remaining_uses must be an integer greater than 0"):
        ScopeSpec(remaining_uses=True)


def test_claim_once_stamps_every_counted_call_and_replays_without_decrement():
    grant = ScopedGrant(
        granted_at=100.0,
        remaining_uses=5,
        grace_seconds=0.5,
    )

    first = grant.claim_once("stop-a", now=100.0)
    replay = first.grant.claim_once("stop-a", now=100.1)
    second = replay.grant.claim_once("stop-b", now=100.2)

    assert first.allowed and not first.replay
    assert first.grant.remaining_uses == 4
    assert replay.allowed and replay.replay
    assert replay.grant == first.grant
    assert second.allowed and not second.replay
    assert second.grant.remaining_uses == 3


def test_claim_once_is_inactive_on_missing_fingerprint_or_clock_rollback():
    grant = ScopedGrant(granted_at=100.0, remaining_uses=1)

    assert not grant.claim_once("", now=100.0).allowed
    assert not grant.is_active(now=99.0)


def test_parallel_replays_of_first_use_consume_only_one_count():
    barrier = Barrier(8)
    state = {"grant": ScopedGrant(granted_at=100.0, remaining_uses=5)}
    lock = __import__("threading").Lock()

    def claim():
        barrier.wait()
        with lock:
            result = state["grant"].claim_once("same-stop", now=100.0)
            state["grant"] = result.grant
            return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: claim(), range(8)))

    assert sum(result.allowed for result in results) == 8
    assert sum(result.replay for result in results) == 7
    assert state["grant"].remaining_uses == 4


def test_scoped_allow_positional_pattern_and_flat_round_trip_remain_compatible():
    allow = ScopedAllow(
        "git status",
        granted_at=100.0,
        ttl_seconds=30.0,
        remaining_uses=2,
    )

    assert allow.pattern == "git status"
    assert ScopedAllow.from_dict(allow.to_dict()) == allow
