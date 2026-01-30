---
description: Require justification before creating new files
---

# AutoFile Justify Mode

Policy set to: **justify-create**

Search for existing files first before creating new ones. New file creation requires the `<AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>` tag in your reasoning.

**Example**:
```
<AUTOFILE_JUSTIFICATION>
Creating new config file because existing config.json doesn't support feature X
</AUTOFILE_JUSTIFICATION>
```

UserPromptSubmit hook has updated the session policy.
