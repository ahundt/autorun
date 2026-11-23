---
description: Warn or stop before a prompt re-sends an expired prompt cache; optional tool-time cache thresholds
argument-hint: "[on|warn|off|set|ok|no|status] [args…]"
---

# Prompt cache guard (/ar:cache)

$ARGUMENTS

When this message's hook context holds a `<held-prompt>`, answer that
prompt as the user's message for this turn; this command needs no reply.

## What you see

Autorun holds prompts by default in the Claude Code terminal and Claude
desktop app when they would re-write an expired cache of 100,000 tokens or
more. SDK hosts such as T3 Code, unknown entrypoints and other harnesses
only warn by default when cache data is available.

Example hold in Claude Code:

```
ar:cache held this prompt. The prompt cache expired 5 h 48 m ago, so sending it
re-writes about 257k tokens at the cache-write price.

Send the same prompt again to proceed once, or use:
  /ar:cache ok
Cut the cost:
  /clear
Or start a new session or combine requests into one prompt.
Stop the check in this session (prompt caching stays on):
  /ar:cache off
Turn off the global default (session settings still win):
  /ar:cache global off
Warn without holding:
  /ar:cache warn
Check only larger contexts:
  /ar:cache set expired-min-tokens 200k
```

Commands appear in the active harness's spelling. Nothing is added to the
model's context.

The cache has expired when the time since the last response is longer than
the cache lifetime that response wrote: 1 hour or 5 minutes on Claude Code,
read from the transcript. The context size is the input, cache-read and
cache-write tokens of that response. Each response that reads the cache
restarts the lifetime, so the clock runs from the last response, and an
unanswered prompt does not restart it.

When the record states no lifetime, autorun uses your `/ar:cache set age
<duration>`, else 300 seconds (CONFIG `cache_guard_default_lifetime_seconds`;
0 turns the guess off). 300 seconds is the shortest documented default:
Anthropic's [5-minute prompt cache](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
and OpenAI's [in-memory retention](https://developers.openai.com/api/docs/guides/prompt-caching)
of "around 5 to 10 minutes of inactivity". Google documents [no implicit-cache
lifetime](https://aistudio.google.com/docs/caching). Claude Code requests the
1-hour lifetime only for the main conversation on a subscription
([Claude Code docs](https://code.claude.com/docs/en/prompt-caching)); with an
API key, a cloud provider or usage credits it is 5 minutes. A guessed lifetime
only warns, never holds a prompt, and the message says what it assumed.
Anthropic measures the lifetime from the start of the request; autorun
measures from the last response. The check needs a record that reports
at least one cache-read or cache-write token.
Auto-compaction runs inside a turn that began with a prompt, so this check
runs before it, and an automatic compaction is never held. A manual `/compact`
on an expired cache is held like a prompt, because summarizing re-sends the
whole old context: run the same `/compact` again to proceed once, or `/clear`
to drop the context instead. Where the check only warns, `/compact` is not
checked, since a compaction hook has no way to show a notice. After a
compaction the next prompt is not checked for the context it replaced; the
check resumes with the next response.

In `autorun.config.json`, `cache_guard_expired_prompt_action` sets the
default for sessions with no setting of their own: `block` (the default),
`warn` or `off`. Any other value is logged and treated as `block`.

## What proceeding costs

Sending re-writes the whole context into the cache at the cache-write price
instead of reading it at the much lower cache-read price. On a 250,000-token
context that is one full-price write of 250,000 tokens. A new session or,
on Claude Code, `/clear` avoids re-writing the old context. Claude Code's
`/compact` shrinks later turns.

## Switching it

```
/ar:cache on      # hold in this session, including SDK hosts that can hold prompts
/ar:cache warn    # warn: show the message, send the prompt
/ar:cache off     # stop autorun's cache checks (prompt and tool-time) in this session; prompt caching stays on
```

Each takes an optional duration (`5m`, `1h`, or `perm`). A timed setting
returns to the previous setting when it ends. `/ar:cache global off` (or
`on`, `warn`) sets the default for sessions without their own setting.

Send the same text again to proceed once, or run `/ar:cache ok`. On Claude
Code every slash command is itself a model request, so `/ar:cache ok` sends
the held prompt in its place: one request answers it, as a resend would. A
held prompt over 9,000 characters is not sent that way; resend it. With
nothing held, `/ar:cache ok` lets the next prompt through. `/ar:cache ok 3`
also keeps passes for the next three prompts.

The check skips registered autorun commands, subagent hooks, prompts marked
as non-user, and Claude Code's three known wrappers: `<task-notification>`,
`<agent-message …>`, and `<cross-session-message …>`.

```
/ar:cache set expired-min-tokens 200k   # check only contexts of 200,000 tokens or more; 0 checks every size
/ar:cache                               # status: the prompt check's setting, the floor, thresholds, overrides
```

Token units: `50000`, `50,000`, `50_000`, `50k`, `.5M`. Durations: `5s`, `5m`,
`1h`, `2h30m`, `2d`, `perm`.

## Where it applies

| Harness | Cache data in its session records | Result |
| --- | --- | --- |
| Claude Code | yes, with the lifetime written | terminal and desktop hold by default; other entrypoints warn unless you opt in with `/ar:cache on` |
| Codex | no; usage is in `event_msg` records autorun does not read | silent |
| Pi, Prime Agent | yes, with no lifetime | warns using the lifetime you set with `/ar:cache set age <duration>`, else an assumed 5 minutes (see below). These harnesses cannot stop a prompt from a hook, so `on` also warns |
| Gemini CLI, Qwen Code | not confirmed | warns when cache data is found, using the same lifetime rule; never blocks |
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
