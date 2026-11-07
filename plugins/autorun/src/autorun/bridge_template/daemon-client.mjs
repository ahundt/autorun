// Shared in-process transport for JavaScript/TypeScript harness adapters.
//
// Policy stays in the Python daemon. This module owns only newline-delimited
// JSON transport, bounded hook-entry fallback, and response extraction.

import { existsSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { spawn } from "node:child_process";

const PROTOCOL_VERSION = 1;
// = autorun.config.HOOK_DEADLINE_PAYLOAD_KEY; test_client_fail_closed.py pins
// it. Epoch seconds: the daemon converts to its own monotonic clock, because
// Node's hrtime is not Python's monotonic clock on macOS after a sleep.
const HOOK_DEADLINE_PAYLOAD_KEY = "_autorun_hook_deadline_epoch";

function daemonTarget(socketPath, portFile) {
  try {
    if (socketPath && existsSync(socketPath)) return { path: socketPath };
  } catch {
    // The socket is not usable.
  }
  try {
    const port = Number(readFileSync(portFile, "utf8").trim());
    if (Number.isInteger(port) && port > 0 && port <= 65535) {
      return { host: "127.0.0.1", port };
    }
  } catch {
    // No valid loopback port file.
  }
  return null;
}

// AUTORUN_DISABLE stands autorun down, with the same truthy set as
// hooks/hook_entry.py. Read here because these bridges ask the daemon first and
// never reach hook_entry while it is up, so a denying daemon could not be
// escaped through the exit every fail-closed reason names. Exported so a
// harness adapter can tell a stood-down bridge from an unreachable daemon.
export function autorunDisabled() {
  const value = String(process.env.AUTORUN_DISABLE ?? "").trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

// Verbatim copies of hooks/hook_entry.py's _RETRY_GUIDANCE and
// _INTERVENTION_GUIDANCE (= autorun.client.UNRECOVERABLE_GUIDANCE). This file is
// installed by a plain copy, so it cannot import them;
// test_client_fail_closed.py pins all three spellings together.
const RETRY_GUIDANCE = "This clears on its own; retry shortly.";
// = autorun.client.RESPONSE_TIMEOUT_GUIDANCE, for a request the daemon took and
// never answered; test_daemon_throughput.py pins the two together.
const RESPONSE_TIMEOUT_GUIDANCE = "Blocking this tool call because the autorun daemon took the request but did not answer in time: it is either very busy or stuck. Retry the same tool call once. If that is blocked the same way, run `autorun --restart-daemon` in a terminal; it leaves a daemon that just restarted and answers a health check alone, and replaces one that does not.";
const INTERVENTION_GUIDANCE = "Retrying will not help: the same failure repeats. Repair it in a terminal (run the repair step named in the error, or `autorun --status` for diagnosis) and the next hook recovers on its own. To stand autorun down, ask the user to start a new session with AUTORUN_DISABLE=1 set where the harness is launched; a prefix on a command has no effect.";

// Same reason shape as hook_entry.py's fail_closed_tool_gate; the repair step
// is the one autorun.client.build_daemon_failure_response names.
function blockedBecause(message, recoverable = false) {
  const guidance = recoverable
    ? RETRY_GUIDANCE
    : "Repair with `autorun --restart-daemon` in a terminal. " + INTERVENTION_GUIDANCE;
  return {
    hookSpecificOutput: {
      permissionDecision: "deny",
      permissionDecisionReason:
        `[autorun] ${message}. Blocking tool use to avoid fail-open. ${guidance}`,
    },
  };
}

// The Python client reports the same failure under the same event code.
function didNotAnswer() {
  return {
    hookSpecificOutput: {
      permissionDecision: "deny",
      permissionDecisionReason:
        "[autorun] [AR_EVENT_V1:daemon_response_timeout] autorun daemon did not answer in time. " +
        RESPONSE_TIMEOUT_GUIDANCE,
    },
  };
}

export function denialReason(response) {
  if (!response) return null;
  const specific = response.hookSpecificOutput ?? {};
  const decision = specific.permissionDecision ?? response.decision;
  if (decision !== "deny" && decision !== "block") return null;
  return specific.permissionDecisionReason ?? response.reason ?? "blocked by autorun";
}

export function responseMessage(response) {
  return (
    response?.systemMessage ??
    response?.reason ??
    response?.hookSpecificOutput?.additionalContext ??
    response?.hookSpecificOutput?.permissionDecisionReason ??
    ""
  );
}

export function createDaemonBridge({
  cliType,
  socketPath,
  portFile,
  hookEntryCommand = [],
  timeoutMs = 5000,
  inprocessCapabilities = [],
}) {
  // { response, connected }: connected is true once the frame was handed to a
  // daemon, after which asking again (here or through the hook entry) would
  // send the same request twice.
  async function exchange(payload, deadline) {
    const target = daemonTarget(socketPath, portFile);
    if (target === null) return { response: null, connected: false };
    const frame =
      JSON.stringify({
        ...payload,
        cli_type: cliType,
        protocol_version: PROTOCOL_VERSION,
        ...(inprocessCapabilities.length > 0
          ? { inprocess_capabilities: [...inprocessCapabilities] }
          : {}),
        _pid: process.pid,
        [HOOK_DEADLINE_PAYLOAD_KEY]: deadline / 1000,
      }) + "\n";

    return new Promise((resolve) => {
      let settled = false;
      let connected = false;
      let text = "";
      const finish = (response) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        socket.destroy();
        resolve({ response, connected });
      };
      const socket = createConnection(target);
      const timer = setTimeout(() => finish(null), Math.max(deadline - Date.now(), 0));
      socket.setEncoding("utf8");
      socket.on("connect", () => {
        connected = true;
        socket.write(frame);
      });
      socket.on("data", (chunk) => {
        text += chunk;
        const newline = text.indexOf("\n");
        if (newline < 0) return;
        try {
          finish(JSON.parse(text.slice(0, newline)));
        } catch {
          finish(null);
        }
      });
      socket.on("error", () => finish(null));
      socket.on("close", () => finish(null));
    });
  }

  async function askDaemon(payload) {
    if (autorunDisabled()) return null;
    return (await exchange(payload, Date.now() + timeoutMs)).response;
  }

  async function askHookEntry(payload, deadline) {
    if (!Array.isArray(hookEntryCommand) || hookEntryCommand.length === 0) {
      return blockedBecause("autorun daemon unreachable");
    }
    return new Promise((resolve) => {
      let stdout = "";
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        child.kill();
        resolve(value);
      };
      let child;
      try {
        child = spawn(hookEntryCommand[0], hookEntryCommand.slice(1), {
          stdio: ["pipe", "pipe", "ignore"],
        });
      } catch {
        resolve(blockedBecause("autorun daemon and hook entry both unreachable"));
        return;
      }
      const timer = setTimeout(
        // A timeout is usually a cold start, as in hook_entry's own timeout.
        () => finish(blockedBecause("hook entry timed out", true)),
        Math.max(deadline - Date.now(), 0),
      );
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
      child.on("error", () =>
        finish(blockedBecause("autorun daemon and hook entry both unreachable")),
      );
      child.on("close", (code) => {
        // Silence is consent. hook_entry.py exits 0 and writes nothing when a
        // tool is allowed; only a decision puts JSON on stdout. Handing "" to
        // JSON.parse throws, and treating that throw as a malfunction turned
        // every allow routed through this fallback into a deny reading
        // "hook entry returned an invalid response". The fallback runs whenever
        // the daemon is unreachable -- a restart, an install, a crash -- so a
        // routine blip blocked every command the user ran.
        //
        // Do not "simplify" this back to a bare JSON.parse: the empty case and
        // the unparseable case must stay distinct.
        if (stdout.trim() === "") {
          // Exit 2 carries a deny whose reason went to stderr (issue #4669
          // workaround), and stderr is not captured here, so a silent exit 2
          // is a decision we cannot read rather than an absent one.
          finish(
            code === 2
              ? blockedBecause("hook entry denied without a readable reason")
              : null,
          );
          return;
        }
        try {
          finish(JSON.parse(stdout));
        } catch {
          // Output that is present but unparseable is a real malfunction: a
          // permission gate that cannot read its own verdict must not fail open.
          finish(blockedBecause("hook entry returned an invalid response"));
        }
      });
      // A hook entry may exit before reading stdin (an allow needs no input).
      // Writing to its closed pipe then raises EPIPE on child.stdin, and an
      // unhandled stream error takes down the whole extension process: Bun on
      // Linux did so right after this fallback had answered correctly. The
      // verdict comes from the exit status and stdout in "close" above, so a
      // failed write carries no information of its own.
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify({ ...payload, cli_type: cliType }) + "\n");
    });
  }

  async function askToolGate(payload) {
    if (autorunDisabled()) return null;
    // One deadline for both routes, so a tool call waits at most timeoutMs.
    const deadline = Date.now() + timeoutMs;
    const { response, connected } = await exchange(payload, deadline);
    if (response) return response;
    if (connected) return didNotAnswer();
    return askHookEntry(payload, deadline);
  }

  async function runCommandResponse(command, cwd, sessionId, metadata = {}) {
    // Strip any spelling of the ar prefix, including a bare "/ar" (Pi's
    // registered command with no arguments), so every form reaches the
    // dispatcher as "ar:<name>" and an empty name gets help, not silence.
    const name = String(command ?? "").trim().replace(/^\/?ar(?:[:\- ]\s*|$)/i, "");
    const response = await askDaemon({
      ...metadata,
      hook_event_name: "UserPromptSubmit",
      session_id: sessionId,
      prompt: "ar:" + name,
      cwd: String(cwd ?? ""),
    });
    if (response) return response;
    if (autorunDisabled()) {
      return { systemMessage: "autorun is stood down by AUTORUN_DISABLE; unset it to use /ar commands." };
    }
    return {
      systemMessage:
        "autorun daemon unreachable; command dispatch is fail-open. " +
        "Run `autorun --restart-daemon`, then retry.",
    };
  }

  async function runCommand(command, cwd, sessionId) {
    return responseMessage(await runCommandResponse(command, cwd, sessionId));
  }

  return { askDaemon, askToolGate, runCommand, runCommandResponse };
}
