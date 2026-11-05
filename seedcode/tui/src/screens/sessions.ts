import type { BackendBridge } from "../backend.js"
import { promptOrCancel, select } from "../theme.js"

type Session = { id: string; providerId: string; provider: string; messageCount: number; preview: string }
type Message = { role: string; text: string }

export async function sessionsScreen(backend: BackendBridge): Promise<void> {
  const sessions = await backend.call<Session[]>("session.list")
  if (sessions.length === 0) {
    console.log("No saved sessions yet.")
    return
  }
  const selected = await promptOrCancel(() =>
    select({
      message: "Saved sessions",
      choices: [
        ...sessions.map((session) => ({
          name: `${session.provider}  ${session.id}  (${session.messageCount} messages)  ${session.preview}`,
          value: `${session.providerId}\t${session.id}`,
        })),
        { name: "Back", value: "@back" },
      ],
      pageSize: 12,
    }),
  )
  if (!selected || selected === "@back") return
  const [providerId, sessionId] = selected.split("\t", 2)
  const messages = await backend.call<Message[]>("session.read", { providerId, sessionId })
  console.log(`Session ${sessionId}`)
  for (const message of messages) {
    const body = message.text.replace(/\s+/g, " ").trim()
    if (body) console.log(`${message.role.toUpperCase()}: ${body.slice(0, 240)}`)
  }
}