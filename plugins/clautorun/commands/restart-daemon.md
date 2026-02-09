---
name: restart-daemon
description: Restart clautorun daemon to reload Python code changes
aliases: [rd, daemon-restart]
---

# Restart Daemon

Restarts the clautorun daemon to load updated Python code.

**When to use:** After modifying Python files in the plugin (integrations.py, config.py, etc.)

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/restart_daemon.py`

**What it does:**
1. Gracefully stops daemon (SIGTERM, waits 5s, SIGKILL if needed)
2. Cleans up stale socket and lock files
3. Triggers daemon auto-start
4. Verifies new daemon running with fresh code
5. Checks bashlex availability
