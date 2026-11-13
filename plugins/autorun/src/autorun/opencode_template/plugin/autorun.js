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

async function logClient(client, directory, level, message) {
  if (typeof client?.app?.log !== "function") return
  try {
    await client.app.log({
      query: { directory },
      body: { service: "autorun", level, message },
    })
  } catch {
    // Logging is best-effort and must never write to the plugin process streams.
  }
}

async function surfaceStopDenial(client, sessionID, directory, reason) {
  const continuation = {
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
  }
  if (typeof client?.session?.promptAsync === "function") {
    try {
      await client.session.promptAsync(continuation)
      return
    } catch (error) {
      await logClient(
        client,
        directory,
        "warn",
        `OpenCode promptAsync continuation failed: ${error instanceof Error ? error.message : String(error)}`,
      )
    }
  }
  if (typeof client?.session?.prompt === "function") {
    // prompt() resolves only when the whole turn ends. Awaiting it would hold
    // this idle handler for that long, so a failure is handled when it arrives.
    Promise.resolve()
      .then(() => client.session.prompt(continuation))
      .catch(async (error) => {
        await logClient(
          client,
          directory,
          "warn",
          `OpenCode prompt continuation failed: ${error instanceof Error ? error.message : String(error)}`,
        )
        await warnCannotContinue(client, directory, reason)
      })
    return
  }
  await warnCannotContinue(client, directory, reason)
}

async function warnCannotContinue(client, directory, reason) {
  if (typeof client?.tui?.showToast === "function") {
    try {
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
    } catch (error) {
      await logClient(
        client,
        directory,
        "warn",
        `OpenCode Stop warning toast failed: ${error instanceof Error ? error.message : String(error)}`,
      )
    }
  }
  await logClient(
    client,
    directory,
    "warn",
    `Stop blocked but this OpenCode client cannot continue the session: ${reason}`,
  )
}

export const AutorunPlugin = async ({ serverUrl, directory, client }) => {
  // A session's parent does not change. Eight entries bound retained session
  // ids; Map insertion order supplies the LRU. A failed SDK lookup covers this
  // prompt plus the next two, then one later prompt retries.
  const promptAuthors = new Map()
  const promptAuthorCacheLimit = 8
  const failedLookupPromptCount = 3

  function cachePromptAuthor(sessionID, entry) {
    promptAuthors.delete(sessionID)
    promptAuthors.set(sessionID, entry)
    while (promptAuthors.size > promptAuthorCacheLimit) {
      promptAuthors.delete(promptAuthors.keys().next().value)
    }
    return entry
  }

  function cachedPromptAuthor(sessionID) {
    const entry = promptAuthors.get(sessionID)
    if (entry) cachePromptAuthor(sessionID, entry)
    return entry
  }

  async function promptAuthor(sessionID, parts) {
    // OpenCode marks compaction, plugin-injected parts, and autorun's own Stop
    // continuation as synthetic. Synthetic evidence outranks session shape.
    if (hasSyntheticPart(parts)) return "automation"
    if (!sessionID || typeof client?.session?.get !== "function") return "automation"

    const cached = cachedPromptAuthor(sessionID)
    if (cached?.author) {
      if (cached.author !== "automation") return cached.author
      if (cached.promptsRemaining > 0) {
        cached.promptsRemaining -= 1
        return "automation"
      }
      promptAuthors.delete(sessionID)
    } else if (cached?.lookup) {
      try {
        return await cached.lookup
      } catch {
        cachePromptAuthor(sessionID, {
          author: "automation",
          promptsRemaining: failedLookupPromptCount - 1,
        })
        return "automation"
      }
    }

    const lookup = client.session
      .get({ path: { id: sessionID }, query: { directory: String(directory ?? "") } })
      .then((result) => {
        const session = sessionInfo(result)
        if (!session || typeof session !== "object") throw new Error("session lookup returned no session")
        return session.parentID ? "subagent" : "user"
      })
    cachePromptAuthor(sessionID, { lookup })
    try {
      const author = await lookup
      cachePromptAuthor(sessionID, { author })
      return author
    } catch {
      cachePromptAuthor(sessionID, {
        author: "automation",
        promptsRemaining: failedLookupPromptCount - 1,
      })
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
      if (event?.type === "session.deleted") {
        const sessionID = properties.sessionID ?? properties.id ?? properties.info?.id
        if (sessionID) promptAuthors.delete(String(sessionID))
        return
      }
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
