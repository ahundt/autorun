---
session_id: 5972dfc0-1209-4441-8d1b-6a48ba9fe265
original_path: /Users/athundt/.claude/plans/modular-greeting-crab.md
export_timestamp: 2026-02-07T23:51:32.997155
export_destination: /Users/athundt/.claude/clautorun/notes/2026-02-07-2351-plan-full-merge-of-plan-export-into-clautorun-dry-consolidation.md
---

# Plan: Full Merge of plan-export into clautorun (DRY Consolidation)

## Context

plan-export is 100% dependent on clautorun for logic, hooks, and state. Maintaining it as a separate plugin creates cross-plugin coupling that caused four bugs, ~500 lines of redundant init/startup code, and makes every plan-export change touch two plugin directories. Merging eliminates these structural problems.

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

### Bug 4: Config Default filename_pattern Wrong (missing time component)

`config.py` DEFAULT_CONFIG and all 8 PRESETS use `"{date}_{name}"` which produces `YYYY_MM_DD_name.md`. But `PlanExportConfig:369` and `plan_export.py:195` both use `"{datetime}_{name}"` which produces `YYYY_MM_DD_HHmm_name.md`. The correct default includes the time component. config.py's wrong default means `/plan-export:reset` and `/plan-export:preset default` would set the wrong pattern.

**Fix**: Change config.py DEFAULT_CONFIG and PRESETS from `{date}_{name}` to `{datetime}_{name}`. Update preset descriptions to show `HHmm` in examples.

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
- Fix Bug 4: Change `DEFAULT_CONFIG` filename_pattern from `"{date}_{name}"` → `"{datetime}_{name}"` to match PlanExportConfig (includes HHmm)
- Remove entire `PRESETS` dict and `list_presets()`, `apply_preset()` functions — unused complexity
- Rename in `set_dir()`, `apply_preset()`, `show_current_settings()`
- Add migration in `load_config()`:
  ```python
  if "output_dir" in user_config and "output_plan_dir" not in user_config:
      user_config["output_plan_dir"] = user_config.pop("output_dir")
  ```
- Add `sys.path` setup for clautorun imports (same pattern as other clautorun scripts)
- Add `rejected-toggle` and `rejected-dir <path>` subcommands to `main()` dispatch, writing `export_rejected` (bool) and `output_rejected_plan_dir` (str) to the config JSON

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
| `preset.md` | **DELETE** | — | Removed: preset system deleted |
| `presets.md` | **DELETE** | — | Removed: preset system deleted |
| `reset.md` | `planexport-reset.md` | `pe-reset.md` | `/cr:planexport-reset`, `/cr:pe-reset` |

**New commands** (rejected plan config — no existing command to move):

| Long Name | Short Alias | Command | Description |
|-----------|-------------|---------|-------------|
| `planexport-rejected.md` | `pe-rej.md` | `/cr:planexport-rejected`, `/cr:pe-rej` | Toggle rejected plan export on/off |
| `planexport-rejected-dir.md` | `pe-rdir.md` | `/cr:planexport-rejected-dir`, `/cr:pe-rdir` | Set rejected plan output directory (default: `notes/rejected`) |

These commands call `plan_export_config.py rejected-toggle` and `plan_export_config.py rejected-dir <path>`, which need to be added to the config script's `main()` dispatch.

Each command .md file updated:
- `${CLAUDE_PLUGIN_ROOT}/scripts/config.py` → `${CLAUDE_PLUGIN_ROOT}/scripts/plan_export_config.py`
- Short alias files are copies of the long files (same content)

### Commit 4: Remove dead code from clautorun/plan_export.py

Remove dead module-level functions after plan-export deletion. Verified: none are called by PlanExport class or daemon handlers — they only existed for the fallback script and re-exports.

**Remove — dead code:**

| Function | Lines | Why Remove |
|----------|-------|------------|
| `detect_hook_type()` | 212-216 | Dead code, never called by daemon |

**Keep — data recovery utilities** (not used by daemon but useful for manual data recovery):

| Function | Lines | Why Keep |
|----------|-------|----------|
| `get_most_recent_plan()` | 255-263 | Find most recent plan file by mtime |
| `get_plan_from_transcript()` | 266-305 | Extract plan path from session transcript JSONL |
| `get_plan_from_metadata()` | 308-324 | Read session_id from plan YAML frontmatter |
| `find_plan_by_session_id()` | 327-334 | Find plan file matching a session_id |

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

**Total: 13 functions removed, ~340 lines. 4 data recovery utilities kept.**

Update `__all__` to remove 13 deleted names. The remaining module-level code is:
- Constants: `GLOBAL_SESSION_ID`, `DEFAULT_CONFIG`, `CONFIG_PATH`, `PLANS_DIR`, `DEBUG_LOG_PATH`
- Data recovery utilities: `get_most_recent_plan`, `get_plan_from_transcript`, `get_plan_from_metadata`, `find_plan_by_session_id`
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
| `test_edge_cases.py` | Move + rewrite: `import plan_export` → `import clautorun.plan_export`. Delete tests for removed functions, keep tests for retained recovery utilities. |
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

All paths relative to `plugins/`. `scripts/` dir under `clautorun/` must be created first (`mkdir -p`).

### Edited in place (clautorun)

| File | Lines | Action | Commit | Net Δ |
|------|-------|--------|--------|-------|
| `clautorun/hooks/hooks.json` | 38 | Add Write\|Edit to PostToolUse, add SessionStart section | 1 | +6 |
| `clautorun/src/clautorun/plan_export.py` | 959 | Remove 13 dead functions, keep 4 recovery utils, add migration | 4 | ~-335 |
| `clautorun/tests/conftest.py` | 222 | Merge 3 markers + 2 fixtures from plan-export conftest | 5 | +10 |
| `../pyproject.toml` (repo root) | 89 | Remove plan-export from workspace members/sources/deps | 5 | -4 |
| `../CLAUDE.md` (repo root) | 237 | Remove plan-export section, add plan export under clautorun | 5 | ~-10 |

### Moved via `git mv` (plan-export → clautorun)

| Source | Destination | Lines | Edit needed | Commit |
|--------|-------------|-------|-------------|--------|
| `plan-export/scripts/config.py` | `clautorun/scripts/plan_export_config.py` | 267 | Rename key `output_dir`→`output_plan_dir`, fix `{date}`→`{datetime}`, remove presets, add migration + rejected commands | 2 |
| `plan-export/commands/status.md` | `clautorun/commands/planexport.md` + copy to `pe.md` | 10 | Update script path | 3 |
| `plan-export/commands/enable.md` | `clautorun/commands/planexport-enable.md` + copy to `pe-on.md` | 13 | Update script path | 3 |
| `plan-export/commands/disable.md` | `clautorun/commands/planexport-disable.md` + copy to `pe-off.md` | 13 | Update script path | 3 |
| `plan-export/commands/configure.md` | `clautorun/commands/planexport-configure.md` + copy to `pe-cfg.md` | 31 | Update script path, remove preset references | 3 |
| `plan-export/commands/dir.md` | `clautorun/commands/planexport-dir.md` + copy to `pe-dir.md` | 9 | Update script path | 3 |
| `plan-export/commands/pattern.md` | `clautorun/commands/planexport-pattern.md` + copy to `pe-fmt.md` | 9 | Update script path | 3 |
| `plan-export/commands/reset.md` | `clautorun/commands/planexport-reset.md` + copy to `pe-reset.md` | 8 | Update script path | 3 |
| `plan-export/tests/test_plan_export_class.py` | `clautorun/tests/test_plan_export_class.py` | 1035 | None (already imports from `clautorun.plan_export`) | 5 |
| `plan-export/tests/test_race_condition_fix.py` | `clautorun/tests/test_race_condition_fix.py` | 929 | Update imports: `export_plan_module` → `clautorun.plan_export` | 5 |
| `plan-export/tests/test_stale_lock_recovery.py` | `clautorun/tests/test_stale_lock_recovery.py` | 564 | Update imports: same pattern | 5 |
| `plan-export/tests/test_same_session_multi_process.py` | `clautorun/tests/test_same_session_multi_process.py` | 499 | Update imports: same pattern | 5 |
| `plan-export/tests/test_edge_cases.py` | `clautorun/tests/test_edge_cases.py` | 848 | Rewrite imports, delete tests for removed functions, keep recovery util tests | 5 |
| `plan-export/tests/test_session_start_handler.py` | `clautorun/tests/test_session_start_handler.py` | 566 | Rewrite to test via PlanExport class | 5 |
| `plan-export/tests/test_plan_export_hooks.sh` | `clautorun/tests/test_plan_export_hooks.sh` | 649 | Update paths | 5 |

### New files (created, not moved)

| File | Lines | Purpose | Commit |
|------|-------|---------|--------|
| `clautorun/commands/pe.md` | 10 | Short alias copy of `planexport.md` | 3 |
| `clautorun/commands/pe-on.md` | 13 | Short alias copy of `planexport-enable.md` | 3 |
| `clautorun/commands/pe-off.md` | 13 | Short alias copy of `planexport-disable.md` | 3 |
| `clautorun/commands/pe-cfg.md` | 31 | Short alias copy of `planexport-configure.md` | 3 |
| `clautorun/commands/pe-dir.md` | 9 | Short alias copy of `planexport-dir.md` | 3 |
| `clautorun/commands/pe-fmt.md` | 9 | Short alias copy of `planexport-pattern.md` | 3 |
| `clautorun/commands/pe-reset.md` | 8 | Short alias copy of `planexport-reset.md` | 3 |
| `clautorun/commands/planexport-rejected.md` + `pe-rej.md` | ~10 | Toggle rejected plan export | 3 |
| `clautorun/commands/planexport-rejected-dir.md` + `pe-rdir.md` | ~10 | Set rejected plan output directory | 3 |

### Deleted (not moved)

| File | Lines | Why | Commit |
|------|-------|-----|--------|
| `plan-export/commands/preset.md` | 9 | Preset system removed | 3 |
| `plan-export/commands/presets.md` | 11 | Preset system removed | 3 |
| `plan-export/scripts/plan_export.py` | 168 | Daemon client, fully redundant with clautorun hook_entry.py | 5 |
| `plan-export/scripts/export_plan_module/__init__.py` | 99 | Pure re-exports from clautorun.plan_export | 5 |
| `plan-export/scripts/__init__.py` | 6 | Empty package init | 5 |
| `plan-export/hooks/hooks.json` | 28 | Causes double-firing (Bug 2), clautorun handles all events | 5 |
| `plan-export/.claude-plugin/plugin.json` | 11 | Plugin manifest for deleted plugin | 5 |
| `plan-export/pyproject.toml` | 123 | Package config for deleted plugin | 5 |
| `plan-export/README.md` | 146 | Documentation for deleted plugin | 5 |
| `plan-export/pytest.ini` | 39 | Test config for deleted plugin | 5 |
| `plan-export/tests/test_bootstrap_fallback.py` | 231 | Tests old script's `HAS_SESSION_LOCK` pattern (obsolete) | 5 |
| `plan-export/tests/test_tool_response_filepath.py` | 583 | Reads old script as text to validate AST (obsolete) | 5 |
| `plan-export/tests/conftest.py` | 38 | Merged into clautorun conftest.py | 5 |
| `plan-export/tests/__init__.py` | 6 | Empty package init | 5 |
| `plan-export/tests/README.md` | 212 | Test documentation | 5 |
| `plan-export/tests/requirements.txt` | 9 | Test requirements (covered by workspace deps) | 5 |
| `plan-export/notes/` | 226 | Exported plan (not source code) | 5 |
| `plan-export/**/__pycache__/` | ~4200 | Bytecode cache (not tracked in git) | 5 |
| `plan-export/.pytest_cache/` | ~295 | Pytest cache (not tracked in git) | 5 |

### Directory changes

| Directory | Before | After |
|-----------|--------|-------|
| `plugins/plan-export/` | 50+ files | **Deleted entirely** |
| `plugins/clautorun/scripts/` | Does not exist | Created, contains `plan_export_config.py` |
| `plugins/clautorun/commands/` | 50+ existing commands | +16 new plan export commands (7 long + 7 short + 2 rejected pairs) |
| `plugins/clautorun/tests/` | Existing clautorun tests | +7 migrated plan export tests (6 .py + 1 .sh) |

## What's Preserved (Zero Capability Loss)

- All 8 config options (enabled, output_plan_dir, filename_pattern, extension, export_rejected, output_rejected_plan_dir, debug_logging, notify_claude)
- All 10 template variables ({YYYY}, {YY}, {MM}, {DD}, {HH}, {mm}, {date}, {datetime}, {name}, {original})
- Configurable output dirs for accepted plans (`notes/`) and rejected plans (`notes/rejected/`)
- Configurable filename pattern via `/cr:pe-fmt` — supports all template variables: `{YYYY}`, `{YY}`, `{MM}`, `{DD}`, `{HH}`, `{mm}`, `{date}`, `{datetime}`, `{name}`, `{original}`
- Configurable output dir via `/cr:pe-dir` — also supports template variables (e.g., `notes/{YYYY}/{MM}`)
- **Removed**: Preset menu (8 preconfigured combos) — the underlying template variable support is fully retained
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
5. `python3 plugins/clautorun/scripts/plan_export_config.py rejected-dir notes/rejected` — sets rejected plan dir
6. `bash plugins/clautorun/tests/test_plan_export_hooks.sh` — integration test passes
7. Verify `/cr:pe` and `/cr:planexport` commands work in Claude Code session
8. Manual: Create plan → Option 2 accept → verify export to notes/
9. Manual: Create plan → Option 1 fresh context → verify SessionStart recovery works
