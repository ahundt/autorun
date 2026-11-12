// OpenCode adapter for autorun's shared in-process daemon transport.
// Policy and command parsing remain in Python; this file maps OpenCode events
// to portable hook frames and native veto behavior.

import { createDaemonBridge, denialReason, responseMessage } from "./daemon-client.mjs"

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

function promptText(parts) {
  return (Array.isArray(parts) ? parts : [])
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n")
}

function hasSyntheticPart(parts) {
  return (Array.isArray(parts) ? parts : []).some((part) => part?.synthetic === true)
}

function sessionInfo(result) {
  return result?.data ?? result
}

function noticePart(message) {
  return {
    type: "text",
    text: `[autorun notice]\n${message}`,
    synthetic: true,
    metadata: { source: "autorun-hook" },
  }
}

async function surfaceStopDenial(client, sessionID, directory, reason) {
  if (typeof client?.session?.promptAsync === "function") {
    try {
      await client.session.promptAsync({
        path: { id: sessionID },
        query: { directory },
        body: {
          parts: [{
            type: "text",
            text: reason,
            synthetic: true,
            metadata: { source: "autorun-stop-gate" },
          }],
        },
      })
      return
    } catch {
      // Older/mismatched clients may expose the method but reject its shape.
    }
  }
  if (typeof client?.tui?.showToast === "function") {
    await client.tui.showToast({
      query: { directory },
      body: {
        title: "Autorun could not continue this session",
        message: `${reason}\n\nThis OpenCode client cannot enforce the continuation; continue manually.`,
        variant: "warning",
        duration: 12000,
      },
    })
    return
  }
  console.error(
    `[autorun] Stop blocked but this OpenCode client cannot continue the session: ${reason}`,
  )
}

export const AutorunPlugin = async ({ serverUrl, directory, client }) => {
  // A session's parent does not change. Cache the in-flight lookup too, so two
  // prompts arriving together still make one SDK request.
  const promptAuthors = new Map()

  async function promptAuthor(sessionID, parts) {
    // OpenCode marks compaction, plugin-injected parts, and autorun's own Stop
    // continuation as synthetic. Synthetic evidence outranks session shape.
    if (hasSyntheticPart(parts)) return "automation"
    if (!sessionID || typeof client?.session?.get !== "function") return "automation"

    let cached = promptAuthors.get(sessionID)
    if (!cached) {
      cached = client.session
        .get({ path: { id: sessionID }, query: { directory: String(directory ?? "") } })
        .then((result) => {
          const session = sessionInfo(result)
          if (!session || typeof session !== "object") throw new Error("session lookup returned no session")
          return session.parentID ? "subagent" : "user"
        })
      promptAuthors.set(sessionID, cached)
    }
    try {
      return await cached
    } catch {
      // Unknown origin must not gain user-only authority. Do not cache a
      // transient SDK failure; a later prompt can retry the lookup.
      if (promptAuthors.get(sessionID) === cached) promptAuthors.delete(sessionID)
      return "automation"
    }
  }

  await bridge.askDaemon({
    hook_event_name: "OpenCodeAttach",
    server_url: String(serverUrl ?? ""),
    cwd: String(directory ?? ""),
  })

  const hooks = {
    "chat.message": async (input, output) => {
      const prompt = promptText(output?.parts)
      const response = await bridge.askDaemon({
        hook_event_name: "UserPromptSubmit",
        session_id: input.sessionID,
        prompt,
        prompt_author: await promptAuthor(input.sessionID, output?.parts),
        cwd: String(directory ?? ""),
      })
      const blocked = denialReason(response)
      if (blocked) throw new Error(blocked)
      const message = responseMessage(response)
      if (message && Array.isArray(output?.parts)) output.parts.push(noticePart(message))
    },

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

    "experimental.session.compacting": async (input, output) => {
      const response = await bridge.askDaemon({
        hook_event_name: "PreCompact",
        session_id: input.sessionID,
        cwd: String(directory ?? ""),
      })
      const message = responseMessage(response)
      if (message && Array.isArray(output?.context)) output.context.push(message)
    },

    event: async ({ event }) => {
      const properties = event?.properties ?? {}
      if (event?.type === "session.idle") {
        if (!properties.sessionID) return
        const response = await bridge.askDaemon({
          hook_event_name: "Stop",
          session_id: String(properties.sessionID),
          cwd: String(directory ?? ""),
        })
        const reason = denialReason(response)
        if (!reason) return
        await surfaceStopDenial(
          client,
          String(properties.sessionID),
          String(directory ?? ""),
          reason,
        )
        return
      }
      if (event?.type === "session.compacted") {
        if (!properties.sessionID) return
        await bridge.askDaemon({
          hook_event_name: "PostCompact",
          session_id: String(properties.sessionID),
          cwd: String(directory ?? ""),
        })
        return
      }
      if (event?.type !== "todo.updated") return
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
          "Run an autorun command: status, help, or task (for example task status). " +
          "This model-callable tool cannot run commands reserved for a verified user prompt.",
        args: {
          command: tool.schema
            .string()
            .describe("command and arguments, e.g. 'status' or 'task status'"),
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
