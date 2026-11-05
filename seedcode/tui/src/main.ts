import { BackendBridge, runPythonSession } from "./backend.js"
import { agentScreen } from "./screens/agents.js"
import { modelScreen } from "./screens/models.js"
import { providerScreen } from "./screens/providers.js"
import { sessionsScreen } from "./screens/sessions.js"
import { settingsScreen } from "./screens/settings.js"
import { workspaceScreen } from "./screens/workspace.js"
import { brand, error, promptOrCancel, select } from "./theme.js"

async function main() {
  let backend = new BackendBridge()
  try {
    while (true) {
      const state = await backend.call<{
        workspace: string
        provider: string
        model: string
        agent: string
        configured: boolean
      }>("app.status")
      brand(`Workspace: ${state.workspace}`)
      const action = await promptOrCancel(() =>
        select({
          message: "Main menu",
          choices: [
            {
              name: `Start coding session  ${state.provider} / ${state.model || "No model selected"}`,
              value: "start",
              disabled: !state.configured,
            },
            { name: "Select workspace", value: "workspace" },
            { name: "Providers", value: "providers" },
            { name: "Models", value: "models" },
            { name: "Agents", value: "agents" },
            { name: "Saved sessions", value: "sessions" },
            { name: "Settings", value: "settings" },
            { name: "Exit", value: "exit" },
          ],
          pageSize: 10,
        }),
      )
      if (!action || action === "exit") return
      try {
        if (action === "workspace") await workspaceScreen(backend)
        if (action === "providers") await providerScreen(backend)
        if (action === "models") await modelScreen(backend)
        if (action === "agents") await agentScreen(backend)
        if (action === "sessions") await sessionsScreen(backend)
        if (action === "settings") await settingsScreen(backend)
        if (action === "start") {
          const current = await backend.call<{ path: string }>("workspace.current")
          await backend.close()
          await runPythonSession(current.path)
          backend = new BackendBridge()
        }
      } catch (reason) {
        error(reason instanceof Error ? reason.message : String(reason))
      }
    }
  } finally {
    await backend.close()
  }
}

main().catch((reason) => {
  error(reason instanceof Error ? reason.message : String(reason))
  process.exitCode = 1
})