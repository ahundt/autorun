// Shared in-process transport for JavaScript/TypeScript harness adapters.
//
// Decisions stay in Python: the daemon normally, the hook client when degraded.
// This module owns JSON transport, bounded fallback, and response extraction.

import { existsSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { dirname, join } from "node:path";
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
      // Loopback reaches every local process, so the daemon serves only a
      // client that first sends the token it published beside the port
      // (= ipc.connection_preamble(); test_ipc_tcp_auth.py).
      const token = readFileSync(join(dirname(portFile), "daemon.token"), "utf8").trim();
      return { host: "127.0.0.1", port, preamble: `${token}\n` };
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

// Denial-only copy of autorun.client.RESPONSE_TIMEOUT_GUIDANCE, pinned by tests.
const RESPONSE_TIMEOUT_GUIDANCE = "Blocking this tool call because the autorun daemon took the request but did not answer in time and a local decision is unavailable or overload_policy=block is selected. Wait and retry when autorun answers again; do not restart the daemon. If this persists, tell the user; AUTORUN_DISABLE=1 at harness launch stands autorun down.";

export function denialReason(response) {
  if (!response) return null;
  const specific = response.hookSpecificOutput ?? {};
  const decision = specific.permissionDecision ?? response.decision;
  if (decision !== "deny" && decision !== "block") return null;
  return specific.permissionDecisionReason || response.reason || "blocked by autorun";
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

export function degradedNotice(response) {
  const message = responseMessage(response);
  return !denialReason(response) && message.includes("[AR_EVENT_V1:degraded_decision]") ? message : "";
}

// Data derived from config.DEFAULT_INTEGRATIONS, pinned by test_review_bridge_screen_data_pinned.
const CRITICAL_GUARD_TOKENS = [["rm"], ["rm", "-rf"], ["git", "reset", "--hard"], ["git", "checkout", "."], ["git", "checkout", "--"], ["git", "checkout"], ["git", "restore"], ["git", "stash", "drop"], ["git", "stash", "clear"], ["git", "clean", "-f"], ["git", "reset", "HEAD~"], ["git", "add", "-A"], ["git", "add", "."], ["dd", "if="], ["mkfs"], ["fdisk"], ["tmutil"], ["bash"], ["dash"], ["fish"], ["ksh"], ["sh"], ["su"], ["zsh"], ["sed"], ["awk"], ["find"], ["git", "filter-repo"], ["git", "filter-branch"], ["bfg"], ["git", "rebase", "-i"], ["git", "rebase", "--interactive"], ["git", "push", "--force"], ["git", "push", "-f"], ["git", "push"], ["gh", "pr", "create"], ["gh", "pr", "merge", "--squash"], ["gh", "pr", "merge"], ["gh", "release", "create"], ["gh", "repo", "create"], ["gh", "issue", "edit"], ["gh", "pr", "edit"], ["gh", "repo", "edit"], ["gh", "pr", "comment"], ["gh", "issue", "comment"], ["gh", "issue", "create"], ["npm", "version"], ["yarn", "version"], ["pnpm", "version"], ["cargo", "set-version"], ["poetry", "version"], ["uv", "version"], ["hatch", "version"], ["bump2version"], ["bumpversion"], ["mvn", "versions:set"], ["npm", "publish"], ["yarn", "publish"], ["pnpm", "publish"], ["cargo", "publish"], ["poetry", "publish"], ["uv", "publish"], ["hatch", "publish"], ["flit", "publish"], ["twine", "upload"], ["gem", "push"], ["mvn", "deploy"], ["gradle", "publish"], ["dotnet", "nuget", "push"], ["docker", "push"]];
const SHELL_NAME_HIDING_FEATURES = ["$'", "$\"", "eval", "base64", "xxd", "printf", "<(", ">("];
const SHELL_TOOL_NAMES = ["bash", "exec_command", "execute_command", "local_shell", "run_command", "run_shell_command", "run_terminal_cmd", "run_terminal_command", "sh", "shell", "shell_command", "terminal_exec", "zsh"];
const BASH_TOOLS = ["Bash", "bash", "bash_command", "exec_command", "functions.exec_command", "run_command", "run_shell_command", "shell"];

function needsFullGuardCheck(command) {
  // Mirror of config.needs_full_guard_check: alphabetic tokens match whole words.
  if (SHELL_NAME_HIDING_FEATURES.some(feature => command.includes(feature))) return true;
  const plain = command.replace(/\\\n|['"\\]/g, "");
  const words = new Set((plain + " " + command).match(/[A-Za-z]+/g) ?? []);
  return CRITICAL_GUARD_TOKENS.some(tokens => tokens.every(
    token => /^[A-Za-z]+$/.test(token) ? words.has(token) : plain.includes(token)));
}
const DEGRADED_FAILURE_CODES = new Set([
  "daemon_dispatch_timeout", "daemon_dispatch_contained", "daemon_dispatch_stalled",
  "state_lock_contended", "state_evaluation_failed", "daemon_failure",
  "daemon_response_timeout", "daemon_unavailable_or_timeout",
]);

function failureCode(response) {
  const code = response?._autorun_failure_code;
  return DEGRADED_FAILURE_CODES.has(code) ? code : null;
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
        [HOOK_DEADLINE_PAYLOAD_KEY]: (Date.now() + Math.max(0, deadline - performance.now())) / 1000,
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
      const timer = setTimeout(() => finish(null), Math.max(deadline - performance.now(), 0));
      socket.setEncoding("utf8");
      socket.on("connect", () => {
        connected = true;
        socket.write((target.preamble ?? "") + frame);
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
    const { response } = await exchange(payload, performance.now() + timeoutMs);
    if (!response) return response;
    const { _autorun_failure_code, ...verdict } = response;
    return verdict;
  }

  function lastResort(payload, failureCode) {
    // Python owns full guards; the literal screen also lets ordinary shells proceed.
    // No config-file reader lives in this bridge: this last resort follows
    // AUTORUN_OVERLOAD_POLICY only; the Python route also reads the config file.
    const policy = process.env.AUTORUN_OVERLOAD_POLICY ?? "critical-guards";
    const name = payload.tool_name ?? payload.toolName ?? payload.toolCall?.name ?? "";
    const shell = BASH_TOOLS.includes(name) || SHELL_TOOL_NAMES.includes(name.split(/__|[./:]/).at(-1).toLowerCase());
    const needsCheck = shell && [payload.tool_input, payload.toolInput, payload.toolCall?.args].some(inputs => {
      for (const key of ["command", "cmd", "CommandLine"]) {
        const value = inputs?.[key];
        const command = typeof value === "string" ? value :
          Array.isArray(value) ? value.flat(Infinity).map(String).join(" ") : "";
        if (command) return needsFullGuardCheck(command);
      }
      return false;
    });
    const deny = policy === "block" || (policy !== "allow" && needsCheck);
    return { hookSpecificOutput: {
      permissionDecision: deny ? "deny" : "allow",
      permissionDecisionReason: `[autorun] [AR_EVENT_V1:${failureCode}] [AR_EVENT_V1:degraded_decision] ` +
        (policy === "allow"
          ? "Autorun could not answer and overload_policy=allow permits this call without built-in destructive-command guards, /ar:no rules, /ar:ok grants, the file policy or task and plan gating until autorun answers again; Do not restart the daemon."
          : policy === "block"
          ? "Autorun could not answer and overload_policy=block denies this tool call; wait and retry when it recovers; do not restart the daemon."
          : needsCheck
          ? "Autorun is degraded and its critical guards could not finish; wait and retry this shell call when it recovers; do not restart the daemon." +
            (failureCode === "daemon_response_timeout" ? " " + RESPONSE_TIMEOUT_GUIDANCE : "")
          : "Autorun could not answer, so the quick screen found no possible built-in destructive-command guard while /ar:no rules, /ar:ok grants, the file policy and task and plan gating are not checked until autorun answers again; Do not restart the daemon.").replaceAll("/ar:", cliType === "opencode" ? "/ar-" : "/ar "),
    }};
  }

  async function askHookEntry(payload, deadline, failureCode = "daemon_unavailable_or_timeout") {
    const failed = () => lastResort(payload, failureCode);
    if (!Array.isArray(hookEntryCommand) || hookEntryCommand.length === 0) {
      return failed();
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
        resolve(failed());
        return;
      }
      const timer = setTimeout(
        // A timeout is usually a cold start, as in hook_entry's own timeout.
        () => finish(failed()),
        Math.max(deadline - performance.now(), 0),
      );
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
      child.on("error", () =>
        finish(failed()),
      );
      child.on("close", () => {
        // Local-only mode always emits a verdict, including an allow notice.
        // Silence cannot prove the guards ran; use the bounded last resort.
        if (stdout.trim() === "") {
          finish(failed());
          return;
        }
        try {
          const parsed = JSON.parse(stdout);
          const decision = parsed?.hookSpecificOutput?.permissionDecision ?? parsed?.decision;
          const { _autorun_failure_code, ...verdict } = parsed ?? {};
          finish(["allow", "approve", "deny", "block"].includes(decision) ? verdict : failed());
        } catch {
          // Output that is present but unparseable is a real malfunction: a
          // permission gate that cannot read its own verdict must not fail open.
          finish(failed());
        }
      });
      // A hook entry may exit before reading stdin (an allow needs no input).
      // Writing to its closed pipe then raises EPIPE on child.stdin, and an
      // unhandled stream error takes down the whole extension process: Bun on
      // Linux did so right after this fallback had answered correctly. The
      // verdict comes from the exit status and stdout in "close" above, so a
      // failed write carries no information of its own.
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify({ ...payload, cli_type: cliType,
        _autorun_degraded_check: true,
        _autorun_failure_code: failureCode,
        [HOOK_DEADLINE_PAYLOAD_KEY]: (Date.now() + Math.max(0, deadline - performance.now())) / 1000,
      }) + "\n");
    });
  }

  async function askToolGate(payload) {
    if (autorunDisabled()) return null;
    // One deadline for both routes, so a tool call waits at most timeoutMs.
    const deadline = performance.now() + timeoutMs;
    // Reserve interpreter startup and a bounded check inside the same budget.
    const { response, connected } = await exchange(payload, deadline - Math.min(500, timeoutMs / 2));
    const code = failureCode(response);
    if (response && !code) {
      const { _autorun_failure_code, ...verdict } = response;
      return verdict;
    }
    return askHookEntry(payload, deadline, code ?? (connected
      ? "daemon_response_timeout" : "daemon_unavailable_or_timeout"));
  }

  // promptAuthor is who wrote the command: "user", "model" or "automation"
  // (= autorun.core.PROMPT_AUTHORS). Commands that change permissions run only
  // for "user", so a caller that cannot say is taken as "automation".
  async function runCommandResponse(command, cwd, sessionId, metadata = {}, promptAuthor = "automation") {
    // Strip any spelling of the ar prefix, including a bare "/ar" (Pi's
    // registered command with no arguments), so every form reaches the
    // dispatcher as "ar:<name>" and an empty name gets help, not silence.
    const name = String(command ?? "").trim().replace(/^\/?ar(?:[:\- ]\s*|$)/i, "");
    const response = await askDaemon({
      ...metadata,
      hook_event_name: "UserPromptSubmit",
      session_id: sessionId,
      prompt: "ar:" + name,
      prompt_author: promptAuthor,
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

  async function runCommand(command, cwd, sessionId, metadata = {}, promptAuthor = "automation") {
    return responseMessage(await runCommandResponse(command, cwd, sessionId, metadata, promptAuthor));
  }

  return { askDaemon, askToolGate, runCommand, runCommandResponse };
}
