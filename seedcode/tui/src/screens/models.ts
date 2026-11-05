import type { BackendBridge } from "../backend.js"
import { input, promptOrCancel, select } from "../theme.js"

type Model = {
  id: string
  name: string
  detail: string
  isFree: boolean | null
  active: boolean
  capabilities: string[] | null
  capabilitiesKnown: boolean
}

export async function modelScreen(backend: BackendBridge): Promise<void> {
  const providers = await backend.call<Array<{ id: string; name: string; active: boolean }>>("provider.list")
  const active = providers.find((provider) => provider.active)
  if (!active) throw new Error("No active provider")
  const models = await backend.call<Model[]>("model.list", { providerId: active.id })
  while (true) {
    const query = await promptOrCancel(() => input({ message: `Search ${active.name} models (empty shows all)` }))
    if (query === undefined) return
    const filtered = models.filter((model) =>
      `${model.id} ${model.name} ${model.detail}`.toLowerCase().includes(query.toLowerCase()),
    )
    const selected = await promptOrCancel(() =>
      select({
        message: `${active.name} models${query ? ` matching "${query}"` : ""}`,
        choices: [
          ...filtered.map((model) => ({
            name: `${model.active ? "> " : "  "}${model.name}${model.detail ? `  ${model.detail}` : ""}${model.active ? "  ACTIVE" : ""}`,
            value: model.id,
          })),
          { name: "Enter model ID", value: "@custom" },
          { name: "Refresh model list", value: "@refresh" },
          { name: "Back", value: "@back" },
        ],
        pageSize: 14,
      }),
    )
    if (!selected || selected === "@back") return
    if (selected === "@refresh") return modelScreen(backend)
    let modelId = selected
    if (selected === "@custom") {
      const typed = await promptOrCancel(() => input({ message: "Model ID" }))
      if (!typed) continue
      modelId = typed
    }
    await backend.call("model.select", { modelId })
    const model = models.find((item) => item.id === modelId)
    if (model) {
      console.log(
        model.capabilitiesKnown
          ? `Reported capabilities: ${model.capabilities?.join(", ") || "none"}`
          : "Model-specific capabilities are not reported by this provider.",
      )
    }
    console.log(`Model selected: ${modelId}`)
    return
  }
}