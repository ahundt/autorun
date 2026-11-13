---
description: Warn or stop before a prompt re-sends an expired prompt cache; optional tool-time cache thresholds
argument-hint: "[on|warn|off|set|ok|no|status] [args…]"
---

# Prompt cache guard (/ar:cache)

$ARGUMENTS

## What you see

When you send a prompt after the session's prompt cache has expired, and the
context is 100,000 tokens or more, autorun shows this before the request goes
out:

```
ar:cache: this session's prompt cache expired 5 h 48 m ago.
This prompt re-writes about 257,000 tokens at the cache-write price.
  /clear or a new session avoids the cost; /compact lowers later turns
  /ar:cache off        this session      /ar:cache on     stop me before sending
  /ar:cache set expired-min-tokens 200k  only warn or block at this context size or larger
```

That is the default, `warn`: the message is shown and the prompt is sent.
Nothing is added to the model's context.

The cache has expired when the time since the last response is longer than
the cache lifetime that response wrote: 1 hour or 5 minutes on Claude Code,
read from the transcript. The context size is the input, cache-read and
cache-write tokens of that response.

## What proceeding costs

Sending re-writes the whole context into the cache at the cache-write price
instead of reading it at the much lower cache-read price. On a 250,000-token
context that is one full-price write of 250,000 tokens. `/clear` or a new
session avoids it; `/compact` makes the context, and so every later turn,
smaller.

## Switching it

```
/ar:cache on      # block: the prompt is stopped; send the same text again to proceed
/ar:cache warn    # warn: show the message, send the prompt (the default)
/ar:cache off     # no prompt check and no tool-time check in this session
```

Each takes an optional duration, `5m`, `1h` or `perm`; a timed one returns to
the previous setting when it ends. `/ar:cache global off` (or `on`, `warn`)
sets the default for every session.

With `on`, Claude Code shows the blocked prompt's text under the message, so
nothing typed is lost. Sending the same text again passes for the rest of the
session. `/ar:cache ok` lets the next prompt through once. `/ar:cache off` and
every other autorun command always get through. A prompt you did not type (a background-task notification, a message
from a subagent or another session, a prompt with a subagent id) is never
checked.

```
/ar:cache set expired-min-tokens 200k   # check only contexts of 200,000 tokens or more; 0 checks every size
/ar:cache                               # status: the prompt check's setting, the floor, thresholds, overrides
```

Token units: `50000`, `50,000`, `50_000`, `50k`, `.5M`. Durations: `5s`, `5m`,
`1h`, `2h30m`, `2d`, `perm`.

Every `/ar:cache` form takes effect only when the user types it; sent by the
model or a subagent, it changes nothing.

## Where it applies

| Harness | Cache data in its session records | Result |
| --- | --- | --- |
| Claude Code | yes, with the lifetime written | warns, or blocks with `/ar:cache on` |
| Codex | no; usage is in `event_msg` records autorun does not read | silent |
| Pi, Prime Agent | yes, with no lifetime | warns only when you have set `/ar:cache set age <duration>`, which stands in for the lifetime; silent otherwise. These harnesses cannot stop a prompt from a hook, so `on` also warns |
| Gemini CLI, Qwen Code | not confirmed | warns only when `age` is set and data is found; never blocks |
| Antigravity, OpenCode | not confirmed | untested; the check runs on their prompt event and stays silent when it finds no cache data |
| ForgeCode | sends autorun no events | not applicable |

A response that arrives after the lifetime while a turn is already running
(a slow tool result) is not caught: the turn has started.

## Tool-time thresholds (optional)

After `/ar:cache on` or `warn`, these also run before each tool call and deny
it when one trips. None is set by default.

```
/ar:cache set ratio 0.3    # deny when cache_read / (input + cache read + cache write) < 30%
/ar:cache set read 50k     # deny when cache-read tokens < 50,000
/ar:cache set age 10m      # deny when the last response is older than 10 minutes

/ar:cache ok 5m            # allow for 5 minutes
/ar:cache ok 3             # allow the next 3 tool uses (or the next 3 prompts)
/ar:cache ok perm          # allow until the session ends
/ar:cache no               # cancel outstanding ok grants
```

They never block a prompt; at prompt time only the expiry check runs. Missing
data never trips anything: no transcript, no usage record, or a record the
reader cannot parse means allow.

`autorun --cache-snapshot` is still accepted so an old statusline line keeps
working, but it does nothing. You can remove it from your statusline script.

Implementation: `plugins/autorun/src/autorun/cache_guard.py`
(`CacheGuard.check_prompt_after_expiry` at prompt time, `CacheGuard.check` at
tool time).
