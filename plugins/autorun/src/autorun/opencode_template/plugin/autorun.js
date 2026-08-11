// autorun bridge for OpenCode.
//
// OpenCode delivers no external hook events, so this plugin is the per-harness
// adapter that carries them to the daemon socket every other harness already
// uses — the role hooks/hook_entry.py plays elsewhere. It stays deliberately
// thin: the only work that must happen in-process is vetoing a tool call and
// answering a command, and every decision is made in Python.
//
// The installer substitutes __AUTORUN_SOCKET__ with an absolute path at copy
// time, so nothing resolves through the host PATH while a session is running.
//
// This file is the ONLY JavaScript autorun ships, and it installs only into
// OpenCode's config dir. OpenCode is a Bun program that loads plugins
// in-process, so a user running it already has Bun; every other harness needs
// Python alone and must never be made to install a second runtime. That is
// also why the general hook path stays on hooks/hook_entry.py: a Bun client
// there measured 28.2 ms against nc's 9.7 ms, so it would cost a dependency
// and still lose.
//
// Failure policy: the same one hook_entry.py applies on every other harness.
// A tool gate autorun cannot evaluate BLOCKS with a restart hint
// (hook_entry.fail_closed_tool_gate), because failing open would delete every
// guard the moment the daemon is down. Lifecycle frames that gate nothing
// (attach, detach) stay silent when the daemon is unreachable.

import { existsSync, readFileSync } from "node:fs"

const SOCKET = __AUTORUN_SOCKET__
const PORT_FILE = __AUTORUN_PORT_FILE__

/**
 * Where the daemon is listening, in the form Bun.connect wants.
 *
 * CPython does not expose AF_UNIX on Windows, so the daemon listens on
 * loopback there and writes its port beside where the socket would be --
 * the same split ipc.py and hook_entry.py make. Bun speaks both, so this
 * returns a target object rather than forcing a second connect path, and
 * needs no dependency: Bun.file covers both reads.
 *
 * Returns null when neither exists, which is the same "no daemon" answer a
 * failed connect gave before, reached without opening a socket.
 */
function daemonTarget() {
  // existsSync, not Bun.file().exists(): the latter answers false for a
  // socket, because it asks whether the path is a regular file. node:fs is
  // built into Bun, so this stays dependency-free.
  try {
    if (existsSync(SOCKET)) return { unix: SOCKET }
  } catch {
    // An unreadable socket path is simply not a usable daemon.
  }
  try {
    const port = Number.parseInt(readFileSync(PORT_FILE, "utf8").trim(), 10)
    if (Number.isInteger(port) && port > 0) return { hostname: "127.0.0.1", port }
  } catch {
    // No port file: nothing is listening that way either.
  }
  return null
}
// Absolute argv for hooks/hook_entry.py, substituted at install time; empty
// when the installer could not resolve one.
const HOOK_ENTRY_COMMAND = __AUTORUN_HOOK_ENTRY_COMMAND__
const TIMEOUT_MS = 5000
const PROTOCOL_VERSION = 1

/**
 * Send one newline-delimited JSON frame and read one back.
 *
 * Cost: one local socket round-trip, ~0.1-0.3 ms, no subprocess. Resolves
 * null on timeout, connection failure, or unparseable reply — every one of
 * which means "allow".
 */
async function askDaemon(payload) {
  const frame =
    JSON.stringify({
      ...payload,
      cli_type: "opencode",
      protocol_version: PROTOCOL_VERSION,
      _pid: process.pid,
    }) + "\n"

  // RAII shape: acquire inside the try, release once in the finally. Promise
  // resolution is idempotent, so the handlers just resolve; the finally runs
  // strictly after the promise settles, which makes the settle-before-close
  // invariant hold by construction — an earlier version called sock.end()
  // inside the data handler, close() fired synchronously and resolved null
  // first, and every deny read as an allow.
  const target = daemonTarget()
  if (target === null) return null

  let sock = null
  let timer = null
  let done = false
  try {
    return await new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), TIMEOUT_MS)
      const chunks = []
      Bun.connect({
        ...target,
        socket: {
          open(s) {
            sock = s
            s.write(frame)
          },
          data(s, chunk) {
            chunks.push(chunk)
            if (!chunk.includes(10)) return // 10 = "\n": the frame terminator
            try {
              resolve(JSON.parse(Buffer.concat(chunks).toString()))
            } catch {
              resolve(null)
            }
          },
          error() {
            resolve(null)
          },
          close() {
            resolve(null)
          },
        },
      }).then((s) => {
        // The connect promise can resolve after the timeout already settled
        // the call; adopt the socket so the finally below (or this branch,
        // when it ran already) still closes it.
        sock = s
        if (done) {
          try {
            s.end()
          } catch {
            // already closed
          }
        }
      }).catch(() => resolve(null))
    })
  } catch {
    return null
  } finally {
    done = true
    clearTimeout(timer)
    try {
      sock?.end()
    } catch {
      // already closed
    }
  }
}

/**
 * Fall back to the entry point every other harness already uses.
 *
 * The installer substitutes the absolute command. When even this cannot
 * answer, a tool gate denies rather than allowing: `fail_closed_tool_gate` in
 * hook_entry.py is the policy this mirrors, so a dead daemon does not quietly
 * remove OpenCode's guards while leaving every other harness protected.
 */
async function askHookEntry(payload) {
  const command = HOOK_ENTRY_COMMAND
  if (!command.length) return blockedBecause("autorun daemon unreachable")
  let child = null
  let timer = null
  try {
    child = Bun.spawn(command, { stdin: "pipe", stdout: "pipe", stderr: "ignore" })
    child.stdin.write(JSON.stringify({ ...payload, cli_type: "opencode" }) + "\n")
    child.stdin.end()
    // hook_entry bounds its own socket work, but uv can wedge before Python
    // exists (bootstrap lock, cold cache), and OpenCode enforces no hook
    // timeout the way Claude's hooks.json "timeout": 10 does — so the bound
    // has to live here or the tool call hangs forever.
    const text = await Promise.race([
      new Response(child.stdout).text(),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(null), TIMEOUT_MS)
      }),
    ])
    if (text === null) return blockedBecause("hook entry timed out")
    return JSON.parse(text)
  } catch {
    return blockedBecause("autorun daemon and hook entry both unreachable")
  } finally {
    // The child's lifetime is this call's lifetime: kill on every exit path
    // (kill after normal exit is a no-op) so a wedged interpreter cannot
    // outlive the tool call it was spawned to answer.
    clearTimeout(timer)
    try {
      child?.kill()
    } catch {
      // already exited
    }
  }
}

function blockedBecause(message) {
  return {
    hookSpecificOutput: {
      permissionDecision: "deny",
      permissionDecisionReason:
        `[autorun] ${message}. Blocking tool use to avoid fail-open. ` +
        "Run `autorun --restart-daemon` or `autorun --install --force`, then retry.",
    },
  }
}

function denialReason(response) {
  if (!response) return null
  const specific = response.hookSpecificOutput ?? {}
  const decision = specific.permissionDecision ?? response.decision
  if (decision !== "deny" && decision !== "block") return null
  return specific.permissionDecisionReason ?? response.reason ?? "blocked by autorun"
}

/**
 * Answer one ar:* control command through the daemon.
 *
 * Exported on its own so tests drive it under bare Bun; the registered tool
 * below wraps it and only exists when @opencode-ai/plugin resolves, which
 * happens in-process inside a real OpenCode session. Command dispatch gates
 * nothing, so unlike the veto this fails OPEN: a dead daemon answers with
 * the way out, never a blocked tool call.
 */
export async function runArCommand(command, directory) {
  // Accept every spelling the other harnesses tolerate: /ar:st, ar:st,
  // ar-st, ar st, or the bare name. The daemon's canonicalizer owns the
  // real grammar; this only strips the prefix so "ar:" can be re-applied.
  const name = String(command ?? "").trim().replace(/^\/?ar[:\- ]\s*/i, "")
  const response = await askDaemon({
    hook_event_name: "UserPromptSubmit",
    prompt: "ar:" + name,
    cwd: String(directory ?? ""),
  })
  return (
    response?.systemMessage ??
    response?.hookSpecificOutput?.additionalContext ??
    "autorun daemon unreachable; command dispatch is fail-open. " +
      "Run `autorun --restart-daemon`, then retry."
  )
}

export const AutorunPlugin = async ({ serverUrl, directory }) => {
  // The inversion: hand Python the server address so the long-lived daemon can
  // be the SDK client. Losing this frame costs the daemon-side features, not
  // the veto, so a failure here is not fatal.
  await askDaemon({
    hook_event_name: "OpenCodeAttach",
    server_url: String(serverUrl ?? ""),
    cwd: String(directory ?? ""),
  })

  const hooks = {
    "tool.execute.before": async (input, output) => {
      const frame = {
        hook_event_name: "PreToolUse",
        session_id: input.sessionID,
        tool_name: input.tool,
        tool_input: output?.args,
        cwd: String(directory ?? ""),
      }
      let response = await askDaemon(frame)
      if (response === null) {
        // Socket unreachable: hand the frame to hook_entry.py, the same entry
        // point every other harness uses, which starts the daemon or answers
        // inline. It costs an interpreter start, which is why it is only on
        // this path and not on the one that runs per tool call.
        response = await askHookEntry(frame)
      }
      // Throwing is OpenCode's documented deny: the tool call fails, the model
      // reads why, and the session continues.
      const reason = denialReason(response)
      if (reason) throw new Error(reason)
    },

    dispose: async () => {
      await askDaemon({ hook_event_name: "OpenCodeDetach", cwd: String(directory ?? "") })
    },
  }

  // The command tool needs @opencode-ai/plugin's schema helper, which only
  // resolves in-process inside OpenCode (its Bun carries the package). Under
  // bare Bun — this shim's own test rig — it is absent, and the veto must
  // not die with it, so the import is dynamic and its failure costs only
  // the tool.
  try {
    const { tool } = await import("@opencode-ai/plugin")
    hooks.tool = {
      autorun: tool({
        description:
          "Run an autorun control command: st (status), allow/justify/find, " +
          "ok/no/blocks/clear guards, task, cache, planexport, stop/estop, " +
          "help. Use when the user types ar:<command> in any spelling or " +
          "asks about autorun status or control.",
        args: {
          command: tool.schema
            .string()
            .describe("the command with its arguments, e.g. 'st' or 'ok git push 5m'"),
        },
        async execute({ command }) {
          return runArCommand(command, directory)
        },
      }),
    }
  } catch {
    // bare Bun: no package, no tool; the veto and lifecycle hooks above
    // still work, which is the part that must never depend on packaging.
  }

  return hooks
}
