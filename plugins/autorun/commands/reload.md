---
description: Force-reload all integration rules from config files
---

# Reload Integrations

Force-reload all safety guard integrations from `config.py` and any custom integration files. Reports the count of loaded integrations.

**Usage**: `/ar:reload`

**When to use**:
- After editing `config.py` DEFAULT_INTEGRATIONS
- After adding custom integration files
- If hooks appear to be stale or not firing

**Expected output**: "Reloaded N integrations" (N >= 28 for default safety guards)

UserPromptSubmit hook processes this command and calls `integrations.load_all_integrations()`.
