// OpenCode adapter for autorun's shared in-process daemon transport.
// Policy and command parsing remain in Python; this file maps OpenCode events
// to portable hook frames and native veto behavior.

import { createDaemonBridge, denialReason } from "./daemon-client.mjs"

const bridge = createDaemonBridge({
  cliType: "opencode",
  socketPath: __AUTORUN_SOCKET__,
  portFile: __AUTORUN_PORT_FILE__,
  hookEntryCommand: __AUTORUN_HOOK_ENTRY_COMMAND__,
})

// The model calls this through the `autorun` tool, so it runs as the model:
// commands that change permissions answer that only the user can run them.
export async function runArCommand(command, directory, sessionId) {
  return bridge.runCommand(command, directory, sessionId, {}, "model")
}

export const AutorunPlugin = async ({ serverUrl, directory }) => {
  await bridge.askDaemon({
    hook_event_name: "OpenCodeAttach",
    server_url: String(serverUrl ?? ""),
    cwd: String(directory ?? ""),
  })

  const hooks = {
    "tool.execute.before": async (input, output) => {
      const response = await bridge.askToolGate({
        hook_event_name: "PreToolUse",
        session_id: input.sessionID,
        tool_name: input.tool,
        tool_input: output?.args,
        cwd: String(directory ?? ""),
      })
      const reason = denialReason(response)
      if (reason) throw new Error(reason)
    },

    event: async ({ event }) => {
      if (event?.type !== "todo.updated") return
      const properties = event.properties ?? {}
      const todos = properties.todos
      // No session id means no session to attribute the list to; sending ""
      // would file it under a per-process key every session shares.
      if (!properties.sessionID || !Array.isArray(todos)) return
      await bridge.askDaemon({
        hook_event_name: "PostToolUse",
        session_id: String(properties.sessionID),
        tool_name: "todowrite",
        tool_input: { todos },
        tool_result: JSON.stringify({ todos }),
        cwd: String(directory ?? ""),
      })
    },

    dispose: async () => {
      await bridge.askDaemon({
        hook_event_name: "OpenCodeDetach",
        cwd: String(directory ?? ""),
      })
    },
  }

  // OpenCode supplies this package inside its Bun process. Bare-Bun transport
  // tests do not, so command registration is optional while tool veto remains
  // available.
  try {
    const { tool } = await import("@opencode-ai/plugin")
    hooks.tool = {
      autorun: tool({
        description:
          "Run an autorun control command: st (status), allow/justify/find, " +
          "ok/no/blocks/clear guards, task, cache, planexport, stop/estop, help.",
        args: {
          command: tool.schema
            .string()
            .describe("command and arguments, e.g. 'st' or 'ok git push 5m'"),
        },
        async execute({ command }, context) {
          return runArCommand(command, directory, context?.sessionID)
        },
      }),
    }
  } catch {
    // The veto is independent of optional command-tool packaging.
  }

  return hooks
}
