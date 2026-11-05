import type { BackendBridge } from "../backend.js"
import { confirm, input, password, promptOrCancel, select } from "../theme.js"

type Provider = {
  id: string
  name: string
  status: string
  active: boolean
  hasKey: boolean
  keyRequired: boolean
  configured: boolean
  model: string
  local: boolean
  custom: boolean
  baseUrl: string
  enabled: boolean
  priority: number | null
}

export async function providerScreen(backend: BackendBridge): Promise<void> {
  const providers = await backend.call<Provider[]>("provider.list")
  const selected = await promptOrCancel(() =>
    select({
      message: "AI providers",
      choices: [
        ...providers.map((provider) => ({
          name: `${provider.active ? "> " : "  "}${provider.name}  ${provider.status}${provider.model ? `  ${provider.model}` : ""}`,
          value: provider.id,
          description: provider.local ? "Runs on this machine" : provider.custom ? "User-configured endpoint" : "Cloud provider",
        })),
        { name: "Add custom provider", value: "@add" },
        { name: "Back", value: "@back" },
      ],
      pageSize: 12,
    }),
  )
  if (!selected || selected === "@back") return
  if (selected === "@add") return addProvider(backend)

  const provider = providers.find((item) => item.id === selected)
  if (!provider) return
  while (true) {
    const action = await promptOrCancel(() =>
      select({
        message: `${provider.name}  |  ${provider.status}`,
        choices: [
          { name: "Use this provider", value: "use" },
          { name: provider.hasKey ? "Replace API key" : "Configure API key", value: "key", disabled: !provider.keyRequired },
          { name: "Configure Ollama host", value: "host", disabled: !provider.local },
          { name: "Edit custom provider", value: "edit", disabled: !provider.custom },
          { name: "Test connection", value: "test" },
          { name: provider.enabled ? "Disable custom provider" : "Enable custom provider", value: "toggle", disabled: !provider.custom },
          { name: "Move fallback priority up", value: "moveUp", disabled: !provider.custom || provider.priority === 0 },
          { name: "Move fallback priority down", value: "moveDown", disabled: !provider.custom },
          { name: "Remove custom provider", value: "remove", disabled: !provider.custom },
          { name: "Back", value: "back" },
        ],
      }),
    )
    if (!action || action === "back") return
    if (action === "use") {
      await backend.call("provider.select", { providerId: provider.id })
      console.log(`${provider.name} selected.`)
      return
    }
    if (action === "key") {
      const apiKey = await promptOrCancel(() => password({ message: "API key", mask: "*" }))
      if (apiKey !== undefined) await backend.call("provider.update", { providerId: provider.id, apiKey })
    }
    if (action === "host") {
      const host = await promptOrCancel(() => input({ message: "Ollama host", default: "http://localhost:11434" }))
      if (host) await backend.call("provider.update", { providerId: provider.id, host })
    }
    if (action === "edit") {
      const name = await promptOrCancel(() => input({ message: "Provider name", default: provider.name }))
      if (!name) continue
      const baseUrl = await promptOrCancel(() => input({ message: "Base URL", default: provider.baseUrl }))
      if (!baseUrl) continue
      const model = await promptOrCancel(() => input({ message: "Default model", default: provider.model }))
      if (model === undefined) continue
      await backend.call("provider.update", { providerId: provider.id, name, baseUrl, model })
      return providerScreen(backend)
    }
    if (action === "toggle") {
      await backend.call("provider.update", { providerId: provider.id, enabled: !provider.enabled })
      return providerScreen(backend)
    }
    if (action === "moveUp" || action === "moveDown") {
      await backend.call("provider.move", {
        providerId: provider.id,
        delta: action === "moveUp" ? -1 : 1,
      })
      return providerScreen(backend)
    }
    if (action === "test") {
      const result = await backend.call<{ status: string }>("provider.test", { providerId: provider.id })
      console.log(`Connection: ${result.status}`)
    }
    if (action === "remove") {
      const ok = await promptOrCancel(() => confirm({ message: `Remove ${provider.name}?`, default: false }))
      if (ok) {
        await backend.call("provider.remove", { providerId: provider.id })
        return
      }
    }
  }
}

async function addProvider(backend: BackendBridge) {
  const name = await promptOrCancel(() => input({ message: "Provider name" }))
  if (!name) return
  const baseUrl = await promptOrCancel(() => input({ message: "OpenAI-compatible base URL" }))
  if (!baseUrl) return
  const apiKey = await promptOrCancel(() => password({ message: "API key", mask: "*" }))
  if (apiKey === undefined) return
  const model = await promptOrCancel(() => input({ message: "Default model (optional)" }))
  const added = await backend.call<{ id: string; name: string }>("provider.add", { name, baseUrl, apiKey, model: model || "" })
  await backend.call("provider.select", { providerId: added.id })
  console.log(`${added.name} added and selected.`)
}