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

export async function runArCommand(command, directory) {
  return bridge.runCommand(command, directory, undefined)
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
        async execute({ command }) {
          return runArCommand(command, directory)
        },
      }),
    }
  } catch {
    // The veto is independent of optional command-tool packaging.
  }

  return hooks
}
