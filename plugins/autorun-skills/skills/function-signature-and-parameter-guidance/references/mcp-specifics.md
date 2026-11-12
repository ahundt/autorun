# MCP Specifics

Everything protocol-particular, kept out of the main reference so the passes stay
surface-agnostic. Pass B8 states the general contract; this file is how MCP spells it.

Verified against
[`schema/2025-06-18/schema.ts`](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2025-06-18/schema.ts)
and the [tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools),
2026-07-20.

---

## The four declarations a tool should carry

| Declaration | Default when absent | What that costs |
|---|---|---|
| `inputSchema.additionalProperties` | `true` — unknown keys accepted and dropped | a misspelled argument succeeds silently |
| `outputSchema` | absent | the response shape is unguessable before calling |
| `annotations` | absent → see the hint defaults below | every tool is treated as destructive |
| `title` | falls back to `name` | cosmetic |

`outputSchema` is optional, but once declared the server **MUST** return conforming
`structuredContent`, and clients **SHOULD** validate it. Declaring it is a promise, not a hint.

## ToolAnnotations, and why the defaults matter

```typescript
export interface ToolAnnotations {
  title?: string;
  readOnlyHint?: boolean;     // Default: false
  destructiveHint?: boolean;  // Default: true
  idempotentHint?: boolean;   // Default: false
  openWorldHint?: boolean;    // Default: true
}
```

Omitting the block does not leave a caller guessing. It **asserts** the tool is destructive,
non-idempotent, and open-world. The cost therefore lands on the read-only majority: a
conforming client must treat `list_projects` exactly like `delete_project`, so it confirms
every call with the user or trusts none.

Two conditionals the schema states and a caller cannot infer:

1. `destructiveHint` and `idempotentHint` are meaningful **only when `readOnlyHint == false`**.
2. Display precedence is `title`, then `annotations.title`, then `name`.

Clients **MUST** treat annotations as untrusted unless the server is trusted — they are hints
for planning, not a security boundary.

Do not confuse `ToolAnnotations` with `Annotations` (`audience`, `priority`, `lastModified`),
which annotates **content**, not tools.

## Two error channels, and which to use

| Channel | Means | Example |
|---|---|---|
| JSON-RPC error `-32601` | method not found | unknown tool name |
| JSON-RPC error `-32602` | invalid params | unknown parameter, out-of-range value |
| result with `isError: true` | the call was valid, the operation failed | API rate limit, missing file |

Reporting a bad argument as `isError` denies the caller the distinction that decides whether
retrying can help. Trigger all four cases and check which channel each takes: unknown tool,
unknown parameter, out-of-range value, and a valid call whose operation fails.

## Auditing a live server

Any MCP server, whatever its implementation language, because this reads protocol output:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"audit","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | your-mcp-server > /tmp/tools.jsonl

python3 scripts/check-schema-descriptions.py /tmp/tools.jsonl
```

If the server hides tools behind a reveal call, invoke that first — a `tools/list` before it
audits only the visible subset.

## Measured across three servers

| | ai-session-search | codebase-memory-mcp | GitKraken |
|---|---|---|---|
| `additionalProperties: false` | **7/7** | 0/17 | 0/31 |
| `outputSchema` | 4/7 | **17/17** | 0/31 |
| `annotations` | 0/7 | 0/17 | **31/31** |
| `title` | 0/7 | **17/17** | 0/31 |

Each server is strong on exactly one declaration and weak on the rest, and no two agree on
which. Three independent teams each solved a different quarter of the same contract, which is
the evidence that B8 checks something real rather than something invented.
