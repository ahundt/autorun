"""
Regression tests for EventContext.cwd propagation.

Root cause of plan export regression (2026-02-18):
  client.py:197 injects _cwd into hook payload.
  core.py:handle_client() used _cwd for resolve_session_key() but did NOT pass it
  to EventContext constructor, so ctx.cwd always returned None.
  plan_export.py:record_write() and project_dir() both need ctx.cwd to work.

Fix: Add cwd parameter to EventContext.__init__() and pass payload.get("_cwd")
     from handle_client() (core.py:ClautorunDaemon.handle_client()).

This prevented plan files from being tracked in active_plans, causing
export_on_exit_plan_mode() to fail to find the plan.

Debug evidence from ~/.claude/plan-export-debug.log:
  [2026-02-18 20:06:26] record_write: cwd not available, skipping dazzling-foraging-gray.md
  (repeated 40+ times during this session)
"""
from clautorun.core import EventContext


class TestEventContextCwd:
    """Verify cwd is correctly propagated to EventContext."""

    def _make_ctx(self, cwd=None, tool_input=None):
        return EventContext(
            session_id="test-session",
            event="PostToolUse",
            cwd=cwd,
            tool_input=tool_input or {}
        )

    def test_cwd_is_none_by_default(self):
        """EventContext.cwd returns None when not provided (backward compatible)."""
        ctx = EventContext(session_id="test", event="PostToolUse")
        assert ctx.cwd is None

    def test_cwd_set_from_constructor(self):
        """EventContext.cwd returns value passed to constructor."""
        ctx = self._make_ctx(cwd="/Users/test/myproject")
        assert ctx.cwd == "/Users/test/myproject"

    def test_cwd_empty_string(self):
        """EventContext.cwd can be empty string (no project dir available)."""
        ctx = self._make_ctx(cwd="")
        assert ctx.cwd == ""

    def test_cwd_not_in_magic_state(self):
        """EventContext.cwd must not be persisted to magic state (Shelve)."""
        ctx = self._make_ctx(cwd="/some/path")
        # cwd is in __slots__ as _cwd, not in _DEFAULTS, so magic state won't intercept
        assert "cwd" not in ctx._DEFAULTS
        assert "_cwd" in ctx.__slots__

    def test_plan_export_project_dir_uses_cwd(self):
        """PlanExport.project_dir() works when EventContext has cwd set."""
        from pathlib import Path
        from unittest.mock import MagicMock
        from clautorun.plan_export import PlanExport, PlanExportConfig

        ctx = self._make_ctx(cwd="/Users/test/myproject")
        config = MagicMock(spec=PlanExportConfig)
        exporter = PlanExport(ctx, config)

        # Should NOT raise ValueError now that cwd is available
        result = exporter.project_dir
        assert result == Path("/Users/test/myproject")

    def test_plan_export_project_dir_raises_without_cwd(self):
        """PlanExport.project_dir() raises ValueError when ctx.cwd is None."""
        from clautorun.plan_export import PlanExport, PlanExportConfig
        from unittest.mock import MagicMock

        ctx = self._make_ctx(cwd=None)
        config = MagicMock(spec=PlanExportConfig)
        exporter = PlanExport(ctx, config)

        # Should raise ValueError (expected when cwd genuinely unavailable)
        try:
            _ = exporter.project_dir
            raise AssertionError("Expected ValueError but got no exception")
        except ValueError as e:
            assert "cwd not available" in str(e)

    def test_record_write_skips_when_cwd_none(self):
        """record_write() skips plan tracking when ctx.cwd is None.

        This is safe behavior — the plan won't be tracked in active_plans, but
        get_current_plan() will still try tool_result.filePath and
        get_plan_from_exit_message() as fallbacks.
        """
        from clautorun.plan_export import PlanExport, PlanExportConfig
        from unittest.mock import MagicMock

        ctx = self._make_ctx(cwd=None)
        config = MagicMock(spec=PlanExportConfig)
        exporter = PlanExport(ctx, config)

        # Should NOT raise — silently skips
        exporter.record_write("/Users/test/.claude/plans/test-plan.md")
        # active_plans should be empty (nothing tracked)
        assert exporter.active_plans == {}
