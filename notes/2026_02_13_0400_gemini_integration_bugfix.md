# Gemini Integration Bugfix & Exploration Notes

**Date:** 2026-02-13 04:00
**Objective:** Resolve "invisible" commands/skills in Gemini CLI, fix hook failures, and integrate AIX as a first-class management system while preserving meaningful filenames.

## 1. Current State Analysis

### 1.1 Discovery Bugs
- **Invisible Skills:** Gemini strictly requires `skills/<dir>/SKILL.md`. Clautorun has bare `.md` files in `skills/`, which are ignored.
- **Broken Commands:** Symlinks in `~/.gemini/extensions/cr/commands/` point to a deleted `/tmp` directory because `install.py` used a temporary copy for installation.
- **Manifest Drift:** `gemini-extension.json` and `.claude-plugin/plugin.json` must be manually synced, leading to missing features on one platform or the other.

### 1.2 Hook & Performance Issues
- **Timeout Mismatch:** Gemini enforces a **5s timeout**; `hook_entry.py` assumes **9s**.
- **PID Confusion:** `client.py` tracks the wrong PID (`hook_entry.py` instead of the Gemini session), causing the daemon to "forget" the session too early.
- **Tool Blindness:** `grep_search`, `glob`, and `read_file` (Gemini-native tools) are not intercepted by the current hook matcher.
- **Overhead:** `uv run --quiet` in hook commands adds significant latency, increasing the risk of hitting the 5s timeout.

### 1.3 Installation Bugs (MAJOR)
- **Broken Symlinks:** `install.py` installs for Gemini from a temporary directory. Gemini (0.28.2) symlinks to the source during local installation. When the temporary directory is deleted, symlinks within the extension (like `/cr:pn`) break.
- **Variable Substitution:** `${extensionPath}` is used in hook commands but verification is needed if Gemini always substitutes this correctly for local extensions in all contexts.

---

## 2. Proposed Architecture: The Symlink Proxy Pattern

To satisfy Gemini's strict directory requirements without renaming every file to `SKILL.md`, we will adopt a **Dual-Layout Symlink Strategy**.

- **Source (The Repo):** `plugins/clautorun/skills/TMUX_MANAGEMENT.md` (Meaningful name preserved).
- **Artifact (The Extension):** `plugins/clautorun/skills/tmux-management/SKILL.md` -> `../TMUX_MANAGEMENT.md` (Relative Symlink).

This ensures:
1.  **Editor Clarity:** Tabs and search results show meaningful names.
2.  **Platform Compliance:** Gemini finds the `SKILL.md` entry point it demands.
3.  **Zero Duplication:** No content is copied; the symlink ensures the live source is always used.

---

## 3. AIX Convergence & Strategy

AIX (https://github.com/thoreinstein/aix) provides unified management for AI extensions.

### 3.1 AIX as the "Source of Truth"
Treat `aix.toml` as the primary manifest for all platforms.
- **Manifest Generation:** Build a utility to generate `gemini-extension.json` and `plugin.json` from `aix.toml`.
- **Structure Generation:** Automatically create the nested `skills/<name>/SKILL.md` structure during the build/install phase.

### 3.2 AIX Workflow
1.  **Modify Logic:** Update code in `src/` or add a command `.md`.
2.  **Update Manifest:** Add entry to `aix.toml`.
3.  **Build/Deploy:** Run unified install/build command to update all platform artifacts and proxy symlinks.

---

## 4. Core Architecture Mapping (Claude vs. Gemini)

| Feature | Claude Code Path | Gemini CLI Path | Integration Strategy |
| :--- | :--- | :--- | :--- |
| **Settings Commands** | `UserPromptSubmit` | `BeforeAgent` | Map via `GEMINI_EVENT_MAP`. Return `continue: False`. |
| **Autonomous Loop** | `Stop` hook (`block`) | `AfterAgent` hook (`deny`) | **KEY FIX**: Map `AfterAgent` -> `Stop`. Return `decision: deny`. |
| **Safety Guards** | `PreToolUse` | `BeforeTool` | Map via `GEMINI_EVENT_MAP`. Map `ask` -> `deny` for Gemini. |
| **Plan Export** | `PostToolUse` (Exit) | `AfterTool` (Exit) | Map via `GEMINI_EVENT_MAP`. |
| **Variables** | `${CLAUDE_PLUGIN_ROOT}` | `${extensionPath}` | `install.py` must substitute BOTH in all `.md` files. |
| **Tool Names** | `Bash`, `Write`, `Edit` | `run_shell_command`, `write_file`, `replace` | Use `BASH_TOOLS`, `WRITE_TOOLS` sets from `config.py`. |

### 4.1 The "Instruction Injection" Pattern (Double-Tap)
1.  **Turn 1 (Trigger):** User types command. `BeforeAgent` hook returns "✅ Started" and `continue: False`.
2.  **Turn 2 (Injection):** `AfterAgent` hook detects `autorun_active`, returns `decision: deny` with the REAL instructions as the `reason`.
3.  **Turn 3 (Execution):** AI receives instructions and begins the IMPLEMENT -> EVALUATE -> VERIFY loop.

---

## 5. End-to-End Actionable Plan

### Phase 1: Repository Restructuring (The "Shadow" Layout)
1.  **Skills Migration:** Create subdirectories for all flat skills and link `SKILL.md` back to the original meaningful file.
2.  **Command Migration:** Standardize and resolve broken symlinks (e.g., `pn.md` -> `plannew.md`).

### Phase 2: AIX Integration
1.  **Unified Manifest:** Update `aix.toml` with all semantic names.
2.  **Code-Gen Step:** Integrate a utility into `clautorun --install` to parse `aix.toml` and generate platform manifests.
3.  **Linking Logic:** Fix `clautorun --install` to use stable absolute paths (stop using `/tmp`).

### Phase 3: Hook & Daemon Robustness
1.  **Gemini Timing:** Update `hook_entry.py` to use a 4s internal timeout for Gemini.
2.  **Session Identity:** Implement stable PID discovery by traversing parent processes until the CLI binary is found.
3.  **Tool Coverage:** Expand `gemini-hooks.json` matcher to include `read_file`, `glob`, `grep_search`.

---

## 6. Actionable Options

### Option A: Manual "Shadow" Folders (Immediate)
- Manually create folders and symlinks in the repo.
- Pros: Simple, works with current installer.
- Cons: High maintenance, prone to drift.

### Option B: AIX Build System (Recommended)
- Use `aix.toml` to drive a `clautorun build` command.
- Pros: Clean repo, robust multi-platform support.
- Cons: New build step required.

---

## 7. Gotchas & Failure Modes
- **Symlink Support:** Windows compatibility (Mitigation: use small "proxy" includes if links fail).
- **Recursive Loops:** Directory scanning hazards (Mitigation: use strict relative paths).
- **Gemini Cache:** Manifest caching (Mitigation: run `gemini extensions reload`).

---

## 8. Pre-Mortem: Why would this fail?
- **Scenario 1: "The Ghost Command":** Broken links to `/tmp`. (Prevention: Persistent repo paths).
- **Scenario 2: "The 5-Second Wall":** `uv run` overhead kills hooks. (Prevention: Call `.venv` binary directly).
- **Scenario 3: "Naming Collision":** Duplicate skill IDs. (Prevention: Manifest-level ID enforcement).

---

## 9. Success Criteria
1.  [ ] `gemini --prompt "/help"` lists all `/cr:` commands correctly.
2.  [ ] `gemini skills list` shows all clautorun skills with correct descriptions.
3.  [ ] `rm test.txt` is blocked in Gemini within 100ms.
4.  [ ] Repository remains readable with meaningful filenames (no `SKILL.md` flood).

---

## 10. Implementation Checklist
1.  [ ] Restructure `skills/` using relative symlinks.
2.  [ ] Update `gemini-hooks.json` with `AfterAgent` and extended tool matching.
3.  [ ] Enhance `core.py` GEMINI_EVENT_MAP and `respond()` logic.
4.  [ ] Fix `install.py` absolute path resolution and path substitution.
5.  [ ] Optimize `hook_entry.py` (4s timeout, direct venv binary).
