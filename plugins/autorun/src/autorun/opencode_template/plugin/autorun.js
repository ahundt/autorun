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

const SOCKET = "__AUTORUN_SOCKET__"
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

  return new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        resolve(value)
      }
    }
    const timer = setTimeout(() => finish(null), TIMEOUT_MS)
    const chunks = []

    try {
      Bun.connect({
        unix: SOCKET,
        socket: {
          open(sock) {
            sock.write(frame)
          },
          data(sock, chunk) {
            chunks.push(chunk)
            if (!chunk.includes(10)) return // 10 = "\n": the frame terminator
            // Settle BEFORE closing. sock.end() fires close() synchronously,
            // and close() resolves null, so ending first threw the answer away
            // and every deny read as an allow.
            let reply = null
            try {
              reply = JSON.parse(Buffer.concat(chunks).toString())
            } catch {
              reply = null
            }
            finish(reply)
            sock.end()
          },
          error() {
            finish(null)
          },
          close() {
            finish(null)
          },
        },
      }).catch(() => finish(null))
    } catch {
      finish(null)
    }
  })
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
  try {
    const child = Bun.spawn(command, { stdin: "pipe", stdout: "pipe", stderr: "ignore" })
    child.stdin.write(JSON.stringify({ ...payload, cli_type: "opencode" }) + "\n")
    child.stdin.end()
    const text = await new Response(child.stdout).text()
    return JSON.parse(text)
  } catch {
    return blockedBecause("autorun daemon and hook entry both unreachable")
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

export const AutorunPlugin = async ({ serverUrl, directory }) => {
  // The inversion: hand Python the server address so the long-lived daemon can
  // be the SDK client. Losing this frame costs the daemon-side features, not
  // the veto, so a failure here is not fatal.
  await askDaemon({
    hook_event_name: "OpenCodeAttach",
    server_url: String(serverUrl ?? ""),
    cwd: String(directory ?? ""),
  })

  return {
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
}
