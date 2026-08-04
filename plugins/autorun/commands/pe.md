---
description: "Plan export status and settings [on|off|globalon|globaloff|dir <path>|pattern <template>|rejected [on|off|dir <path>]|reset]"
argument-hint: "[on|off|globalon|globaloff|dir <path>|pattern <template>|rejected [on|off|dir <path>]|reset]"
---

# Plan Export (/ar:pe)

$ARGUMENTS

Plans are exported automatically when you exit plan mode. This one command
shows status and owns every plan-export setting.

## Usage

```
/ar:pe                        # show status: effective state and which layer set it
/ar:pe off                    # turn off for this project only
/ar:pe on                     # turn on for this project only (pins over the global default)
/ar:pe globaloff              # turn off everywhere (projects with a pin keep theirs)
/ar:pe globalon               # turn on everywhere (projects with a pin keep theirs)
/ar:pe dir <path>             # set the export directory (template variables allowed)
/ar:pe pattern <template>     # set the filename pattern, e.g. {datetime}_{name}
/ar:pe rejected               # toggle rejected-plan export
/ar:pe rejected on|off        # set rejected-plan export explicitly
/ar:pe rejected dir <path>    # set the rejected-plan directory
/ar:pe reset                  # restore defaults (also clears project pins)
```

A project pin beats the global default. Settings persist in autorun's config
directory and apply to every supported harness; a pre-0.13 settings file under
`~/.claude/` is still read until the first write publishes the new location.

Template variables for `dir` and `pattern`: `{YYYY}` `{YY}` `{MM}` `{DD}`
`{HH}` `{mm}` `{ss}` `{date}` `{datetime}` `{name}` `{original}`.
