import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { createDaemonBridge, denialReason, responseMessage } from "./daemon-client.mjs";

const bridge = createDaemonBridge({
  cliType: "pi",
  socketPath: __AUTORUN_SOCKET__,
  portFile: __AUTORUN_PORT_FILE__,
  hookEntryCommand: __AUTORUN_HOOK_ENTRY_COMMAND__,
});

function sessionId(ctx: ExtensionContext): string {
  return ctx.sessionManager.getSessionId();
}

function frame(ctx: ExtensionContext, event: string): Record<string, unknown> {
  return {
    hook_event_name: event,
    session_id: sessionId(ctx),
    transcript_path: ctx.sessionManager.getSessionFile(),
    cwd: ctx.cwd,
  };
}

export default function autorunPiExtension(pi: ExtensionAPI) {
  function showCommandResult(message: string, ctx: ExtensionContext): void {
    if (ctx.mode === "print") {
      process.stdout.write(message + "\n");
      return;
    }
    pi.sendMessage({ customType: "autorun", content: message, display: true });
  }

  pi.registerCommand("ar", {
    description: "Run an autorun control command, for example /ar st or /ar go <task>",
    handler: async (args, ctx) => {
      const message = await bridge.runCommand(args, ctx.cwd, sessionId(ctx));
      showCommandResult(message, ctx);
    },
  });

  pi.on("input", async (event, ctx) => {
    if (!/^\/?ar[:\-]/i.test(event.text.trim())) return { action: "continue" };
    const message = await bridge.runCommand(event.text, ctx.cwd, sessionId(ctx));
    showCommandResult(message, ctx);
    return { action: "handled" };
  });

  pi.on("session_start", async (event, ctx) => {
    await bridge.askDaemon({ ...frame(ctx, "SessionStart"), source: event.reason });
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const response = await bridge.askDaemon({
      ...frame(ctx, "UserPromptSubmit"),
      prompt: event.prompt,
    });
    const content = responseMessage(response);
    if (!content) return undefined;
    return { message: { customType: "autorun", content, display: true } };
  });

  pi.on("tool_call", async (event, ctx) => {
    const response = await bridge.askToolGate({
      ...frame(ctx, "PreToolUse"),
      tool_name: event.toolName,
      tool_input: event.input,
    });
    const reason = denialReason(response);
    return reason ? { block: true, reason } : undefined;
  });

  pi.on("tool_result", async (event, ctx) => {
    const response = await bridge.askDaemon({
      ...frame(ctx, "PostToolUse"),
      tool_name: event.toolName,
      tool_input: event.input,
      tool_result: { content: event.content, details: event.details, isError: event.isError },
    });
    const content = responseMessage(response);
    if (!content) return undefined;
    return { content: [...event.content, { type: "text", text: content }] };
  });

  pi.on("agent_settled", async (_event, ctx) => {
    const response = await bridge.askDaemon(frame(ctx, "Stop"));
    if (response?.decision !== "block") return;
    const reason = responseMessage(response);
    if (reason) pi.sendUserMessage(reason);
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    await bridge.askDaemon(frame(ctx, "SessionEnd"));
  });
}
