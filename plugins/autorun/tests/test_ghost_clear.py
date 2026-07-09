"""Ghost-task / stale-ref workaround tests (v0.10.2).

Covers: counter reset/increment, injection augmentation, marker regex,
handler idempotence, end-to-end loop, disabled-feature, multi-session
isolation, CI regex-leak check, FeatureToggle-vs-ConfigFlag wiring,
config persistence, ctx override precedence, marker template integrity.

Follows fixture conventions from test_stop_chain_task_lifecycle.py and
test_task_lifecycle_ghost_task_bug.py.
"""
from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest

from autorun.config import CONFIG
from autorun import task_lifecycle as tl
from autorun import plugins as plg
from autorun.core import EventContext, ThreadSafeDB


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _sid(tmp_path) -> str:
    """Unique session ID derived from tmp_path so tests don't share session state."""
    return f"ghost-{tmp_path.name}"


def _isolated_cfg(tmp_path, monkeypatch) -> tl.TaskLifecycleConfig:
    """Patch CONFIG_PATH and return a config whose storage_dir is tmp_path-isolated.

    Both monkeypatches are needed so that hooks creating TaskLifecycle(ctx=ctx)
    via TaskLifecycleConfig.load() use the same isolated storage as the test mgr.
    """
    monkeypatch.setattr(tl, "CONFIG_PATH", tmp_path / "task-lifecycle.config.json")
    cfg = tl.TaskLifecycleConfig(storage_dir=tmp_path / "task-tracking")
    cfg.save()
    return cfg


def _task_cfg(tmp_path, **overrides) -> tl.TaskLifecycleConfig:
    return tl.TaskLifecycleConfig(storage_dir=tmp_path / "task-tracking", **overrides)


def _saved_mgr(tmp_path, monkeypatch, **overrides) -> tl.TaskLifecycle:
    """Create a manager whose config is also visible to hook-created managers."""
    monkeypatch.setattr(tl, "CONFIG_PATH", tmp_path / "task-lifecycle.config.json")
    cfg = _task_cfg(tmp_path, **overrides)
    cfg.save()
    return tl.TaskLifecycle(session_id=_sid(tmp_path), config=cfg)


def _make_mgr(tmp_path, **overrides) -> tl.TaskLifecycle:
    """Return a TaskLifecycle with isolated storage and unique session ID."""
    cfg = _task_cfg(tmp_path, **overrides)
    return tl.TaskLifecycle(session_id=_sid(tmp_path), config=cfg)


def _add_task(mgr: tl.TaskLifecycle, tid: str, subject: str, status: str = "in_progress") -> None:
    def seed(tasks):
        tasks[str(tid)] = {
            "id": str(tid), "subject": subject, "status": status,
            "created_at": time.time(), "updated_at": time.time(),
            "tool_outputs": [], "metadata": {},
        }
    mgr.atomic_update_tasks(seed)


def _marker(task_id: str | int) -> str:
    """Format the configured stale-clear marker without duplicating its literal."""
    return CONFIG["ghost_clear_marker_template"].format(id=task_id)


def _ctx_for_session(
    session_id: str,
    event: str = "Stop",
    tool_result: str = "",
    transcript_text: str = "",
) -> EventContext:
    """Create an EventContext for a specific session id."""
    session_transcript = []
    if transcript_text:
        session_transcript = [{"role": "assistant", "content": transcript_text}]

    ctx = EventContext(
        session_id=session_id,
        event=event,
        prompt="",
        tool_name="",
        tool_input={},
        tool_result=tool_result,
        session_transcript=session_transcript,
        store=ThreadSafeDB(),
    )
    return ctx


def _make_ctx(tmp_path, event: str = "Stop", tool_result: str = "", transcript_text: str = "") -> EventContext:
    """Create an EventContext with unique session ID and isolated store."""
    return _ctx_for_session(_sid(tmp_path), event, tool_result, transcript_text)


def _make_stop_ctx(tmp_path, tool_result: str = "", transcript_text: str = "") -> EventContext:
    return _make_ctx(tmp_path, event="Stop", tool_result=tool_result, transcript_text=transcript_text)


def _arm_stale_clear(mgr: tl.TaskLifecycle, times: int = 2) -> None:
    """Arm stale clear by producing repeated identical Stop blocks."""
    for _ in range(times):
        assert mgr.handle_stop(_ctx_for_session(mgr.session_id)) is not None


# ─── Section 1: counter increments on identical id-set ────────────────────────

def test_counter_increments_on_identical_id_set(tmp_path):
    mgr = _make_mgr(tmp_path)
    _add_task(mgr, "72", "Ghost #72")
    _add_task(mgr, "74", "Ghost #74")
    ctx = _make_stop_ctx(tmp_path)

    mgr.handle_stop(ctx)
    assert mgr.session_metadata.get("consecutive_identical_stop_block_count") == 1

    mgr.handle_stop(ctx)
    assert mgr.session_metadata.get("consecutive_identical_stop_block_count") == 2


# ─── Section 2: counter resets on id-set change ───────────────────────────────

def test_counter_resets_on_id_set_change(tmp_path):
    mgr = _make_mgr(tmp_path)
    _add_task(mgr, "72", "Ghost #72")
    ctx = _make_stop_ctx(tmp_path)

    mgr.handle_stop(ctx)
    first_hash = mgr.session_metadata.get("last_stop_block_id_hash")
    assert first_hash is not None

    _add_task(mgr, "99", "Different task")
    mgr.handle_stop(ctx)

    assert mgr.session_metadata.get("consecutive_identical_stop_block_count") == 1
    assert mgr.session_metadata.get("last_stop_block_id_hash") != first_hash


# ─── Section 3: counter resets on PostToolUse activity ───────────────────────

def test_counter_resets_on_posttooluse(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    mgr = _make_mgr(tmp_path)
    _add_task(mgr, "72", "Ghost #72")
    ctx = _make_stop_ctx(tmp_path)

    mgr.handle_stop(ctx)
    assert mgr.session_metadata.get("consecutive_identical_stop_block_count") == 1

    ctx.event = "PostToolUse"
    ctx.tool_name = "Read"
    plg.reset_ghost_counter_on_activity(ctx)

    assert mgr.session_metadata.get("consecutive_identical_stop_block_count", 0) == 0
    assert "last_stop_block_id_hash" not in mgr.session_metadata


# ─── Section 4: injection unchanged at count == 1 ─────────────────────────────

def test_injection_unchanged_at_count_one(tmp_path):
    """At count=1 the active STALE-TASK ESCAPE HATCH block is NOT appended,
    but the AI-escape *hint* (mentioning the marker) IS in the base injection
    so the AI can discover the path on block #1. Compare with
    test_injection_augmented_at_threshold which checks the active hatch.
    """
    mgr = _make_mgr(tmp_path)
    _add_task(mgr, "72", "Ghost #72")
    ctx = _make_stop_ctx(tmp_path)

    result = mgr.handle_stop(ctx)
    assert result is not None
    assert result.get("continue") is True

    msg = result.get("systemMessage", "")
    # The active hatch (heading + "this same set of ids has blocked Stop")
    # must NOT fire at count=1 — anti-abuse: only after min_consecutive blocks.
    assert "STALE-TASK ESCAPE HATCH" not in msg
    assert "this same set of ids has blocked Stop" not in msg
    # The hint that the AI-callable marker exists IS expected on block #1
    # (added for the "infinite non-overridable stop failure" bug fix). The
    # literal AUTORUN_TASKS_CLEAR_STALE_TASK appearing in the message is
    # explicitly desired here — see _ACT_STALE_AI_ESCAPE in task_lifecycle.py.
    assert "AUTORUN_TASKS_CLEAR_STALE_TASK" in msg, (
        "Block #1 must hint at the AI-callable stale-clear marker so the AI "
        "knows an override path exists. See test_first_stop_block_mentions_"
        "stale_task_ai_escape_path."
    )
    assert "CANNOT STOP" in msg
    assert "Actions: 1." in msg


# ─── Section 5: injection augmented at count == min_consecutive ──────────────

def test_injection_augmented_at_threshold(tmp_path):
    mgr = _make_mgr(tmp_path, ghost_clear_min_consecutive_blocks=2)
    _add_task(mgr, "72", "Ghost #72")
    _add_task(mgr, "74", "Ghost #74")
    ctx = _make_stop_ctx(tmp_path)

    mgr.handle_stop(ctx)          # count=1 — no escape hatch
    result = mgr.handle_stop(ctx) # count=2 — threshold met

    assert result is not None
    msg = result.get("systemMessage", "")
    assert "CANNOT STOP" in msg
    assert "STALE-TASK ESCAPE HATCH" in msg
    assert "AUTORUN_TASKS_CLEAR_STALE_TASK(72)" in msg
    assert "AUTORUN_TASKS_CLEAR_STALE_TASK(74)" in msg


# ─── Section 6: marker regex positive cases ───────────────────────────────────

@pytest.mark.parametrize("sample,expected", [
    ("AUTORUN_TASKS_CLEAR_STALE_TASK(72)", "72"),
    ("noise AUTORUN_TASKS_CLEAR_STALE_TASK(1) noise", "1"),
    ("AUTORUN_TASKS_CLEAR_STALE_TASK(99999)", "99999"),
    ("AUTORUN_TASKS_CLEAR_STALE_TASK(plan-1)", "plan-1"),
    ("AUTORUN_TASKS_CLEAR_STALE_TASK(task.alpha_2)", "task.alpha_2"),
])
def test_regex_positive(sample, expected):
    match = plg._ghost_marker_regex().search(sample)
    assert match is not None
    assert match.group(1) == expected


# ─── Section 7: marker regex negative cases ───────────────────────────────────

@pytest.mark.parametrize("sample", [
    "AUTORUN_TASKS_CLEAR_STALE_TASK",
    "AUTORUN_TASKS_CLEAR_STALE_TASK()",
    "AUTORUN_TASKS_CLEAR_STALE_TASK(72",
    "AUTORUN_TASKS_CLEAR_STALE_TASK(plan/1)",
])
def test_regex_negative(sample):
    assert plg._ghost_marker_regex().search(sample) is None


# ─── Section 8: handler idempotence ───────────────────────────────────────────

def test_handler_idempotent(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    mgr = _make_mgr(tmp_path)
    _add_task(mgr, "72", "Ghost #72")
    _arm_stale_clear(mgr)

    ctx = _make_ctx(tmp_path, event="PostToolUse", tool_result=_marker("72"))
    plg.clear_ghost_tasks(ctx)
    assert mgr.tasks.get("72", {}).get("status") == "ignored"

    # Second call — no-op, still ignored
    plg.clear_ghost_tasks(ctx)
    assert mgr.tasks.get("72", {}).get("status") == "ignored"


def test_posttooluse_marker_before_threshold_does_not_clear(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    mgr = _make_mgr(tmp_path, ghost_clear_min_consecutive_blocks=2)
    _add_task(mgr, "72", "Real in-progress task")

    ctx = _make_ctx(tmp_path, event="PostToolUse", tool_result=_marker("72"))
    plg.clear_ghost_tasks(ctx)

    assert mgr.tasks.get("72", {}).get("status") == "in_progress"


# ─── Section 9: handler logs GHOST_CLEAR audit event ─────────────────────────

def test_handler_logs_ghost_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "CONFIG_PATH", tmp_path / "task-lifecycle.config.json")
    cfg = tl.TaskLifecycleConfig(storage_dir=tmp_path / "task-tracking", debug_logging=True)
    cfg.save()
    mgr = tl.TaskLifecycle(session_id=_sid(tmp_path), config=cfg)
    _add_task(mgr, "72", "Ghost #72")
    _arm_stale_clear(mgr)

    ctx = _make_ctx(tmp_path, event="PostToolUse", tool_result=_marker("72"))
    plg.clear_ghost_tasks(ctx)

    log_path = cfg.storage_dir / _sid(tmp_path) / "audit.log"
    assert log_path.exists(), "Audit log must be created (debug_logging=True required)"
    log_text = log_path.read_text(encoding="utf-8")
    assert "GHOST_CLEAR" in log_text
    assert "task#72" in log_text


# ─── Section 10: full loop (Stop→Stop→marker→Stop allows) ────────────────────

def test_full_loop(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    mgr = _make_mgr(tmp_path, ghost_clear_min_consecutive_blocks=2)
    _add_task(mgr, "72", "Ghost")
    _add_task(mgr, "74", "Ghost")
    ctx = _make_stop_ctx(tmp_path)

    mgr.handle_stop(ctx)
    mgr.handle_stop(ctx)  # count==2, injection augmented

    ctx2 = _make_ctx(
        tmp_path, event="PostToolUse",
        tool_result="AUTORUN_TASKS_CLEAR_STALE_TASK(72)\nAUTORUN_TASKS_CLEAR_STALE_TASK(74)",
    )
    plg.clear_ghost_tasks(ctx2)

    assert mgr.tasks.get("72", {}).get("status") == "ignored"
    assert mgr.tasks.get("74", {}).get("status") == "ignored"

    ctx3 = _make_stop_ctx(tmp_path)
    assert mgr.handle_stop(ctx3) is None  # allow stop


def test_stop_marker_clears_after_threshold_without_tool_call(tmp_path, monkeypatch):
    """Assistant-text marker must work on the next Stop, not only PostToolUse.

    Regression source: when the AI prints the stale-clear marker as text and no
    tool call follows, Claude triggers Stop again. A PostToolUse-only marker
    scanner misses that path and the session stays blocked forever.
    """
    mgr = _saved_mgr(tmp_path, monkeypatch, ghost_clear_min_consecutive_blocks=2)
    _add_task(mgr, "72", "Ghost")

    _arm_stale_clear(mgr)

    stop_ctx = _make_stop_ctx(tmp_path, transcript_text=_marker("72"))

    assert mgr.handle_stop(stop_ctx) is None
    assert mgr.tasks["72"]["status"] == "ignored"


def test_stop_marker_before_threshold_does_not_clear(tmp_path, monkeypatch):
    """The AI-callable marker is armed by repeated identical Stop blocks."""
    mgr = _saved_mgr(tmp_path, monkeypatch, ghost_clear_min_consecutive_blocks=3)
    _add_task(mgr, "72", "Real in-progress task")

    assert mgr.handle_stop(_make_stop_ctx(tmp_path)) is not None

    early_stop_ctx = _make_stop_ctx(tmp_path, transcript_text=_marker("72"))
    result = mgr.handle_stop(early_stop_ctx)

    assert result is not None
    assert "CANNOT STOP" in result.get("systemMessage", "")
    assert mgr.tasks["72"]["status"] == "in_progress"


def test_stop_marker_clears_only_current_session_task(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    storage = tmp_path / "task-tracking"
    cfg = tl.TaskLifecycleConfig(storage_dir=storage, ghost_clear_min_consecutive_blocks=2)
    cfg.save()
    mgr_a = tl.TaskLifecycle(session_id=f"{_sid(tmp_path)}-A", config=cfg)
    mgr_b = tl.TaskLifecycle(session_id=f"{_sid(tmp_path)}-B", config=cfg)
    _add_task(mgr_a, "72", "A stale task")
    _add_task(mgr_b, "72", "B real task")

    _arm_stale_clear(mgr_a)

    clear_ctx = _ctx_for_session(mgr_a.session_id, transcript_text=_marker("72"))
    assert mgr_a.handle_stop(clear_ctx) is None

    assert mgr_a.tasks["72"]["status"] == "ignored"
    assert mgr_b.tasks["72"]["status"] == "in_progress"


def test_stop_marker_partial_clear_still_blocks_remaining_tasks(tmp_path, monkeypatch):
    mgr = _saved_mgr(tmp_path, monkeypatch, ghost_clear_min_consecutive_blocks=2)
    _add_task(mgr, "72", "Stale task")
    _add_task(mgr, "99", "Real unfinished task")

    _arm_stale_clear(mgr)

    result = mgr.handle_stop(_make_stop_ctx(tmp_path, transcript_text=_marker("72")))

    assert result is not None
    msg = result.get("systemMessage", "")
    assert "CANNOT STOP" in msg
    assert "#72" not in msg
    assert "#99" in msg
    assert mgr.tasks["72"]["status"] == "ignored"
    assert mgr.tasks["99"]["status"] == "in_progress"


# ─── Section 11: disabled feature suppresses injection ────────────────────────

def test_disabled_feature_suppresses_injection(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    mgr = _make_mgr(tmp_path, ghost_clear_enabled=False, ghost_clear_min_consecutive_blocks=2)
    _add_task(mgr, "72", "Ghost")
    ctx = _make_stop_ctx(tmp_path)

    mgr.handle_stop(ctx)
    result = mgr.handle_stop(ctx)

    assert result is not None
    msg = result.get("systemMessage", "")
    assert "STALE-TASK ESCAPE HATCH" not in msg


# ─── Section 12: multi-session isolation ─────────────────────────────────────

def test_multi_session_isolation(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    storage = tmp_path / "task-tracking"
    sid_a = f"{_sid(tmp_path)}-A"
    sid_b = f"{_sid(tmp_path)}-B"
    cfg_a = tl.TaskLifecycleConfig(storage_dir=storage)
    cfg_b = tl.TaskLifecycleConfig(storage_dir=storage)

    mgr_a = tl.TaskLifecycle(session_id=sid_a, config=cfg_a)
    mgr_b = tl.TaskLifecycle(session_id=sid_b, config=cfg_b)

    _add_task(mgr_a, "72", "A-ghost")
    ctx_a = EventContext(
        session_id=sid_a, event="Stop", prompt="", tool_name="",
        tool_input={}, tool_result="", session_transcript=[], store=ThreadSafeDB(),
    )
    mgr_a.handle_stop(ctx_a)

    assert mgr_b.session_metadata.get("consecutive_identical_stop_block_count", 0) == 0


# ─── Section 13: single-source-of-truth CI guard ─────────────────────────────

def test_marker_literal_single_source_of_truth():
    """AUTORUN_TASKS_CLEAR_STALE_TASK must not appear in non-comment code outside config.py."""
    import ast
    src = Path(__file__).resolve().parents[1] / "src" / "autorun"
    marker = "AUTORUN_TASKS_CLEAR_STALE_TASK"
    offenders = []
    for py in src.rglob("*.py"):
        if py.name == "config.py":
            continue
        text = py.read_text(encoding="utf-8")
        if marker not in text:
            continue
        # Parse AST and check only non-docstring string literals
        try:
            tree = ast.parse(text)
        except SyntaxError:
            offenders.append(str(py.relative_to(src.parent)) + " (parse error)")
            continue
        # Collect all docstring nodes (module/class/function first Expr(Constant))
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)):
                    docstrings.add(id(node.body[0].value))
        found = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and marker in node.value):
                found = True
                break
        if found:
            offenders.append(str(py.relative_to(src.parent)))
    assert offenders == [], f"Marker literal in string constant outside config.py: {offenders}"


# ─── Section 14: ghost_clear_enabled wired through TaskLifecycleConfig ────────

def test_enabled_flag_read_from_config_not_dict():
    """clear_ghost_tasks must read ghost_clear_enabled from manager.config, not CONFIG dict."""
    src = inspect.getsource(plg.clear_ghost_tasks)
    assert "manager.config.ghost_clear_enabled" in src
    assert 'CONFIG["ghost_clear_enabled"]' not in src
    assert "CONFIG.get(\"ghost_clear_enabled\"" not in src


# ─── Section 15: config persistence round-trip ───────────────────────────────

def test_config_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "CONFIG_PATH", tmp_path / "cfg.json")
    cfg = tl.TaskLifecycleConfig(
        ghost_clear_enabled=False,
        ghost_clear_min_consecutive_blocks=5,
        ghost_clear_hash_length=16,
    )
    cfg.save()
    loaded = tl.TaskLifecycleConfig.load()
    assert loaded.ghost_clear_enabled is False
    assert loaded.ghost_clear_min_consecutive_blocks == 5
    assert loaded.ghost_clear_hash_length == 16


# ─── Section 16: ctx override precedence ─────────────────────────────────────

def test_ctx_override_precedence(tmp_path):
    mgr = _make_mgr(tmp_path, ghost_clear_min_consecutive_blocks=2)
    _add_task(mgr, "72", "Ghost")
    ctx = _make_stop_ctx(tmp_path)
    ctx.ghost_clear_min_consecutive_blocks_override = 3

    mgr.handle_stop(ctx)  # count=1
    r2 = mgr.handle_stop(ctx)  # count=2 — below override threshold of 3
    assert r2 is not None
    assert "STALE-TASK ESCAPE HATCH" not in r2.get("systemMessage", "")

    r3 = mgr.handle_stop(ctx)  # count=3 — at override threshold
    assert r3 is not None
    assert "STALE-TASK ESCAPE HATCH" in r3.get("systemMessage", "")


# ─── Section 17: marker template integrity ───────────────────────────────────

def test_marker_template_integrity():
    t = CONFIG["ghost_clear_marker_template"]
    assert t.count("{id}") == 1
    assert t.startswith("AUTORUN_")
    assert t.format(id=42) == "AUTORUN_TASKS_CLEAR_STALE_TASK(42)"
    prefix, suffix = t.split("{id}")
    assert suffix == ")"
    assert prefix.endswith("(")


def test_task_ignore_aliases_route_to_one_handler():
    canonical = plg.app._find_command("/ar:task-ignore 72", "claude")
    legacy = plg.app._find_command("/task-ignore 72", "claude")
    codex_colon = plg.app._find_command("ar:task-ignore 72", "codex")
    codex_space = plg.app._find_command("ar task-ignore 72", "codex")

    matches = [canonical, legacy, codex_colon, codex_space]
    assert all(match is not None for match in matches)
    assert {match.handler for match in matches if match is not None} == {plg.handle_task_ignore}
    assert {match.alias for match in matches if match is not None} == {"/ar:task-ignore", "/task-ignore"}


def test_plain_task_ignore_alias_uses_task_lifecycle_state(tmp_path, monkeypatch):
    _isolated_cfg(tmp_path, monkeypatch)
    mgr = _make_mgr(tmp_path)
    _add_task(mgr, "72", "Stale task")
    ctx = EventContext(
        session_id=_sid(tmp_path),
        event="UserPromptSubmit",
        prompt="/task-ignore 72 user confirmed stale",
        store=ThreadSafeDB(),
        cli_type="claude",
    )

    result = plg.app.dispatch(ctx)

    assert "Ignored task 72" in result.get("systemMessage", "")
    assert mgr.tasks["72"]["status"] == "ignored"
