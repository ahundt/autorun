---
description: Emergency stop - immediately halt all actions (short for /ar:estop)
# Manual-only: reachable by typing the command, not by model choice.
# Halts autonomous work outright; a model deciding to trip it mid-task is a failure mode, not a feature.
disable-model-invocation: true
---

# Emergency Stop (SOS)

**⚠️ WARNING**: This will immediately halt all autonomous operations.

Use this when you need to stop all activity immediately. For graceful stop that allows current task to complete, use `/ar:x` or `/ar:stop`.

UserPromptSubmit hook has processed the emergency stop command.
