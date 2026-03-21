# Feature: `session`/`sess`/`s` keyword + truly persistent `permanent`

## Problem

Today `permanent` means different things depending on the command:
- `/ar:ok rm permanent` = unlimited for the rest of this session (not actually permanent)
- `/ar:globalok rm permanent` = unlimited across sessions until cleared (actually permanent)

The word "permanent" is misleading for session-scoped allows.

## Proposed Change

Add `session`/`sess`/`s` as new scope keywords, and make `permanent` truly persistent everywhere.

### New behavior

| Keyword | `/ar:ok` (session cmd) | `/ar:globalok` (global cmd) |
|---------|----------------------|---------------------------|
| `session`/`sess`/`s` | Rest of this session (current behavior of `permanent`) | Rest of this session (new — downgrades global to session) |
| `permanent`/`perm`/`p` | **Persisted at project path level** (new) | Until cleared (current behavior, unchanged) |

### What "project path level" means

Options to decide:
1. **Git root** — `git rev-parse --show-toplevel` at time of command. Stored in daemon_state.json under project path key.
2. **CWD** — current working directory. Simpler but less stable.
3. **`.claude/` directory** — wherever the nearest `.claude/` dir is. Aligns with Claude Code project concept.

Recommendation: Git root. It's stable, unambiguous, and matches how most project-level config works.

### Storage

Currently:
- Session: `{session_id}/session_allowed_patterns` in daemon_state.json
- Global: `__global__/global_allowed_patterns` in daemon_state.json

New tier needed:
- Project: `{git_root_hash}/project_allowed_patterns` in daemon_state.json

### Files to modify

1. `scoped_allow.py:parse_scope_args()` (lines 42-76) — add `session`/`sess`/`s` to keyword parsing
2. `scoped_allow.py:_PERMANENT_KEYWORDS` — split into `_SESSION_KEYWORDS` and `_PERMANENT_KEYWORDS`
3. `plugins.py:_make_block_op()` (lines 464-493) — handle new `session` keyword, handle `permanent` creating project-scoped entry
4. `plugins.py:check_blocked_commands()` (lines 611-629) — add project tier to TIER 1 allow checks (between session and global)
5. `plugins.py:ScopeAccessor` (lines 365-420) — add `"project"` scope with project-path-keyed storage
6. `config.py` — add project scope to TIER documentation
7. `commands/ok.md`, `commands/globalok.md` — update keyword docs
8. `README.md`, `CLAUDE.md` — update scope tables and descriptions

### Backward compatibility

- `permanent`/`perm`/`p` on `/ar:ok` would change from session-scoped to project-persisted — **breaking change**
- Migration path: users who currently use `/ar:ok rm permanent` expecting session scope would need to switch to `/ar:ok rm session`
- Alternative: add `session` keyword without changing `permanent` behavior, then change `permanent` in a later version with a deprecation warning

### Priority order

The TIER 1 allow check order would become:
1. Session allows (`/ar:ok`)
2. Project allows (`/ar:ok ... permanent`)
3. Global allows (`/ar:globalok`)

### Open questions

1. Should `/ar:no` and `/ar:globalno` also support `session`/`permanent` scope keywords? Blocks currently have no temporal scope — they last until cleared.
2. Should there be a `/ar:projectclear` command? Or does `/ar:clear` clear project-level too?
3. Should `/ar:blocks` show project-level allows? Or a separate `/ar:projectstatus`?
4. How to handle project path changes (e.g., user cd's to a different repo)?
