// Shared in-process transport for JavaScript/TypeScript harness adapters.
//
// Policy stays in the Python daemon. This module owns only newline-delimited
// JSON transport, bounded hook-entry fallback, and response extraction.

import { existsSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { spawn } from "node:child_process";

const PROTOCOL_VERSION = 1;

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

function blockedBecause(message) {
  return {
    hookSpecificOutput: {
      permissionDecision: "deny",
      permissionDecisionReason:
        `[autorun] ${message}. Blocking tool use to avoid fail-open. ` +
        "Run `autorun --restart-daemon` or `autorun --install --force`, then retry.",
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
  async function askDaemon(payload) {
    const target = daemonTarget(socketPath, portFile);
    if (target === null) return null;
    const frame =
      JSON.stringify({
        ...payload,
        cli_type: cliType,
        protocol_version: PROTOCOL_VERSION,
        ...(inprocessCapabilities.length > 0
          ? { inprocess_capabilities: [...inprocessCapabilities] }
          : {}),
        _pid: process.pid,
      }) + "\n";

    return new Promise((resolve) => {
      let settled = false;
      let text = "";
      const finish = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        socket.destroy();
        resolve(value);
      };
      const socket = createConnection(target);
      const timer = setTimeout(() => finish(null), timeoutMs);
      socket.setEncoding("utf8");
      socket.on("connect", () => socket.write(frame));
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

  async function askHookEntry(payload) {
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
        () => finish(blockedBecause("hook entry timed out")),
        timeoutMs,
      );
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
      child.on("error", () =>
        finish(blockedBecause("autorun daemon and hook entry both unreachable")),
      );
      child.on("close", () => {
        try {
          finish(JSON.parse(stdout));
        } catch {
          finish(blockedBecause("hook entry returned an invalid response"));
        }
      });
      child.stdin.end(JSON.stringify({ ...payload, cli_type: cliType }) + "\n");
    });
  }

  async function askToolGate(payload) {
    return (await askDaemon(payload)) ?? askHookEntry(payload);
  }

  async function runCommandResponse(command, cwd, sessionId) {
    // Strip any spelling of the ar prefix, including a bare "/ar" (Pi's
    // registered command with no arguments), so every form reaches the
    // dispatcher as "ar:<name>" and an empty name gets help, not silence.
    const name = String(command ?? "").trim().replace(/^\/?ar(?:[:\- ]\s*|$)/i, "");
    const response = await askDaemon({
      hook_event_name: "UserPromptSubmit",
      session_id: sessionId,
      prompt: "ar:" + name,
      cwd: String(cwd ?? ""),
    });
    if (response) return response;
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
