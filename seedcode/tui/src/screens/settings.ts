import type { BackendBridge } from "../backend.js"
import { confirm, input, promptOrCancel, select } from "../theme.js"

type Settings = {
  theme: string
  streamIdleTimeout: number
  maxTokens: number
  promptProfile: string
  autoFailover: boolean
  ollamaKeepAlive: string
}

export async function settingsScreen(backend: BackendBridge): Promise<void> {
  while (true) {
    const settings = await backend.call<Settings>("settings.get")
    const action = await promptOrCancel(() =>
      select({
        message: "Settings and configuration",
        choices: [
          { name: `Theme  ${settings.theme}`, value: "theme" },
          { name: `Stream idle timeout  ${settings.streamIdleTimeout}s`, value: "timeout" },
          { name: `Generation limit  ${settings.maxTokens} tokens`, value: "tokens" },
          { name: `Model profile  ${settings.promptProfile}`, value: "profile" },
          { name: `Automatic failover  ${settings.autoFailover ? "On" : "Off"}`, value: "failover" },
          { name: `Ollama keep-alive  ${settings.ollamaKeepAlive}`, value: "keepAlive" },
          { name: "Back", value: "back" },
        ],
      }),
    )
    if (!action || action === "back") return
    if (action === "theme") {
      const theme = await promptOrCancel(() =>
        select({
          message: "Theme",
          choices: ["seed", "forest", "ocean", "dusk", "ember", "mono"].map((value) => ({
            name: value,
            value,
          })),
        }),
      )
      if (theme) await backend.call("settings.update", { theme })
    }
    if (action === "timeout" || action === "tokens") {
      const timeout = action === "timeout"
      const value = await promptOrCancel(() => input({
        message: timeout ? "Maximum seconds without stream data (1-1800)" : "Maximum generation tokens",
        default: String(timeout ? settings.streamIdleTimeout : settings.maxTokens),
        validate: (text) => /^\d+$/.test(text) || "Enter a whole number",
      }))
      if (value) await backend.call("settings.update", { [timeout ? "streamIdleTimeout" : "maxTokens"]: Number(value) })
    }
    if (action === "profile") {
      const promptProfile = await promptOrCancel(() =>
        select({
          message: "Model execution profile",
          choices: ["auto", "small", "standard", "advanced"].map((value) => ({
            name: value,
            value,
          })),
        }),
      )
      if (promptProfile) await backend.call("settings.update", { promptProfile })
    }
    if (action === "failover") {
      const autoFailover = await promptOrCancel(() => confirm({ message: "Allow automatic provider failover?", default: settings.autoFailover }))
      if (autoFailover !== undefined) await backend.call("settings.update", { autoFailover })
    }
    if (action === "keepAlive") {
      const ollamaKeepAlive = await promptOrCancel(() => input({ message: "Ollama keep-alive", default: settings.ollamaKeepAlive }))
      if (ollamaKeepAlive) await backend.call("settings.update", { ollamaKeepAlive })
    }
  }
}