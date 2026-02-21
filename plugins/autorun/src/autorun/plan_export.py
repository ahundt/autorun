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
    Workaround for Claude Code bug where Option 1 ("Continue with fresh context"
    button in plan accept dialog) does NOT fire PostToolUse hooks for ExitPlanMode.
    Plans accepted with Option 1 were silently lost because the hook never triggered.

CLAUDE CODE BUG (upstream, not in this code):
    Bug Location: Claude Code's plan acceptance dialog, Option 1 handler
    Bug Behavior: When user accepts a plan with Option 1 ("Continue with fresh context"):
        1. Claude Code clears the conversation context
        2. A NEW session_id is assigned (different from the planning session)
        3. PostToolUse hook for ExitPlanMode is NEVER fired (BUG!)
        4. The plan file exists at ~/.claude/plans/<name>.md but is never exported
        5. Result: Plan is silently lost - no hook, no export, no notification

    This is a bug in Claude Code itself, not in this plugin. This module implements
    a WORKAROUND by tracking plan writes and recovering unexported plans on SessionStart.

WHY "MOST RECENT PLAN" IS WRONG:
    A naive solution would be "just export the most recent plan file". This fails because:
    1. Multiple simultaneous sessions exist (different terminal windows)
    2. Threading issues exist (race conditions between sessions)
    3. Old sessions can be completed AFTER new ones start
    4. The "most recent" plan might belong to a DIFFERENT session/project
    5. Claude Code runs in VS Code, Claude App, and terminals - not just one context

KEY INSIGHT - Plan File Path is the Stable Identifier:
    What DOES persist across Option 1 clears:
    - The terminal window has a continuous lifetime
    - The plan file path (e.g., ~/.claude/plans/foo.md) stays the SAME
    - The working directory (project cwd) stays the SAME
    - Content hash can detect if already exported

    Solution: Track {plan_path → (cwd, session_id)} mappings in a GLOBAL store
    (not session-scoped) that survives the session_id change.

REQUIREMENTS:
    1. Export plans to notes/ with configurable filename pattern
    2. Handle multiple simultaneous sessions (different terminals, VS Code, Claude App)
    3. Survive session clears (Option 1 creates new session_id)
    4. Prevent duplicate exports (content hash tracking)
    5. Preserve all 8 config options
    6. Support template variables: {YYYY}, {MM}, {DD}, {HH}, {mm}, {date}, {datetime}, {name}, {original}
    7. Terminal-agnostic: Works in VS Code extension, Claude App, and CLI terminals

CONFIGURATION (~/.claude/plan-export.config.json):
    enabled                  - Enable/disable plan export (default: true)
    output_plan_dir          - Directory for exported plans (default: "notes")
    filename_pattern         - Filename template (default: "{datetime}_{name}")
    extension                - File extension (default: ".md")
    export_rejected          - Save rejected plans (default: true)
    output_rejected_plan_dir - Directory for rejected plans (default: "notes/rejected")
    debug_logging            - Enable debug logging (default: false)
    notify_claude            - Show export confirmation message (default: true)

TEMPLATE VARIABLES:
    {YYYY}     - 4-digit year (2025)
    {YY}       - 2-digit year (25)
    {MM}       - Month 01-12
    {DD}       - Day 01-31
    {HH}       - Hour 00-23
    {mm}       - Minute 00-59
    {date}     - Full date YYYY_MM_DD
    {datetime} - Full datetime YYYY_MM_DD_HHmm
    {name}     - Extracted plan name from first heading
    {original} - Original plan filename (without .md)

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

STATE PERSISTENCE:
    State is stored in ~/.claude/sessions/plugin___plan_export__.db (shelve format):
    - Uses GLOBAL_SESSION_ID = "__plan_export__" (NOT session-scoped)
    - Survives: daemon restarts, Option 1 session clears, VS Code restarts, reboots
    - Two state dictionaries:
        active_plans: Plans written but not yet exported
            Key: plan file path (e.g., "/Users/x/.claude/plans/foo.md")
            Value: {cwd, session_id, recorded_at}
        tracking: Content hashes of exported plans (prevents duplicates)
            Key: SHA256 hash (first 16 chars)
            Value: {exported_to, exported_at}

LIFECYCLE:
    Short-term (within session):
        Write plan → ThreadSafeDB cache + shelve → survives
        Edit plan → updates cache + shelve → survives
        ExitPlanMode (Option 2) → export immediately → survives

    Long-term (across sessions):
        Daemon restart → shelve persists, cache rebuilds → survives
        Option 1 (fresh context) → NEW session_id, but GLOBAL store survives
        VS Code restart → shelve persists → survives
        Machine reboot → shelve persists → survives

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
from .session_manager import session_state, SessionLock, SessionTimeoutError
from .config import WRITE_TOOLS, EDIT_TOOLS, PLAN_TOOLS

# Global key for cross-session state (survives Option 1 fresh context)
GLOBAL_SESSION_ID = "__plan_export__"

# Default config for compatibility
DEFAULT_CONFIG = {
    "enabled": True,
    "output_plan_dir": "notes",
    "filename_pattern": "{datetime}_{name}",
    "extension": ".md",
    "export_rejected": True,
    "output_rejected_plan_dir": "notes/rejected",
    "debug_logging": False,
    "notify_claude": True,
}

# Config file path
CONFIG_PATH = Path.home() / ".claude" / "plan-export.config.json"
PLANS_DIR = Path.home() / ".claude" / "plans"
DEBUG_LOG_PATH = Path.home() / ".claude" / "plan-export-debug.log"

# Permission modes that indicate the user accepted the plan in the ExitPlanMode dialog.
# Options 1 (bypassPermissions) and 3 (acceptEdits) count as acceptance.
# Used in recover_unexported_plans() to route exit_attempted plans to notes/ vs notes/rejected/.
# "default" and "plan" are NOT plan acceptance modes — they fall through to finalize_backup().
PLAN_ACCEPTED_PERMISSION_MODES = frozenset({"bypassPermissions", "acceptEdits"})


# === Module-level helper functions (DRY - single source of truth) ===



def get_content_hash(file_path) -> str:
    """Get SHA256 hash of file content (first 16 chars)."""
    try:
        content = Path(file_path).read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except IOError:
        return ""


def log_warning(message: str, config: "PlanExportConfig" = None) -> None:
    """Log message to debug log if debug_logging is enabled.

    Args:
        message: Message to log.
        config: Optional pre-loaded config to avoid disk read. If None, loads from disk.
    """
    if config is None:
        config = PlanExportConfig.load()
    if config.debug_logging:
        try:
            with open(DEBUG_LOG_PATH, "a") as f:
                f.write(f"[{datetime.now()}] {message}\n")
        except IOError:
            pass


def get_most_recent_plan() -> Optional[Path]:
    """Find most recent plan file in ~/.claude/plans/."""
    if not PLANS_DIR.exists():
        return None
    plan_files = list(PLANS_DIR.glob("*.md"))
    if not plan_files:
        return None
    plan_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return plan_files[0]


def get_plan_from_transcript(transcript_path: str) -> Optional[Path]:
    """Extract plan file path from session transcript."""
    transcript = Path(transcript_path)
    if not transcript.exists():
        return None

    found_plans = set()
    try:
        with open(transcript, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "assistant":
                        message = entry.get("message", {})
                        content = message.get("content", [])
                        if isinstance(content, list):
                            for item in content:
                                if item.get("type") == "tool_use":
                                    tool_name = item.get("name", "")
                                    if tool_name in (WRITE_TOOLS | EDIT_TOOLS):
                                        tool_input = item.get("input", {})
                                        file_path = tool_input.get("file_path", "")
                                        if file_path and str(PLANS_DIR) in file_path and file_path.endswith(".md"):
                                            found_plans.add(file_path)
                except json.JSONDecodeError:
                    continue
    except IOError:
        return None

    if not found_plans:
        return None

    valid_plans = [Path(p) for p in found_plans if Path(p).exists()]
    if not valid_plans:
        return None

    valid_plans.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return valid_plans[0]


def get_plan_from_metadata(plan_path: Path) -> Optional[str]:
    """Extract session_id from plan file YAML frontmatter."""
    try:
        content = plan_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        frontmatter_end = content.find("\n---", 4)
        if frontmatter_end == -1:
            return None
        frontmatter = content[4:frontmatter_end].strip()
        for line in frontmatter.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                return session_id.strip('"\'')
    except (IOError, UnicodeDecodeError):
        pass
    return None


def find_plan_by_session_id(session_id: str) -> Optional[Path]:
    """Find a plan file by session_id in metadata."""
    if not PLANS_DIR.exists():
        return None
    for plan_path in PLANS_DIR.glob("*.md"):
        if get_plan_from_metadata(plan_path) == session_id:
            return plan_path
    return None


def load_tracking() -> dict:
    """Load export tracking data from session state."""
    with session_state(GLOBAL_SESSION_ID) as state:
        return dict(state.get("tracking", {}))


def save_tracking(tracking: dict) -> None:
    """Save export tracking data to session state."""
    with session_state(GLOBAL_SESSION_ID) as state:
        state["tracking"] = tracking


def record_export(plan_path, dest_path) -> None:
    """Record a successful export to tracking."""
    content_hash = get_content_hash(plan_path)
    if not content_hash:
        return
    with session_state(GLOBAL_SESSION_ID) as state:
        tracking = state.get("tracking", {})
        tracking[content_hash] = {
            "exported_at": datetime.now().isoformat(),
            "destination": str(dest_path),
            "source": str(plan_path),
        }
        state["tracking"] = tracking


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
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                # Migrate legacy key name (config.py used "output_dir" before v0.8)
                if "output_dir" in data and "output_plan_dir" not in data:
                    data["output_plan_dir"] = data.pop("output_dir")
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()

    def to_dict(self) -> dict:
        """Convert config to dict (for backwards compatibility)."""
        return {
            "enabled": self.enabled,
            "output_plan_dir": self.output_plan_dir,
            "filename_pattern": self.filename_pattern,
            "extension": self.extension,
            "export_rejected": self.export_rejected,
            "output_rejected_plan_dir": self.output_rejected_plan_dir,
            "debug_logging": self.debug_logging,
            "notify_claude": self.notify_claude,
        }


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

    # --- Template Expansion (preserves all current variables) ---

    def expand_template(self, template: str, plan_path: Path, plan_name: str) -> str:
        """Expand template variables: {YYYY}, {MM}, {DD}, {HH}, {mm}, {ss}, {date}, {datetime}, {name}, {original}."""
        now = datetime.now()
        replacements = {
            "{YYYY}": now.strftime("%Y"),
            "{YY}": now.strftime("%y"),
            "{MM}": now.strftime("%m"),
            "{DD}": now.strftime("%d"),
            "{HH}": now.strftime("%H"),
            "{mm}": now.strftime("%M"),
            "{ss}": now.strftime("%S"),
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
            log_warning(f"record_write: cwd not available, skipping {file_path}", self.config)
            return

        def updater(plans):
            plans[file_path] = {
                "cwd": project_dir,
                "session_id": self.ctx.session_id,
                "recorded_at": datetime.now().isoformat(),
            }
        self.atomic_update_active_plans(updater)
        log_warning(f"Recorded plan write: {file_path}", self.config)

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

    def get_plan_from_exit_message(self) -> Optional[Path]:
        """Extract plan file path from ExitPlanMode response message.

        ExitPlanMode returns messages like:
        "Your plan has been saved to: /Users/athundt/.claude/plans/foo.md"

        This method parses the tool_result to extract the file path when
        filePath field is missing.
        """
        tool_result = getattr(self.ctx, 'tool_result', None)
        if not tool_result:
            return None

        # Collect string values to search for "saved to:" pattern
        search_fields = []
        if isinstance(tool_result, dict):
            search_fields = list(tool_result.values())
        elif isinstance(tool_result, str):
            # Try to parse as JSON first
            try:
                parsed = json.loads(tool_result)
                if isinstance(parsed, dict):
                    search_fields = list(parsed.values())
                elif isinstance(parsed, list):
                    search_fields = parsed
                else:
                    search_fields = [tool_result]
            except (json.JSONDecodeError, TypeError):
                # Not JSON, treat as plain string
                search_fields = [tool_result]
        else:
            return None

        # Look for "saved to:" pattern in any field.
        # Uses greedy match to handle .md in directory names (e.g. .mdbackup/).
        # path.exists() validates the extracted path.
        for value in search_fields:
            if not isinstance(value, str):
                continue
            if "saved to:" in value.lower():
                match = re.search(r"saved to:\s*([^\n]+\.md)\b", value, re.IGNORECASE)
                if match:
                    path_str = match.group(1).strip()
                    path = Path(path_str)
                    if path.exists():
                        logger.info(f"Extracted plan path from ExitPlanMode message: {path}")
                        return path

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
            # Skip empty plans (no content to export)
            try:
                if not path.read_text(encoding="utf-8").strip():
                    to_remove.append(path_str)
                    continue
            except (IOError, UnicodeDecodeError):
                to_remove.append(path_str)
                continue
            if get_content_hash(path) in tracking:
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

    def _copy_plan_to_dir(self, plan_path: Path, rejected: bool = False) -> Path:
        """Copy plan file to configured output directory with collision handling.

        Must be called INSIDE an active session_state lock (preserves atomicity with
        state updates in the caller). Raises on any file I/O error; caller handles.
        Returns the destination Path after copy + metadata embedding.

        File I/O inside the lock is consistent with export() line comment:
        "inside lock — plan files are small, <1MB".
        """
        dir_key = "output_rejected_plan_dir" if rejected else "output_plan_dir"
        output_dir = getattr(self.config, dir_key)
        useful_name = self.extract_useful_name(plan_path)

        expanded_dir = self.expand_template(output_dir, plan_path, useful_name)
        notes_dir = self.project_dir / expanded_dir
        notes_dir.mkdir(parents=True, exist_ok=True)

        base_filename = self.expand_template(
            self.config.filename_pattern, plan_path, useful_name
        )
        base_filename = self._sanitize_filename(base_filename)
        dest_filename = f"{base_filename}{self.config.extension}"
        dest_path = notes_dir / dest_filename

        counter = 1
        while dest_path.exists():
            dest_filename = f"{base_filename}_{counter}{self.config.extension}"
            dest_path = notes_dir / dest_filename
            counter += 1

        shutil.copy2(plan_path, dest_path)
        embed_plan_metadata(plan_path, self.ctx.session_id, dest_path)
        return dest_path

    def export(self, plan_path: Path, rejected: bool = False, force: bool = False) -> Dict:
        """Export plan to project notes directory.

        Args:
            plan_path: Source plan file
            rejected: Export to rejected directory
            force: Skip content-hash dedup check (for explicit re-export)
        """
        try:
            content_hash = get_content_hash(plan_path)

            # Single session_state open: check dedup + update tracking + update active_plans
            # Collapses the previous triple-open pattern to eliminate 3 lock cycles and
            # 2 race windows between checks and updates.
            with session_state(GLOBAL_SESSION_ID) as state:
                tracking = state.get("tracking", {})
                if not force and content_hash in tracking:
                    prev = tracking[content_hash]
                    return {
                        "success": True,
                        "message": "Already exported (dedup)",
                        "destination": prev.get("exported_to", ""),
                        "skipped": True,
                    }

                # _copy_plan_to_dir called inside lock — same atomicity contract as before
                dest_path = self._copy_plan_to_dir(plan_path, rejected)

                # Atomic update: tracking + active_plans in one write
                dest_str = str(dest_path)
                new_tracking = dict(tracking)
                new_tracking[content_hash] = {
                    "exported_to": dest_str,
                    "exported_at": datetime.now().isoformat(),
                }
                state["tracking"] = new_tracking

                active_plans = dict(state.get("active_plans", {}))
                active_plans.pop(str(plan_path), None)
                state["active_plans"] = active_plans

            rel_path = dest_path.relative_to(self.project_dir)
            log_warning(f"Exported plan to {rel_path}", self.config)
            return {"success": True, "message": f"Plan exported to {rel_path}", "destination": str(dest_path)}
        except Exception as e:
            log_warning(f"Export error: {e}", self.config)
            return {"success": False, "error": str(e)}

    def backup_to_rejected(self, plan_path: Path, permission_mode: str) -> Optional[str]:
        """Back up plan to notes/rejected/ before ExitPlanMode dialog is shown.

        Called at PreToolUse(ExitPlanMode) BEFORE the user sees the acceptance dialog.
        Does NOT update tracking (so get_unexported() still finds this plan at recovery).
        Does NOT remove from active_plans (plan is still pending a decision).

        Records exit_attempted, mode_at_exit_attempt, and backup_path in active_plans so
        recover_unexported_plans() can route correctly:
          - permission_mode changed to accepted mode → promote backup to notes/
          - permission_mode unchanged/unaccepted → finalize_backup() records in tracking

        Skips if config.export_rejected=False (user opted out of notes/rejected/ entirely).
        Returns the backup file path string, or None on failure/skip.
        """
        if not self.config.export_rejected:
            return None
        try:
            with session_state(GLOBAL_SESSION_ID) as state:
                # _copy_plan_to_dir called inside lock — same atomicity contract as export()
                dest_path = self._copy_plan_to_dir(plan_path, rejected=True)
                backup_path_str = str(dest_path)

                # Update active_plans only. Do NOT touch tracking — preserves get_unexported().
                # Upsert: create entry if absent (e.g. after Option 1 recovery removed it)
                # so get_current_plan() can find the plan via active_plans fallback in PostToolUse.
                plans = state.get("active_plans", {})
                plan_key = str(plan_path)
                entry = plans.get(plan_key, {})
                if "cwd" not in entry:
                    entry["cwd"] = str(self.ctx.cwd) if self.ctx.cwd else str(self.project_dir)
                entry["exit_attempted"] = True
                entry["mode_at_exit_attempt"] = permission_mode
                entry["backup_path"] = backup_path_str
                plans[plan_key] = entry
                state["active_plans"] = plans

            rel = dest_path.relative_to(self.project_dir)
            log_warning(f"Backed up plan to {rel} (pending acceptance decision)", self.config)
            return backup_path_str

        except Exception as e:
            log_warning(f"backup_to_rejected error: {e}", self.config)
            return None

    def finalize_backup(self, plan_path: Path) -> Dict:
        """Finalize a plan that was backed up but not accepted (Option 4 / Escape).

        The file already exists in notes/rejected/ (written by backup_to_rejected).
        Records the content hash → backup_path in tracking (prevents future re-recovery),
        then removes from active_plans. No second file is written.
        """
        try:
            content_hash = get_content_hash(plan_path)
            plan_key = str(plan_path)

            with session_state(GLOBAL_SESSION_ID) as state:
                plans = state.get("active_plans", {})
                info = plans.get(plan_key, {})
                backup_path = info.get("backup_path", "")

                tracking = state.get("tracking", {})
                new_tracking = dict(tracking)
                new_tracking[content_hash] = {
                    "exported_to": backup_path,
                    "exported_at": datetime.now().isoformat(),
                }
                state["tracking"] = new_tracking

                new_plans = dict(plans)
                new_plans.pop(plan_key, None)
                state["active_plans"] = new_plans

            # Compute relative path for human-readable message (same pattern as export() lines 753-755)
            if backup_path:
                try:
                    rel = Path(backup_path).relative_to(self.project_dir)
                    msg = f"Plan retained in {rel} (not accepted)"
                except ValueError:
                    msg = "Plan retained in notes/rejected/ (not accepted)"
            else:
                msg = "Plan retained in notes/rejected/ (not accepted)"

            log_warning(f"Finalized rejected plan (already in notes/rejected/): {plan_key}", self.config)
            return {
                "success": True,
                "message": msg,
                "destination": backup_path,
                "skipped": True,
            }
        except Exception as e:
            log_warning(f"finalize_backup error: {e}", self.config)
            return {"success": False, "error": str(e)}



# === Module-level export functions (use after classes are defined) ===


def export_plan(plan_path, project_dir, session_id: str = None) -> dict:
    """Export a plan file to project notes directory.

    Args:
        plan_path: Path to the plan file
        project_dir: Project directory (notes/ will be created here)
        session_id: Optional session ID for metadata

    Returns:
        dict with success, source, destination, message keys
    """
    from .core import ThreadSafeDB
    store = ThreadSafeDB()
    ctx = EventContext(
        session_id=session_id or "unknown",
        event="PostToolUse",
        tool_name=next(iter(PLAN_TOOLS)),
        tool_input={"cwd": str(project_dir)},
        store=store
    )
    config = PlanExportConfig.load()
    exporter = PlanExport(ctx, config)
    result = exporter.export(Path(plan_path), force=True)

    if result["success"]:
        return {
            "success": True,
            "source": str(plan_path),
            "destination": result["destination"],
            "message": result["message"]
        }
    return {
        "success": False,
        "source": str(plan_path),
        "destination": "",
        "message": result.get("error", "Export failed")
    }


def export_rejected_plan(plan_path, project_dir, session_id: str = None) -> dict:
    """Export a rejected plan to project rejected plans directory.

    Args:
        plan_path: Path to the plan file
        project_dir: Project directory
        session_id: Optional session ID for metadata

    Returns:
        dict with success, source, destination, message keys
    """
    from .core import ThreadSafeDB
    store = ThreadSafeDB()
    ctx = EventContext(
        session_id=session_id or "unknown",
        event="PostToolUse",
        tool_name=next(iter(PLAN_TOOLS)),
        tool_input={"cwd": str(project_dir)},
        store=store
    )
    config = PlanExportConfig.load()
    exporter = PlanExport(ctx, config)
    result = exporter.export(Path(plan_path), rejected=True, force=True)

    if result["success"]:
        return {
            "success": True,
            "source": str(plan_path),
            "destination": result["destination"],
            "message": result["message"]
        }
    return {
        "success": False,
        "source": str(plan_path),
        "destination": "",
        "message": result.get("error", "Export failed")
    }


def handle_session_start(hook_input: dict) -> None:
    """Handle SessionStart hook - recover unexported plans.

    CLAUDE CODE BUG WORKAROUND:
    When a user accepts a plan with Option 1 (fresh context), the PostToolUse
    hook for ExitPlanMode doesn't fire, leaving the plan unexported.

    This handler catches unexported plans on the next session start by checking
    for plans tracked via Write/Edit PostToolUse events that have no matching
    export in the tracking data.

    Prints JSON response to stdout for Claude Code.
    """
    from .core import ThreadSafeDB, validate_hook_response
    from .config import detect_cli_type
    
    store = ThreadSafeDB()
    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", str(Path.cwd()))
    cli_type = detect_cli_type(hook_input)

    ctx = EventContext(
        session_id=session_id,
        event="SessionStart",
        tool_input={"cwd": cwd},
        store=store
    )

    config = PlanExportConfig.load()
    if not config.enabled:
        print(json.dumps(validate_hook_response("SessionStart", {"continue": True, "suppressOutput": True}, cli_type=cli_type)))
        return

    if not session_id or session_id == "unknown":
        print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
        return

    transcript_path = hook_input.get("transcript_path")
    if not transcript_path:
        print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
        return

    try:
        lock_context = SessionLock(session_id, timeout=5.0)
    except Exception:
        print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
        return

    try:
        with lock_context:
            exporter = PlanExport(ctx, config)

            # Try transcript-based recovery first
            plan_path = get_plan_from_transcript(transcript_path)
            if not plan_path:
                print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
                return

            # Skip empty plans
            try:
                content = plan_path.read_text(encoding="utf-8")
                if not content.strip():
                    print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
                    return
            except (IOError, UnicodeDecodeError):
                print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
                return

            # Check if already exported (content-hash dedup)
            content_hash = get_content_hash(plan_path)
            tracking = load_tracking()
            if content_hash in tracking:
                print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
                return

            # Export the plan (force=True: dedup already checked above via load_tracking)
            result = exporter.export(plan_path, force=True)
            if result["success"]:
                record_export(plan_path, result.get("destination", ""))
                if config.notify_claude:
                    print(json.dumps(validate_hook_response("SessionStart", {
                        "continue": True,
                        "systemMessage": f"📋 Recovered unexported plan: {result['message']}",
                    }, cli_type=cli_type)))
                    return

            print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))

    except SessionTimeoutError:
        print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))
    except Exception as e:
        log_warning(f"SessionStart handler error: {e}", config)
        print(json.dumps(validate_hook_response("SessionStart", {"continue": True}, cli_type=cli_type)))


def embed_plan_metadata(plan_path, session_id: str, export_destination) -> None:
    """Embed metadata into exported plan file.

    Args:
        plan_path: Original plan file path
        session_id: Session ID to embed
        export_destination: Destination file to add metadata to
    """
    try:
        dest = Path(export_destination)
        content = dest.read_text(encoding="utf-8")
        if content.startswith("---"):
            return  # Already has frontmatter
        metadata = f"""---
session_id: {session_id}
original_path: {plan_path}
export_timestamp: {datetime.now().isoformat()}
export_destination: {export_destination}
---

"""
        dest.write_text(metadata + content, encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        pass


# --- Daemon-Integrated Handlers (Sugar + Error Handling) ---

# PreToolUse backup: PostToolUse is unreliable in some Claude Code sessions.
# Content-hash deduplication prevents double-export if both Pre and Post fire.

@app.on("PreToolUse")
def track_and_export_plans_early(ctx: EventContext) -> Optional[Dict]:
    """Track plan writes; at ExitPlanMode, back up to notes/rejected/ before dialog is shown.

    Design (backup-then-route):
      PreToolUse(ExitPlanMode) fires BEFORE the user sees the dialog.
      backup_to_rejected() copies the plan to notes/rejected/ without touching
      tracking, so get_unexported() still finds it at SessionStart recovery.
      recover_unexported_plans() then routes:
        - exit_attempted + accepted mode → promote to notes/ (Option 1)
        - exit_attempted + not accepted  → finalize_backup() (Option 4 / Escape)
        - not exit_attempted             → export(rejected=True) (abandoned)

    PostToolUse (Options 2/3) still routes to notes/ via export_on_exit_plan_mode().
    Always returns None — never blocks tool execution.
    """
    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None

        # Track Write/Edit to plan files
        if ctx.tool_name in (WRITE_TOOLS | EDIT_TOOLS):
            file_path = ctx.tool_input.get("file_path", "")
            PlanExport(ctx, config).record_write(file_path)

        # Back up to notes/rejected/ BEFORE dialog is shown.
        # backup_to_rejected() skips tracking so get_unexported() can still find this plan
        # at SessionStart recovery for proper accepted/rejected routing.
        # Never send a notification here — the user has not yet made a choice.
        elif ctx.tool_name in PLAN_TOOLS:
            exporter = PlanExport(ctx, config)
            plan = exporter.get_current_plan()
            if plan:
                exporter.backup_to_rejected(plan, ctx.permission_mode)
            # Always return None — never block ExitPlanMode.
    except Exception as e:
        logger.debug(f"PreToolUse plan_export: {e}")
    return None


@app.on("PostToolUse")
def track_plan_writes(ctx: EventContext) -> Optional[Dict]:
    """Track Write/Edit to plan files for fresh context recovery."""
    if ctx.tool_name not in (WRITE_TOOLS | EDIT_TOOLS):
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
    if ctx.tool_name not in PLAN_TOOLS:
        return None
    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None
        exporter = PlanExport(ctx, config)
        plan = exporter.get_current_plan()

        # If plan not found via tool_result or active_plans,
        # try parsing from ExitPlanMode's response message
        if not plan:
            plan = exporter.get_plan_from_exit_message()

        logger.info(
            f"export_on_exit_plan_mode: tool={ctx.tool_name} "
            f"plan={plan} permission_mode={ctx.permission_mode}"
        )

        if plan:
            result = exporter.export(plan)
            logger.info(f"export_on_exit_plan_mode: export result={result}")
            if result["success"] and config.notify_claude:
                if result.get("skipped"):
                    # Plan already exported (dedup). Notify with where it was sent rather
                    # than silently returning nothing — the user needs to know where to find it.
                    dest = result.get("destination", "")
                    if dest:
                        try:
                            rel = Path(dest).relative_to(exporter.project_dir)
                            return ctx.respond("allow", f"📋 Plan exported to {rel}", to_human=True)
                        except ValueError:
                            return ctx.respond("allow", f"📋 Plan exported to {dest}", to_human=True)
                else:
                    return ctx.respond("allow", f"📋 {result['message']}", to_human=True)
    except SessionTimeoutError:
        return ctx.respond("allow", "⚠️ Plan export skipped: lock timeout. Re-trigger ExitPlanMode to retry.", to_human=True)
    except Exception as e:
        logger.error(f"export_on_exit_plan_mode error: {e}")
    return None


@app.on("SessionStart")
def recover_unexported_plans(ctx: EventContext) -> Optional[Dict]:
    """Recover plans from Option 1 (fresh context) on session start.

    CRITICAL: Runs in NEW session after Option 1 clears context.
    Uses GLOBAL_SESSION_ID to read active_plans from OLD session.
    Daemon integration: Shares ThreadSafeDB cache across sessions.

    Three-branch routing based on exit_attempted flag and permission_mode:

    | exit_attempted | permission_mode at recovery | Action |
    |---|---|---|
    | True  | bypassPermissions / acceptEdits | promote backup to notes/ (Option 1) |
    | True  | plan / default / other          | finalize_backup() → stays in notes/rejected/ (Option 4/Escape) |
    | False | any                             | export(rejected=True) → notes/rejected/ (abandoned) |

    Shift+Tab cases:
    - No prior ExitPlanMode: exit_attempted=False → abandoned branch (notes/rejected/)
    - After ExitPlanMode backup, Shift+Tab to default: finalize_backup() (notes/rejected/)
    - After ExitPlanMode backup, Shift+Tab to bypassPermissions: known limitation (false positive
      promotes to notes/; plan appears in both folders — low-severity, documented)

    mode_at_exit_attempt guard: if ctx.permission_mode == mode_at_exit_attempt, the mode
    did NOT change between backup and recovery → not an Option 1 acceptance.
    """
    # Note: ctx.payload doesn't exist - EventContext uses individual properties (core.py:397-419)
    logger.info(f"SessionStart handler called (event: {ctx.event})")

    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None

        exporter = PlanExport(ctx, config)
        recovered_count = 0
        accepted_msgs: list = []
        rejected_msgs: list = []

        # Snapshot active_plans before get_unexported() may clean stale entries.
        # Plans cleaned (file no longer exists) won't appear in the loop anyway.
        active_plans_snapshot = exporter.active_plans

        for plan in exporter.get_unexported():
            info = active_plans_snapshot.get(str(plan), {})
            exit_was_attempted = info.get("exit_attempted", False)
            mode_at_exit = info.get("mode_at_exit_attempt", "")
            # mode_at_exit is always "plan" for genuine ExitPlanMode attempts.
            # If recovery permission_mode matches stored mode, mode did not change
            # (e.g. user opened dialog, dismissed, then same mode at new session)
            # → not an Option 1 acceptance.
            # source="clear" is the primary Option 1 detection signal. Claude Code applies
            # bypassPermissions 2ms AFTER the SessionStart hook completes, so permission_mode
            # is always "default" at hook time for Option 1 sessions (confirmed by debug log).
            # The permission_mode path is retained as a fallback for when Anthropic fixes the
            # timing bug so the correct value arrives in the hook payload.
            session_is_clear = ctx.source == "clear"
            mode_changed = ctx.permission_mode != mode_at_exit
            plan_was_accepted = (exit_was_attempted
                                 and (session_is_clear
                                      or (mode_changed
                                          and ctx.permission_mode in PLAN_ACCEPTED_PERMISSION_MODES)))

            logger.info(
                f"recover_unexported_plans: plan={plan.name} "
                f"exit_was_attempted={exit_was_attempted} "
                f"session_is_clear={session_is_clear} "
                f"permission_mode={ctx.permission_mode} "
                f"mode_at_exit={mode_at_exit!r} "
                f"plan_was_accepted={plan_was_accepted}"
            )

            if plan_was_accepted:
                # Option 1: new session with bypassPermissions/acceptEdits.
                # Backup exists in notes/rejected/ (written by backup_to_rejected).
                # Promote to notes/. force=True bypasses dedup — hash not in tracking yet
                # because backup_to_rejected() skips tracking intentionally.
                result = exporter.export(plan, rejected=False, force=True)
                if result["success"]:
                    recovered_count += 1
                    if result.get("message"):
                        accepted_msgs.append(result["message"])

            elif exit_was_attempted:
                # Option 4 or Escape: dialog was shown, plan was not accepted.
                # Backup already exists in notes/rejected/. Record in tracking, clean active_plans.
                result = exporter.finalize_backup(plan)
                if result["success"]:
                    recovered_count += 1
                    if result.get("message"):
                        rejected_msgs.append(result["message"])

            else:
                # Abandoned: ExitPlanMode was never called before this recovery.
                # Standard export to notes/rejected/ (or notes/ if export_rejected=False).
                result = exporter.export(plan, rejected=config.export_rejected)
                if result["success"]:
                    recovered_count += 1
                    if result.get("message"):
                        rejected_msgs.append(result["message"])

            logger.info(f"recover_unexported_plans: result={result}")

        if recovered_count > 0:
            parts = []
            if accepted_msgs:
                parts.append("Accepted: " + "; ".join(accepted_msgs))
            if rejected_msgs:
                parts.append("Not accepted: " + "; ".join(rejected_msgs))
            msg = f"📋 Recovered {recovered_count} plan(s): " + "; ".join(parts)
            # PATHWAY 4 (SessionStart): ctx.respond() adds required schema fields
            # (stopReason, suppressOutput) and applies schema validation via
            # validate_hook_response(). systemMessage is always human-visible for SessionStart.
            return ctx.respond("allow", msg)

    except SessionTimeoutError as e:
        logger.warning(f"SessionStart plan recovery timeout: {e}")
        return None  # Lock timeout, skip silently
    except Exception as e:
        logger.error(f"recover_unexported_plans error: {e}", exc_info=True)

    return None





# === Public API ===

__all__ = [
    # Classes
    "PlanExport",
    "PlanExportConfig",
    # Constants
    "GLOBAL_SESSION_ID",
    "PLAN_ACCEPTED_PERMISSION_MODES",
    "DEFAULT_CONFIG",
    "CONFIG_PATH",
    "PLANS_DIR",
    "DEBUG_LOG_PATH",
    # Utility functions
    "get_content_hash",
    "log_warning",
    "get_most_recent_plan",
    "get_plan_from_transcript",
    "get_plan_from_metadata",
    "find_plan_by_session_id",
    "load_tracking",
    "save_tracking",
    "record_export",
    # Export functions
    "export_plan",
    "export_rejected_plan",
    "handle_session_start",
    "embed_plan_metadata",
    # Daemon handlers (for registration)
    "track_plan_writes",
    "export_on_exit_plan_mode",
    "recover_unexported_plans",
]
