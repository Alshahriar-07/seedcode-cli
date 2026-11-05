import { mkdir } from "node:fs/promises"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const targets = [
  resolve(root, "dist/index.cjs"),
  resolve(root, "../../npm/tui/dist/index.cjs"),
]

for (const outfile of targets) {
  await mkdir(dirname(outfile), { recursive: true })
  await build({
    entryPoints: [resolve(root, "src/main.ts")],
    outfile,
    bundle: true,
    platform: "node",
    format: "cjs",
    target: "node20",
  })
}