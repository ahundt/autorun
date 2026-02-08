#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Plan export integration for clautorun - fixes fresh context bug.

PURPOSE:
    Export Claude Code plan files to project notes/ directory on plan acceptance.
    Workaround for Claude Code bug where Option 1 ("fresh context") does NOT
    fire PostToolUse hooks for ExitPlanMode.

REQUIREMENTS:
    1. Export plans to notes/ with configurable filename pattern
    2. Handle multiple simultaneous sessions (different terminals, VS Code, Claude App)
    3. Survive session clears (Option 1 creates new session_id)
    4. Prevent duplicate exports (content hash tracking)
    5. Preserve all 8 config options from original plan_export.py
    6. Support template variables: {YYYY}, {MM}, {DD}, {HH}, {mm}, {date}, {datetime}, {name}, {original}

GOALS:
    - DRY: Reuse clautorun's session_state() for thread-safe persistence
    - Clean API: PlanExport class encapsulates all state and logic
    - No redundant locking: clautorun's SessionLock is sufficient

GOTCHAS:
    1. EventContext state is scoped to session_id. On Option 1, NEW session has
       DIFFERENT session_id, so state doesn't transfer. Solution: Use GLOBAL_SESSION_ID.

    2. TOCTOU race condition: `x = self.active_plans; x[k] = v; self.active_plans = x`
       is NOT atomic. Another process could modify between read and write.
       Solution: Use atomic_update_*() methods that operate within single session_state().

    3. project_dir fallback: If ctx.cwd is None and we use Path.cwd(), we get the
       daemon's cwd, not the hook's cwd. Solution: Raise ValueError if cwd unavailable.

    4. Unicode dashes: Em dash (—), en dash (–), etc. must be normalized to ASCII
       dash before filename sanitization, or they end up in the filename.

    5. tool_result may be string or dict: ExitPlanMode sometimes returns JSON string.
       Solution: Try json.loads() if tool_result is string.

DESIGN:
    Three hook events handled:
    1. PostToolUse(Write/Edit) - Track plan file writes to active_plans
    2. PostToolUse(ExitPlanMode) - Export immediately (Option 2 - regular accept)
    3. SessionStart - Recover unexported plans (Option 1 - fresh context workaround)

    State stored in GLOBAL_SESSION_ID shelve (not session-scoped):
    - active_plans: {plan_path: {cwd, session_id, recorded_at}}
    - tracking: {content_hash: {exported_to, exported_at}}

THREAD SAFETY & MULTIPROCESS CONCURRENCY:
    - All state access goes through session_state(GLOBAL_SESSION_ID)
    - session_state() uses SessionLock with fcntl.flock() for cross-process exclusion
    - SessionLock supports reentrant locking (same thread can acquire multiple times)
    - atomic_update_*() methods ensure read-modify-write is atomic
    - shelve.sync() is called on context exit for durability
    - No additional FileLock needed - clautorun's SessionLock is sufficient

HOOK FLOW:
    PLAN MODE:
      Write to ~/.claude/plans/foo.md
           ↓
      PostToolUse(Write) → record_write() → atomic_update_active_plans()
           ↓
      shelve[__plan_export__:active_plans][foo.md] = {cwd, session_id}

    OPTION 1 (fresh context - BUG WORKAROUND):
      ExitPlanMode NOT fired (Claude Code bug)
           ↓
      NEW session starts (different session_id)
           ↓
      SessionStart → get_unexported() reads from GLOBAL shelve
           ↓
      Finds foo.md, exports, clears from active_plans

    OPTION 2 (regular accept - NORMAL):
      ExitPlanMode PostToolUse fired
           ↓
      export() → atomic_update_tracking() + atomic_update_active_plans()
           ↓
      SessionStart: finds hash in tracking → skips (no duplicate)
"""
from typing import Optional, Dict, List
from pathlib import Path
from dataclasses import dataclass, field
import hashlib
import shutil
import re
import json
import logging
from datetime import datetime

from .core import EventContext, app, logger
from .session_manager import session_state, SessionTimeoutError

# Global key for cross-session state (survives Option 1 fresh context)
GLOBAL_SESSION_ID = "__plan_export__"


@dataclass
class PlanExportConfig:
    """Configuration with all current plan_export.py capabilities."""
    enabled: bool = True
    output_plan_dir: str = "notes"
    filename_pattern: str = "{datetime}_{name}"
    extension: str = ".md"
    export_rejected: bool = True
    output_rejected_plan_dir: str = "notes/rejected"
    debug_logging: bool = False
    notify_claude: bool = True

    @classmethod
    def load(cls) -> "PlanExportConfig":
        """Load from ~/.claude/plan-export.config.json with defaults."""
        config_path = Path.home() / ".claude" / "plan-export.config.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()


@dataclass
class PlanExport:
    """Manages plan export state with cross-session persistence.

    Uses GLOBAL_SESSION_ID for state that must survive Option 1 (fresh context).
    Reuses clautorun's session_state() for thread-safe, file-locked access.
    No redundant FileLock - clautorun's SessionLock handles concurrency.
    """
    ctx: EventContext
    config: PlanExportConfig = field(default_factory=PlanExportConfig.load)

    # --- Cross-Session State (uses GLOBAL key, not session-scoped) ---
    #
    # NOTE: Use atomic_update_* methods to avoid TOCTOU race conditions.
    # The pattern `x = self.active_plans; x[k] = v; self.active_plans = x`
    # is NOT atomic - another process could modify between read and write.

    @property
    def active_plans(self) -> dict:
        """Plans written but not yet exported. Survives session clears.
        WARNING: For modifications, use atomic_update_active_plans() instead.
        """
        with session_state(GLOBAL_SESSION_ID) as state:
            return dict(state.get("active_plans", {}))  # Return copy

    def atomic_update_active_plans(self, updater) -> None:
        """Atomically update active_plans. updater(plans) modifies in-place."""
        with session_state(GLOBAL_SESSION_ID) as state:
            plans = state.get("active_plans", {})
            updater(plans)
            state["active_plans"] = plans

    @property
    def tracking(self) -> dict:
        """Content hashes of exported plans. Prevents duplicates.
        WARNING: For modifications, use atomic_update_tracking() instead.
        """
        with session_state(GLOBAL_SESSION_ID) as state:
            return dict(state.get("tracking", {}))  # Return copy

    def atomic_update_tracking(self, updater) -> None:
        """Atomically update tracking. updater(tracking) modifies in-place."""
        with session_state(GLOBAL_SESSION_ID) as state:
            tracking = state.get("tracking", {})
            updater(tracking)
            state["tracking"] = tracking

    @property
    def project_dir(self) -> Path:
        """Get project directory from context. Never falls back to daemon's cwd."""
        cwd = getattr(self.ctx, 'cwd', None)
        if cwd is None:
            # Try to get from tool_input (hook input includes cwd)
            cwd = self.ctx.tool_input.get('cwd') if hasattr(self.ctx, 'tool_input') else None
        if cwd is None:
            raise ValueError("project_dir: cwd not available in context")
        return Path(cwd)

    # --- Plan File Detection ---

    def is_plan_file(self, path: str) -> bool:
        """Check if path is a Claude plan file."""
        return "/.claude/plans/" in path and path.endswith(".md")

    def content_hash(self, path: Path) -> str:
        """SHA256 hash of plan content (first 16 chars)."""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except IOError:
            return ""

    # --- Template Expansion (preserves all current variables) ---

    def expand_template(self, template: str, plan_path: Path, plan_name: str) -> str:
        """Expand template variables: {YYYY}, {MM}, {DD}, {HH}, {mm}, {date}, {datetime}, {name}, {original}."""
        now = datetime.now()
        replacements = {
            "{YYYY}": now.strftime("%Y"),
            "{YY}": now.strftime("%y"),
            "{MM}": now.strftime("%m"),
            "{DD}": now.strftime("%d"),
            "{HH}": now.strftime("%H"),
            "{mm}": now.strftime("%M"),
            "{date}": now.strftime("%Y_%m_%d"),
            "{datetime}": now.strftime("%Y_%m_%d_%H%M"),
            "{name}": plan_name,
            "{original}": plan_path.stem,
        }
        result = template
        for var, value in replacements.items():
            result = result.replace(var, value)
        return result

    def extract_useful_name(self, plan_path: Path) -> str:
        """Extract name from first heading or filename."""
        try:
            content = plan_path.read_text(encoding="utf-8")
            for line in content.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    name = re.sub(r"^#+\s*", "", line)
                    return self._sanitize_filename(name)
                return self._sanitize_filename(line)
        except IOError:
            pass
        return plan_path.stem

    def _sanitize_filename(self, name: str) -> str:
        """Convert string to safe filename component.

        Handles Unicode dashes (em dash, en dash, etc.) and other
        problematic Unicode characters that could end up in filenames.
        """
        # Step 1: Normalize Unicode dashes to ASCII dash
        # Covers: em dash (—), en dash (–), minus (−), figure dash (‒),
        # horizontal bar (―), hyphen (‐), non-breaking hyphen (‑)
        UNICODE_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u2E3A\u2E3B"
        for dash in UNICODE_DASHES:
            name = name.replace(dash, "-")

        # Step 2: Normalize Unicode quotes to nothing (remove them)
        UNICODE_QUOTES = "\u2018\u2019\u201C\u201D\u00AB\u00BB"  # '', "", «»
        for quote in UNICODE_QUOTES:
            name = name.replace(quote, "")

        # Step 3: Remove unsafe ASCII characters
        name = re.sub(r'[<>:"/\\|?*&@#$%^!`~\[\]{}();\']+', "", name)

        # Step 4: Count separators to determine majority preference
        underscore_count = name.count('_')
        dash_count = name.count('-')

        if underscore_count >= dash_count:
            # Prefer underscores - convert spaces, dots, commas, dashes
            name = re.sub(r"[\s.,-]+", "_", name)
            name = re.sub(r"_+", "_", name).strip("_")
        else:
            # Prefer dashes - convert spaces, underscores, dots, commas
            name = re.sub(r"[\s_.,]+", "-", name)
            name = re.sub(r"-+", "-", name).strip("-")

        return name.lower()

    # --- State Management ---

    def record_write(self, file_path: str) -> None:
        """Record a plan file write for later recovery. ATOMIC."""
        if not self.is_plan_file(file_path):
            return
        try:
            project_dir = str(self.project_dir)
        except ValueError:
            self._log(f"record_write: cwd not available, skipping {file_path}")
            return

        def updater(plans):
            plans[file_path] = {
                "cwd": project_dir,
                "session_id": self.ctx.session_id,
                "recorded_at": datetime.now().isoformat(),
            }
        self.atomic_update_active_plans(updater)
        self._log(f"Recorded plan write: {file_path}")

    def get_current_plan(self) -> Optional[Path]:
        """Get current plan file from tool_result or active_plans."""
        # Try tool_result.filePath first (ExitPlanMode provides this)
        tool_result = getattr(self.ctx, 'tool_result', None)
        if isinstance(tool_result, dict):
            file_path = tool_result.get("filePath")
            if file_path and Path(file_path).exists():
                return Path(file_path)
        elif isinstance(tool_result, str):
            # Sometimes tool_result is JSON string
            try:
                parsed = json.loads(tool_result)
                if isinstance(parsed, dict):
                    file_path = parsed.get("filePath")
                    if file_path and Path(file_path).exists():
                        return Path(file_path)
            except (json.JSONDecodeError, TypeError):
                pass

        # Fall back to active plans for this project
        try:
            project_dir = str(self.project_dir)
        except ValueError:
            return None
        for path, info in self.active_plans.items():
            if info.get("cwd") == project_dir and Path(path).exists():
                return Path(path)
        return None

    def get_unexported(self) -> List[Path]:
        """Get unexported plans for current project. ATOMIC cleanup."""
        try:
            project_dir = str(self.project_dir)
        except ValueError:
            return []

        result = []
        tracking = self.tracking
        to_remove = []

        # Read-only pass: identify unexported plans and stale entries
        for path_str, info in self.active_plans.items():
            if info.get("cwd") != project_dir:
                continue
            path = Path(path_str)
            if not path.exists():
                to_remove.append(path_str)
                continue
            if self.content_hash(path) in tracking:
                to_remove.append(path_str)
                continue
            result.append(path)

        # Atomic cleanup of stale entries
        if to_remove:
            def cleanup(plans):
                for path_str in to_remove:
                    plans.pop(path_str, None)
            self.atomic_update_active_plans(cleanup)

        return result

    # --- Export Logic ---

    def export(self, plan_path: Path, rejected: bool = False) -> Dict:
        """Export plan to project notes directory."""
        try:
            dir_key = "output_rejected_plan_dir" if rejected else "output_plan_dir"
            output_dir = getattr(self.config, dir_key)
            useful_name = self.extract_useful_name(plan_path)

            # Expand templates in directory path
            expanded_dir = self.expand_template(output_dir, plan_path, useful_name)
            notes_dir = self.project_dir / expanded_dir
            notes_dir.mkdir(parents=True, exist_ok=True)

            # Expand template in filename
            base_filename = self.expand_template(
                self.config.filename_pattern, plan_path, useful_name
            )
            base_filename = self._sanitize_filename(base_filename)
            dest_filename = f"{base_filename}{self.config.extension}"
            dest_path = notes_dir / dest_filename

            # Handle collision
            counter = 1
            while dest_path.exists():
                dest_filename = f"{base_filename}_{counter}{self.config.extension}"
                dest_path = notes_dir / dest_filename
                counter += 1

            # Copy file
            shutil.copy2(plan_path, dest_path)

            # Embed metadata
            self._embed_metadata(plan_path, dest_path)

            # Record hash to prevent duplicates (ATOMIC)
            content_hash = self.content_hash(plan_path)
            dest_str = str(dest_path)

            def record_hash(tracking):
                tracking[content_hash] = {
                    "exported_to": dest_str,
                    "exported_at": datetime.now().isoformat(),
                }
            self.atomic_update_tracking(record_hash)

            # Clear from active (ATOMIC)
            plan_path_str = str(plan_path)

            def remove_plan(plans):
                plans.pop(plan_path_str, None)
            self.atomic_update_active_plans(remove_plan)

            rel_path = dest_path.relative_to(self.project_dir)
            self._log(f"Exported plan to {rel_path}")
            return {"success": True, "message": f"Plan exported to {rel_path}"}
        except Exception as e:
            self._log(f"Export error: {e}")
            return {"success": False, "error": str(e)}

    def _embed_metadata(self, source: Path, dest: Path) -> None:
        """Add YAML frontmatter with session_id, timestamps."""
        try:
            content = dest.read_text(encoding="utf-8")
            if content.startswith("---"):
                return  # Already has frontmatter
            metadata = f"""---
session_id: {self.ctx.session_id}
original_path: {source}
export_timestamp: {datetime.now().isoformat()}
---

"""
            dest.write_text(metadata + content, encoding="utf-8")
        except IOError:
            pass

    def _log(self, message: str) -> None:
        """Debug logging if enabled."""
        if self.config.debug_logging:
            try:
                log_file = Path.home() / ".claude" / "plan-export-debug.log"
                with open(log_file, "a") as f:
                    f.write(f"[{datetime.now()}] {message}\n")
            except IOError:
                pass


# --- Daemon-Integrated Handlers (Sugar + Error Handling) ---

@app.on("PostToolUse")
def track_plan_writes(ctx: EventContext) -> Optional[Dict]:
    """Track Write/Edit to plan files for fresh context recovery."""
    if ctx.tool_name not in ("Write", "Edit"):
        return None
    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None
        PlanExport(ctx, config).record_write(ctx.tool_input.get("file_path", ""))
    except SessionTimeoutError:
        pass  # Lock timeout - skip gracefully
    except Exception as e:
        logger.error(f"track_plan_writes error: {e}")
    return None


@app.on("PostToolUse")
def export_on_exit_plan_mode(ctx: EventContext) -> Optional[Dict]:
    """Export plan when ExitPlanMode fires (Option 2 - regular accept)."""
    if ctx.tool_name != "ExitPlanMode":
        return None
    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None
        exporter = PlanExport(ctx, config)
        plan = exporter.get_current_plan()
        if plan:
            result = exporter.export(plan)
            if result["success"] and config.notify_claude:
                return ctx.respond("allow", f"📋 {result['message']}")
    except SessionTimeoutError:
        return ctx.respond("allow", "Export skipped: lock timeout")
    except Exception as e:
        logger.error(f"export_on_exit_plan_mode error: {e}")
    return None


@app.on("SessionStart")
def recover_unexported_plans(ctx: EventContext) -> Optional[Dict]:
    """Recover plans from Option 1 (fresh context) on session start.

    CRITICAL: Runs in NEW session after Option 1 clears context.
    Uses GLOBAL_SESSION_ID to read active_plans from OLD session.
    Daemon integration: Shares ThreadSafeDB cache across sessions.
    """
    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None
        exporter = PlanExport(ctx, config)
        for plan in exporter.get_unexported():
            result = exporter.export(plan)
            if result["success"] and config.notify_claude:
                return ctx.respond("allow", f"📋 Recovered: {result['message']} (from fresh context)")
    except SessionTimeoutError:
        return None  # Lock timeout, skip silently
    except Exception as e:
        logger.error(f"recover_unexported_plans error: {e}")
    return None
