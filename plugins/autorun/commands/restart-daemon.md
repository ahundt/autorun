---
name: restart-daemon
description: Restart autorun daemon to reload Python code changes
aliases: [rd, daemon-restart]
---

# Restart Daemon

Restarts the autorun daemon for the current autorun install to load updated Python code.

**When to use:** After modifying Python files in the plugin (integrations.py, config.py, etc.)

!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -m autorun --restart-daemon`

**What it does:**
1. Gracefully stops the daemon for the current AUTORUN_HOME/source tree (SIGTERM, waits 5s, SIGKILL if needed)
2. Cleans up stale socket and lock files
3. Triggers daemon auto-start
4. Verifies new daemon running with fresh code
5. Checks bashlex availability

**Risky all-daemons maintenance restart:** If stale daemons from multiple
installs must be stopped, run `autorun --restart-all-daemons` explicitly. This
can interrupt active autorun-backed sessions in other installs. Normal restart
is scoped so active production/worktree sessions are not killed accidentally.
