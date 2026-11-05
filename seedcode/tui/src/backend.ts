import { spawn, type ChildProcessByStdio } from "node:child_process"
import { createInterface } from "node:readline"
import type { Readable, Writable } from "node:stream"

type BackendReply<T> = { id: number; ok: true; result: T } | { id: number; ok: false; error: string }
type Pending = { resolve: (value: unknown) => void; reject: (reason: Error) => void }

function commandFromEnvironment() {
  const raw = process.env.SEEDCODE_BACKEND_COMMAND
  if (!raw) return [process.env.SEEDCODE_PYTHON || "python", "-m", "seedcode"]
  const parsed: unknown = JSON.parse(raw)
  if (!Array.isArray(parsed) || !parsed.every((part) => typeof part === "string") || parsed.length === 0) {
    throw new Error("SEEDCODE_BACKEND_COMMAND must be a non-empty JSON string array")
  }
  return parsed as string[]
}

export class BackendBridge {
  private readonly child: ChildProcessByStdio<Writable, Readable, null>
  private readonly pending = new Map<number, Pending>()
  private sequence = 0

  constructor() {
    const [executable, ...args] = commandFromEnvironment()
    this.child = spawn(executable, [...args, "--tui-backend"], {
      stdio: ["pipe", "pipe", "inherit"],
      env: process.env,
      windowsHide: true,
    })
    const lines = createInterface({ input: this.child.stdout })
    lines.on("line", (line) => this.receive(line))
    this.child.on("error", (reason) => this.failAll(reason))
    this.child.on("exit", (code) => {
      this.failAll(new Error(`Python backend exited (${code ?? "unknown"})`))
    })
  }

  call<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const id = ++this.sequence
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
      })
      this.child.stdin.write(`${JSON.stringify({ id, method, params })}\n`, (reason) => {
        if (!reason) return
        this.pending.delete(id)
        reject(reason)
      })
    })
  }

  async close() {
    this.child.stdin.end()
    await new Promise<void>((resolve) => {
      if (this.child.exitCode !== null) return resolve()
      this.child.once("exit", () => resolve())
      this.child.kill()
    })
  }

  private receive(line: string) {
    let response: BackendReply<unknown>
    try {
      response = JSON.parse(line) as BackendReply<unknown>
    } catch {
      return this.failAll(new Error("Python backend returned invalid JSON"))
    }
    const pending = this.pending.get(response.id)
    if (!pending) return
    this.pending.delete(response.id)
    if (response.ok) pending.resolve(response.result)
    else pending.reject(new Error(response.error))
  }

  private failAll(reason: Error) {
    for (const pending of this.pending.values()) pending.reject(reason)
    this.pending.clear()
  }
}

export async function runPythonSession(workspace: string) {
  const [executable, ...args] = commandFromEnvironment()
  const child = spawn(executable, args, {
    stdio: "inherit",
    env: {
      ...process.env,
      SEEDCODE_FORCE_PYTHON_TUI: "1",
      SEEDCODE_WORKSPACE: workspace,
    },
    windowsHide: false,
  })
  const code = await new Promise<number>((resolve, reject) => {
    child.once("error", reject)
    child.once("exit", (exitCode) => resolve(exitCode ?? 0))
  })
  if (code !== 0) throw new Error(`SeedCode session exited with code ${code}`)
}