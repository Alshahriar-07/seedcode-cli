import type { BackendBridge } from "../backend.js"
import { promptOrCancel, select } from "../theme.js"

type Agent = { id: string; name: string; description: string; tools: string[] | null; active: boolean }

export async function agentScreen(backend: BackendBridge): Promise<void> {
  const agents = await backend.call<Agent[]>("agent.list")
  const selected = await promptOrCancel(() =>
    select({
      message: "Agent profiles",
      choices: [
        ...agents.map((agent) => ({
          name: `${agent.active ? "> " : "  "}${agent.name}${agent.active ? "  ACTIVE" : ""}`,
          value: agent.id,
          description: `${agent.description}${agent.tools ? ` | ${agent.tools.length} tools` : " | Full tool access"}`,
        })),
        { name: "Back", value: "@back" },
      ],
      pageSize: 12,
    }),
  )
  if (!selected || selected === "@back") return
  const agent = await backend.call<{ name: string }>("agent.select", { agentId: selected })
  console.log(`Agent selected: ${agent.name}`)
}