import type { BackendBridge } from "../backend.js"
import { input, promptOrCancel, select } from "../theme.js"

type Entry = { name: string; path: string }
type DirectoryPage = { path: string; parent: string | null; entries: Entry[]; truncated: boolean }

async function browse(backend: BackendBridge, start: string): Promise<string | undefined> {
  let current = start
  while (true) {
    const page = await backend.call<DirectoryPage>("workspace.list", { path: current })
    const choices: { name: string; value: string }[] = [
      { name: `Select this folder: ${page.path}`, value: "@select" },
    ]
    if (page.parent) choices.push({ name: "..  Parent directory", value: page.parent })
    choices.push(...page.entries.map((entry) => ({ name: `${entry.name}  ${entry.path}`, value: entry.path })))
    choices.push({ name: "Filter directories...", value: "@filter" })
    const picked = await promptOrCancel(() =>
      select({ message: page.path, choices, pageSize: 14, loop: true }),
    )
    if (!picked) return undefined
    if (picked === "@select") return page.path
    if (picked === "@filter") {
      const filter = await promptOrCancel(() => input({ message: "Directory name filter" }))
      if (filter === undefined) continue
      const matches = page.entries.filter((entry) => entry.name.toLowerCase().includes(filter.toLowerCase()))
      if (matches.length === 1) current = matches[0]!.path
      else if (matches.length > 1) {
        const match = await promptOrCancel(() =>
          select({
            message: "Matching directories",
            choices: matches.map((entry) => ({ name: entry.path, value: entry.path })),
            pageSize: 14,
          }),
        )
        if (match) current = match
      }
      continue
    }
    current = picked
  }
}

export async function workspaceScreen(backend: BackendBridge): Promise<void> {
  const [current, recent, roots] = await Promise.all([
    backend.call<{ path: string }>("workspace.current"),
    backend.call<string[]>("workspace.recent"),
    backend.call<string[]>("workspace.roots"),
  ])
  const action = await promptOrCancel(() =>
    select({
      message: `Active workspace: ${current.path}`,
      choices: [
        { name: "Use current workspace", value: "current" },
        { name: "Browse folders", value: "browse" },
        { name: "Recent workspaces", value: "recent", disabled: recent.length === 0 },
        { name: "Open directory by path", value: "open" },
        { name: "Browse drives", value: "drives", disabled: roots.length < 2 },
        { name: "Back", value: "back" },
      ],
    }),
  )
  if (!action || action === "back" || action === "current") return

  let target: string | undefined
  if (action === "browse") target = await browse(backend, current.path)
  if (action === "drives") {
    const root = await promptOrCancel(() =>
      select({ message: "Select a drive", choices: roots.map((path) => ({ name: path, value: path })) }),
    )
    if (root) target = await browse(backend, root)
  }
  if (action === "recent") {
    target = await promptOrCancel(() =>
      select({ message: "Recent workspaces", choices: recent.map((path) => ({ name: path, value: path })) }),
    )
  }
  if (action === "open") {
    target = await promptOrCancel(() => input({ message: "Directory path", default: current.path }))
  }
  if (target) {
    const selected = await backend.call<{ path: string }>("workspace.select", { path: target })
    console.log(`Workspace selected: ${selected.path}`)
  }
}