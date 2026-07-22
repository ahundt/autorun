"""Executable documentation contract for the JSON-to-SQLite transition."""

import inspect

from autorun import plan_export
from autorun.session_manager import (
    SessionLock,
    SQLiteStore,
    StateMigrator,
    StateRetention,
    _JSONStore,
    _SQLiteStateProxy,
    _StateProxy,
    _build_store,
    session_state,
)
from autorun.task_lifecycle import TaskLifecycle


def _doc(symbol) -> str:
    return " ".join((inspect.getdoc(symbol) or "").split())


def _assert_contract(symbol, *required: str) -> None:
    doc = _doc(symbol)
    for text in required:
        assert text in doc, f"{symbol.__qualname__} must document {text!r}"


def test_deprecated_session_lock_names_replacements_and_retirement_gate():
    _assert_contract(
        SessionLock,
        "DEPRECATED COMPATIBILITY SHIM",
        "session_state",
        "PlanExport._publication_lock",
        "Remove when",
    )
    assert "SessionLock(" not in inspect.getsource(plan_export.handle_session_start)
    assert "SessionLock" not in plan_export.__dict__, (
        "production plan export must not import the no-op compatibility shim"
    )
    assert "REPLACES: SessionLock" in (plan_export.__doc__ or "")
    _assert_contract(
        plan_export.PlanExport._publication_lock,
        "Replacement for the deprecated no-op",
        "SessionLock",
        "reverse reference",
    )


def test_json_and_sqlite_stores_cross_reference_status_and_retirement():
    _assert_contract(
        _JSONStore,
        "SUPPORTED TRANSITION BACKEND - NOT DEPRECATED",
        "Replacement: SQLiteStore",
        "Retire when",
    )
    _assert_contract(
        SQLiteStore,
        "REPLACEMENT FOR: _JSONStore",
        "_JSONStore remains supported",
        "StateMigrator.rollback",
    )


def test_proxy_lifecycle_shims_and_context_replacement_are_linked_both_ways():
    for proxy in (_StateProxy, _SQLiteStateProxy):
        _assert_contract(proxy.sync, "DEPRECATED NO-OP", "session_state")
        _assert_contract(proxy.close, "DEPRECATED NO-OP", "session_state")
    _assert_contract(
        session_state,
        "REPLACES",
        "_StateProxy.sync",
        "_SQLiteStateProxy.close",
    )


def test_json_gc_and_sqlite_retention_cross_reference_without_false_deprecation():
    _assert_contract(
        TaskLifecycle.cli_gc,
        "JSON-ONLY COMPATIBILITY PATH - NOT DEPRECATED",
        "Replacement for SQLite: StateRetention",
        "Retire when",
    )
    _assert_contract(
        StateRetention,
        "REPLACEMENT FOR SQLITE: TaskLifecycle.cli_gc",
        "TaskLifecycle.cli_gc remains supported for JSON",
    )


def test_migrator_and_backend_selector_cross_reference_their_removal_gate():
    _assert_contract(
        StateMigrator,
        "USED BY: _build_store",
        "Retire when",
    )
    _assert_contract(
        _build_store,
        "USES: StateMigrator",
        "Retire StateMigrator when",
    )


def test_singleton_test_aliases_point_to_the_supported_reset_helper():
    from autorun import session_manager

    _assert_contract(
        session_manager._reset_for_testing,
        "REPLACES direct mutation",
        "_store",
        "_manager",
        "can be removed when",
    )
    source = inspect.getsource(session_manager)
    assert "DEPRECATED TEST ALIAS: use _reset_for_testing() instead of assigning _store" in source
    assert "DEPRECATED TEST ALIAS: use _reset_for_testing() instead of assigning _manager" in source
