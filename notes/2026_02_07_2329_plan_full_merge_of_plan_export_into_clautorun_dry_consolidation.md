---
session_id: 5972dfc0-1209-4441-8d1b-6a48ba9fe265
original_path: /Users/athundt/.claude/plans/modular-greeting-crab.md
export_timestamp: 2026-02-07T23:29:02.129494
export_destination: /Users/athundt/.claude/clautorun/notes/2026-02-07-2329-plan-full-merge-of-plan-export-into-clautorun-dry-consolidation.md
---

# Plan: Full Merge of plan-export into clautorun (DRY Consolidation)

## Context

plan-export is 100% dependent on clautorun for logic, hooks, and state. Maintaining it as a separate plugin creates cross-plugin coupling that caused three bugs, ~500 lines of redundant init/startup code, and makes every plan-export change touch two plugin directories. Merging eliminates these structural problems.

## Bugs Fixed

### Bug 1: Write/Edit Tracking Never Wired Up (fresh context recovery broken)

`track_plan_writes()` at `clautorun/plan_export.py:861` listens for PostToolUse(Write|Edit), but **no hooks.json delivers those events**. Result: `active_plans` is never populated, so SessionStart recovery (the fresh-context workaround documented at plan_export.py:26-36) silently does nothing.

**Fix**: Add `Write|Edit` to clautorun's PostToolUse matcher in hooks.json.

### Bug 2: Hook Double-Firing on ExitPlanMode

Both plugins register PostToolUse(ExitPlanMode):
- `plugins/clautorun/hooks/hooks.json:17-19` → hook_entry.py → daemon
- `plugins/plan-export/hooks/hooks.json:4-14` → plan_export.py → try_daemon → daemon

`export_on_exit_plan_mode()` fires twice per ExitPlanMode event.

**Fix**: Delete plan-export's hooks.json (clautorun handles all events).

### Bug 3: Config Key Mismatch (user settings silently ignored)

`config.py:32` writes `"output_dir"`. `PlanExportConfig:368` expects `"output_plan_dir"`. `hasattr(cls, k)` filter silently drops user's setting, so `/plan-export:dir custom_path` has no effect.

**Fix**: Rename to `output_plan_dir` in config.py + add migration in PlanExportConfig.load().

---

## Implementation

**Git history**: Use `git mv` for all file moves (config.py, commands, tests) so git tracks rename history. Only use `git rm` for files being deleted outright (scripts/plan_export.py, export_plan_module/, obsolete tests).

### Commit 1: Fix hooks.json — wire up Write/Edit tracking + SessionStart

**File: `plugins/clautorun/hooks/hooks.json`**

```diff
  "PostToolUse": [
    {
-     "matcher": "ExitPlanMode",
+     "matcher": "ExitPlanMode|Write|Edit",
      "hooks": [{ "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py", "timeout": 10 }]
    }
  ],
+ "SessionStart": [
+   {
+     "hooks": [{ "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py", "timeout": 10 }]
+   }
+ ],
  "Stop": [
```

This fixes bug 1 (Write/Edit tracking) and enables bug 2 fix (clautorun now handles all plan-export events).

### Commit 2: Fix config key mismatch + migrate config.py into clautorun

**Move** (use `git mv` to preserve history — `mkdir -p plugins/clautorun/scripts/` first since directory doesn't exist):
`plugins/plan-export/scripts/config.py` → `plugins/clautorun/scripts/plan_export_config.py`

**In moved config.py**:
- Rename `"output_dir"` → `"output_plan_dir"` in `DEFAULT_CONFIG` and all 8 PRESETS
- Rename in `set_dir()`, `apply_preset()`, `show_current_settings()`
- Add migration in `load_config()`:
  ```python
  if "output_dir" in user_config and "output_plan_dir" not in user_config:
      user_config["output_plan_dir"] = user_config.pop("output_dir")
  ```
- Add `sys.path` setup for clautorun imports (same pattern as other clautorun scripts)

**In `clautorun/plan_export.py`** — Add same migration to `PlanExportConfig.load()`:
```python
# Migrate legacy key name (config.py used "output_dir" before v0.8)
if "output_dir" in data and "output_plan_dir" not in data:
    data["output_plan_dir"] = data.pop("output_dir")
```

### Commit 3: Move commands into clautorun with long+short names

**Move 9 command .md files** (use `git mv` to preserve history) from `plugins/plan-export/commands/` to `plugins/clautorun/commands/`:

| Original File | Long Name | Short Alias | Command |
|---------------|-----------|-------------|---------|
| `status.md` | `planexport.md` | `pe.md` | `/cr:planexport`, `/cr:pe` |
| `enable.md` | `planexport-enable.md` | `pe-on.md` | `/cr:planexport-enable`, `/cr:pe-on` |
| `disable.md` | `planexport-disable.md` | `pe-off.md` | `/cr:planexport-disable`, `/cr:pe-off` |
| `configure.md` | `planexport-configure.md` | `pe-cfg.md` | `/cr:planexport-configure`, `/cr:pe-cfg` |
| `dir.md` | `planexport-dir.md` | `pe-dir.md` | `/cr:planexport-dir`, `/cr:pe-dir` |
| `pattern.md` | `planexport-pattern.md` | `pe-fmt.md` | `/cr:planexport-pattern`, `/cr:pe-fmt` |
| `preset.md` | `planexport-preset.md` | `pe-preset.md` | `/cr:planexport-preset`, `/cr:pe-preset` |
| `presets.md` | `planexport-presets.md` | `pe-presets.md` | `/cr:planexport-presets`, `/cr:pe-presets` |
| `reset.md` | `planexport-reset.md` | `pe-reset.md` | `/cr:planexport-reset`, `/cr:pe-reset` |

Each command .md file updated:
- `${CLAUDE_PLUGIN_ROOT}/scripts/config.py` → `${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py`
- Short alias files are copies of the long files (same content)

### Commit 4: Remove dead code from clautorun/plan_export.py

Remove 12 module-level functions (~330 lines) that are dead code after plan-export deletion. Verified: none are called by PlanExport class or daemon handlers — they only existed for the fallback script and re-exports.

**Plan discovery functions (dead — superseded by PlanExport class methods):**

| Function | Lines | Why Remove |
|----------|-------|------------|
| `detect_hook_type()` | 212-216 | Dead code, never called by daemon |
| `get_most_recent_plan()` | 255-263 | Superseded by `PlanExport.get_current_plan()` |
| `get_plan_from_transcript()` | 266-305 | Fallback-only transcript parser, never called by daemon |
| `get_plan_from_metadata()` | 308-324 | Only caller is `find_plan_by_session_id()` (also removed) |
| `find_plan_by_session_id()` | 327-334 | Calls `get_plan_from_metadata()`, never called by daemon |

**Tracking wrappers (dead — superseded by `PlanExport.atomic_update_tracking()`):**

| Function | Lines | Why Remove |
|----------|-------|------------|
| `load_tracking()` | 337-340 | Duplicates `PlanExport.tracking` property (line 434) |
| `save_tracking()` | 343-346 | Duplicates `PlanExport.atomic_update_tracking()` (line 441) |
| `record_export()` | 349-361 | Duplicates `PlanExport.export()` hash recording (line 668) |

**Export wrappers (dead — standalone wrappers around PlanExport methods):**

| Function | Lines | Why Remove |
|----------|-------|------------|
| `export_plan()` | 720-756 | Duplicates `PlanExport.export()` |
| `export_rejected_plan()` | 759-795 | Duplicates `PlanExport.export(rejected=True)` |
| `handle_session_start()` | 798-830 | Duplicates `recover_unexported_plans()` daemon handler |
| `embed_plan_metadata()` | 833-856 | Duplicates `PlanExport._embed_metadata()` (line 689) |

**Also remove** (only caller is `record_export()` which is being removed; PlanExport has its own `content_hash()` method at line 465):

| Function | Lines | Why Remove |
|----------|-------|------------|
| `get_content_hash()` | 219-225 | Duplicates `PlanExport.content_hash()` (line 465). Only caller is `record_export()` (also removed) |

**Helper wrappers (dead — config.py has its own independent implementations; never called within clautorun):**

| Function | Lines | Why Remove |
|----------|-------|------------|
| `get_config_path()` | 228-230 | Trivial `return CONFIG_PATH` — config.py has its own at config.py:82 |
| `load_config()` | 233-236 | Wraps `PlanExportConfig.load().to_dict()` — config.py has its own at config.py:87 |
| `is_enabled()` | 239-241 | Wraps `PlanExportConfig.load().enabled` — only imported by plan-export scripts (deleted) |
| `log_warning()` | 244-252 | Never called anywhere — only imported by plan-export scripts (deleted) |

**Total: 17 functions removed, ~420 lines.**

Update `__all__` to remove all 17 deleted names. The remaining module-level code is:
- Constants: `GLOBAL_SESSION_ID`, `DEFAULT_CONFIG`, `CONFIG_PATH`, `PLANS_DIR`, `DEBUG_LOG_PATH`
- Classes: `PlanExportConfig`, `PlanExport`
- Daemon handlers: `track_plan_writes`, `export_on_exit_plan_mode`, `recover_unexported_plans`

### Commit 5: Migrate tests + delete plan-export plugin

**Move tests** (use `git mv` to preserve history) from `plugins/plan-export/tests/` to `plugins/clautorun/tests/`:

| Test File | Action |
|-----------|--------|
| `test_plan_export_class.py` | Move as-is (already imports from `clautorun.plan_export`) |
| `test_race_condition_fix.py` | Move + update imports: `export_plan_module` → `clautorun.plan_export`, `export_plan()` → `PlanExport.export()` |
| `test_stale_lock_recovery.py` | Move + update imports: same pattern |
| `test_same_session_multi_process.py` | Move + update imports: same pattern |
| `test_edge_cases.py` | Move + rewrite: `import plan_export` → `import clautorun.plan_export`. Delete tests for removed functions. |
| `test_session_start_handler.py` | Move + rewrite: test `recover_unexported_plans()` via `PlanExport` class instead of old script functions. |
| `test_bootstrap_fallback.py` | **DELETE** — tests OLD script's `HAS_SESSION_LOCK` pattern |
| `test_tool_response_filepath.py` | **DELETE** — reads OLD script as text to validate AST |

Also move `test_plan_export_hooks.sh` (integration test) and update paths.

**Merge conftest.py**: plan-export's conftest.py adds 3 pytest markers (`slow`, `stress`, `race`) and 2 fixtures (`test_timeout`, `stress_test_timeout`). Merge these into clautorun's existing `conftest.py` (which already has `unique_session_id`, `temp_session_dir`, etc.). The plan-export conftest's `sys.path.insert` lines are unnecessary since clautorun's conftest already adds `src/` to path.

**Delete entire `plugins/plan-export/` directory** including:
- `scripts/plan_export.py` (168 lines — daemon client, fully redundant)
- `scripts/export_plan_module/__init__.py` (99 lines — pure re-exports)
- `scripts/__init__.py` (7 lines)
- `hooks/hooks.json` (28 lines — now empty/redundant)
- `commands/` (9 files — moved to clautorun)
- `.claude-plugin/plugin.json` (11 lines)
- `pyproject.toml` package config
- `README.md`

**Update workspace `pyproject.toml`** at repo root: Remove plan-export from UV workspace members.

**Update `CLAUDE.md`**: Remove plan-export plugin section, add plan export subsection under clautorun commands.

---

## Files Summary

| Action | File | Lines Δ |
|--------|------|---------|
| Edit | `clautorun/hooks/hooks.json` | +6 |
| Edit | `clautorun/plan_export.py` | -420 (remove 17 dead functions) + ~5 (migration) |
| Move+Edit | `config.py` → `clautorun/scripts/plan_export_config.py` | ~15 changed |
| Create | 18 command .md files in `clautorun/commands/` | ~18 × 15 = ~270 (moved, not new logic) |
| Move | 6 test files → `clautorun/tests/` | ~30 lines changed (imports) |
| Merge | plan-export `conftest.py` fixtures into clautorun `conftest.py` | ~10 lines added |
| **DELETE** | `plugins/plan-export/` (entire directory) | **~-1200 lines** |
| **DELETE** | 2 obsolete test files | ~-160 |
| **Net new code** | | **~-1300 lines** |

**Note:** `plugins/clautorun/scripts/` directory does not exist yet. Commit 2 must `mkdir -p plugins/clautorun/scripts/` before `git mv`.

## What's Preserved (Zero Capability Loss)

- All 8 config options (enabled, output_plan_dir, filename_pattern, extension, export_rejected, output_rejected_plan_dir, debug_logging, notify_claude)
- All 10 template variables ({YYYY}, {YY}, {MM}, {DD}, {HH}, {mm}, {date}, {datetime}, {name}, {original})
- All 8 PRESETS (default, plans, docs, dated, yearly, simple, archive, original)
- PlanExport class with atomic operations, cross-session state, content hash dedup
- 3 daemon handlers (track_plan_writes, export_on_exit_plan_mode, recover_unexported_plans)
- Fresh context workaround (SessionStart recovery) — now actually functional with Write/Edit tracking wired up
- Metadata embedding (YAML frontmatter)
- Debug logging
- All tests (migrated, 2 obsolete ones deleted)

## Verification

1. `python3 -c "import json; json.load(open('plugins/clautorun/hooks/hooks.json'))"` — valid JSON
2. `uv run pytest plugins/clautorun/tests/test_plan_export_class.py -v` — 57 tests pass
3. `uv run pytest plugins/clautorun/tests/ -v` — full suite passes
4. `python3 plugins/clautorun/scripts/plan_export_config.py status` — shows correct settings
5. `python3 plugins/clautorun/scripts/plan_export_config.py presets` — lists all 8 presets
6. `bash plugins/clautorun/tests/test_plan_export_hooks.sh` — integration test passes
7. Verify `/cr:pe` and `/cr:planexport` commands work in Claude Code session
8. Manual: Create plan → Option 2 accept → verify export to notes/
9. Manual: Create plan → Option 1 fresh context → verify SessionStart recovery works
