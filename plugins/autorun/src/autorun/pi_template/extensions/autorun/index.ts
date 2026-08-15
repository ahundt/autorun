import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { createDaemonBridge, denialReason, responseMessage } from "./daemon-client.mjs";

const TASK_STATUSES = ["pending", "in_progress", "completed", "deleted"];
const TaskCreateParameters = {
  type: "object",
  properties: {
    subject: { type: "string", description: "Short imperative task title" },
    description: { type: "string", description: "Concrete completion requirements" },
    activeForm: { type: "string", description: "Present-progress activity label" },
  },
  required: ["subject", "description", "activeForm"],
  additionalProperties: false,
};
const TaskUpdateParameters = {
  type: "object",
  properties: {
    taskId: { type: "string", description: "ID returned by TaskCreate" },
    subject: { type: "string" },
    description: { type: "string" },
    activeForm: { type: "string" },
    status: { type: "string", enum: TASK_STATUSES },
    addBlockedBy: { type: "array", items: { type: "string" } },
    addBlocks: { type: "array", items: { type: "string" } },
  },
  required: ["taskId"],
  additionalProperties: false,
};
const TaskListParameters = {
  type: "object",
  properties: {},
  additionalProperties: false,
};

const bridge = createDaemonBridge({
  cliType: __AUTORUN_CLI_TYPE__,
  socketPath: __AUTORUN_SOCKET__,
  portFile: __AUTORUN_PORT_FILE__,
  hookEntryCommand: __AUTORUN_HOOK_ENTRY_COMMAND__,
  inprocessCapabilities: ["response_projection_v2", "task_operations_v1"],
});

export function createTaskId(): string {
  const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "");
  return random ? `pi-${random}` : `pi-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function textResult(text: string, details: Record<string, unknown>) {
  return { content: [{ type: "text" as const, text }], details };
}

function confirmedTaskText(task: Record<string, any>): string {
  const subject = String(task.subject ?? "").trim();
  return `Task ${task.id} [${task.status}]${subject ? ` ${subject}` : ""}`;
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw signal.reason ?? new Error("Task operation aborted");
}

function sessionId(ctx: ExtensionContext): string {
  return ctx.sessionManager.getSessionId();
}

function boundedTranscript(ctx: ExtensionContext): unknown[] {
  const messages = ctx.sessionManager.buildSessionContext().messages;
  const limit = 64 * 1024;
  const recent: unknown[] = [];
  let bytes = 2;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const encoded = JSON.stringify(message);
    const messageBytes = Buffer.byteLength(encoded, "utf8") + (recent.length > 0 ? 1 : 0);
    if (bytes + messageBytes > limit) break;
    recent.unshift(message);
    bytes += messageBytes;
  }
  return recent;
}

function frame(ctx: ExtensionContext, event: string): Record<string, unknown> {
  return {
    hook_event_name: event,
    session_id: sessionId(ctx),
    transcript_path: ctx.sessionManager.getSessionFile(),
    session_transcript: boundedTranscript(ctx),
    cwd: ctx.cwd,
  };
}

export default function autorunPiExtension(pi: ExtensionAPI) {
  let continuationInFlight = false;

  async function reprojectTaskReceipts(ctx: ExtensionContext): Promise<void> {
    const taskRecords: Record<string, unknown>[] = [];
    for (const entry of ctx.sessionManager.getBranch()) {
      if (entry.type !== "message") continue;
      const message: any = entry.message;
      if (message.role !== "toolResult") continue;
      if (message.toolName !== "TaskCreate" && message.toolName !== "TaskUpdate") continue;
      const snapshot = message.details?.taskSnapshot;
      if (snapshot && typeof snapshot === "object") taskRecords.push(snapshot);
    }
    await bridge.askDaemon({
      ...frame(ctx, "AutorunOperation"),
      inprocess_operation: "task_reproject_v1",
      task_records: taskRecords,
    });
  }

  function deliverCommandResponse(response: Record<string, any>, ctx: ExtensionContext): void {
    const message = responseMessage(response);
    if (response?._autorun_bridge?.starts_agent_turn === true) {
      pi.sendMessage(
        { customType: "autorun-command", content: message, display: true },
        { triggerTurn: true, ...(ctx.isIdle() ? {} : { deliverAs: "followUp" }) },
      );
      return;
    }
    if (ctx.mode === "print") {
      process.stdout.write(message + "\n");
      return;
    }
    ctx.ui.notify(message, "info");
  }

  pi.registerTool({
    name: "TaskCreate",
    label: "Create task",
    description: "Create one tracked autorun task and return its stable ID.",
    parameters: TaskCreateParameters as any,
    executionMode: "sequential",
    async execute(_callId, params: any, signal) {
      throwIfAborted(signal);
      const task = { id: createTaskId(), ...params, status: "pending" };
      return textResult(`Created task ${task.id}: ${task.subject}`, { task });
    },
  });

  pi.registerTool({
    name: "TaskUpdate",
    label: "Update task",
    description: "Update a tracked autorun task's fields, status, or dependencies.",
    parameters: TaskUpdateParameters as any,
    executionMode: "sequential",
    async execute(_callId, params: any, signal) {
      throwIfAborted(signal);
      return textResult(`Updated task ${params.taskId}`, {
        taskId: params.taskId,
        updates: { ...params },
      });
    },
  });

  pi.registerTool({
    name: "TaskList",
    label: "List tasks",
    description: "List tracked autorun tasks for the current Pi session.",
    parameters: TaskListParameters as any,
    executionMode: "sequential",
    async execute(_callId, _params, signal, _onUpdate, ctx) {
      throwIfAborted(signal);
      const response = await bridge.askDaemon({
        ...frame(ctx, "AutorunOperation"),
        inprocess_operation: "task_list_v1",
      });
      throwIfAborted(signal);
      const operation = response?._autorun_bridge;
      if (operation?.operation !== "task_list_v1" || !Array.isArray(operation.tasks)) {
        return textResult("Unable to list autorun tasks: daemon returned no task projection.", {
          tasks: [], total: 0, truncated: false, error: "task projection unavailable",
        });
      }
      const lines = operation.tasks.map(
        (task: any) => `${task.id} [${task.status}] ${task.subject}`,
      );
      return textResult(lines.join("\n") || "No tracked tasks", operation);
    },
  });

  pi.registerCommand("ar", {
    description: "Run an autorun control command, for example /ar st or /ar go <task>",
    handler: async (args, ctx) => {
      const response = await bridge.runCommandResponse(args, ctx.cwd, sessionId(ctx));
      deliverCommandResponse(response, ctx);
    },
  });

  pi.on("input", async (event, ctx) => {
    if (!/^\/?ar[:\-]/i.test(event.text.trim())) return { action: "continue" };
    const response = await bridge.runCommandResponse(event.text, ctx.cwd, sessionId(ctx));
    deliverCommandResponse(response, ctx);
    return { action: "handled" };
  });

  pi.on("session_start", async (event, ctx) => {
    continuationInFlight = false;
    await reprojectTaskReceipts(ctx);
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
    const taskResult =
      event.toolName === "TaskCreate" || event.toolName === "TaskUpdate"
        ? event.details
        : undefined;
    const response = await bridge.askDaemon({
      ...frame(ctx, "PostToolUse"),
      tool_name: event.toolName,
      tool_input: event.input,
      tool_result: taskResult ?? {
        content: event.content,
        details: event.details,
        isError: event.isError,
        usage: event.usage,
      },
    });
    const content = responseMessage(response);
    const taskSnapshot = response?._autorun_bridge?.task_snapshot;
    if (taskResult && !taskSnapshot) {
      return {
        content: [
          ...event.content,
          {
            type: "text",
            text:
              `autorun did not confirm ${event.toolName}; task state may be unchanged. ` +
              "Run `autorun --restart-daemon`, then retry the task operation.",
          },
        ],
        details: event.details,
        isError: true,
        usage: event.usage,
      };
    }
    if (!content && !taskSnapshot) return undefined;
    const confirmedContent = taskSnapshot
      ? [{ type: "text" as const, text: confirmedTaskText(taskSnapshot) }]
      : event.content;
    return {
      content: content
        ? [...confirmedContent, { type: "text", text: content }]
        : confirmedContent,
      details: taskSnapshot ? { ...event.details, taskSnapshot } : event.details,
      isError: event.isError,
      usage: event.usage,
    };
  });

  pi.on("session_before_compact", async (_event, ctx) => {
    await bridge.askDaemon(frame(ctx, "PreCompact"));
  });

  pi.on("session_compact", async (_event, ctx) => {
    continuationInFlight = false;
    await bridge.askDaemon(frame(ctx, "PostCompact"));
  });

  pi.on("session_tree", async (_event, ctx) => {
    continuationInFlight = false;
    await reprojectTaskReceipts(ctx);
  });

  pi.on("agent_start", async () => {
    continuationInFlight = false;
  });

  pi.on("agent_settled", async (_event, ctx) => {
    if (continuationInFlight || ctx.hasPendingMessages()) return;
    const response = await bridge.askDaemon(frame(ctx, "Stop"));
    if (response?.decision !== "block") return;
    const reason = responseMessage(response);
    if (!reason || ctx.hasPendingMessages()) return;
    continuationInFlight = true;
    pi.sendMessage(
      { customType: "autorun-continuation", content: reason, display: false },
      { triggerTurn: true, ...(ctx.isIdle() ? {} : { deliverAs: "followUp" }) },
    );
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    continuationInFlight = false;
    await bridge.askDaemon(frame(ctx, "SessionEnd"));
  });
}
