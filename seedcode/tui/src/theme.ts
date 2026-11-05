import {
  confirm as promptConfirm,
  input as promptInput,
  password as promptPassword,
  select as promptSelect,
} from "@inquirer/prompts"

export const VERSION = "10.2.0"
export const GREEN = "\x1b[38;2;34;197;94m"
export const WHITE = "\x1b[38;2;250;250;250m"
export const GRAY = "\x1b[38;2;161;161;170m"
export const MUTED = "\x1b[38;2;113;113;122m"
export const RESET = "\x1b[0m"

const baseTheme = {
  prefix: { idle: "", done: "" },
  spinner: { interval: 80, frames: [".", "..", "..."] },
  style: {
    answer: (text: string) => `${WHITE}${text}${RESET}`,
    message: (text: string) => `${WHITE}${text}${RESET}`,
    error: (text: string) => `ERROR: ${text}`,
    defaultAnswer: (text: string) => `${MUTED}(${text})${RESET}`,
    help: (text: string) => `${MUTED}${text}${RESET}`,
    highlight: (text: string) => `${GREEN}${text}${RESET}`,
    key: (text: string) => `${GREEN}${text}${RESET}`,
  },
}

export function select<Value>(config: {
  message: string
  choices: readonly {
    name: string
    value: Value
    description?: string
    disabled?: boolean | string
  }[]
  pageSize?: number
  loop?: boolean
}) {
  return promptSelect<Value>({
    ...config,
    instructions: {
      navigation: "Up/Down navigate, Enter select, Esc back",
      pager: "",
    },
    theme: {
      ...baseTheme,
      icon: { cursor: `${GREEN}>${RESET}` },
      indexMode: "hidden",
      style: {
        ...baseTheme.style,
        disabled: (text: string) => `${MUTED}${text}${RESET}`,
        description: (text: string) => `${MUTED}${text}${RESET}`,
        keysHelpTip: () => undefined,
      },
    },
  })
}

export function input(config: Parameters<typeof promptInput>[0]) {
  return promptInput({ ...config, theme: baseTheme })
}

export function password(config: Parameters<typeof promptPassword>[0]) {
  return promptPassword({ ...config, theme: baseTheme })
}

export function confirm(config: Parameters<typeof promptConfirm>[0]) {
  return promptConfirm({ ...config, theme: baseTheme })
}

export function brand(section: string) {
  console.log(`${GREEN}S E E D C O D E${RESET}  ${WHITE}CLI v${VERSION}${RESET}`)
  console.log(`${MUTED}${section}${RESET}`)
}

export function note(message: string) {
  console.log(`${GRAY}${message}${RESET}`)
}

export function error(message: string) {
  console.error(`ERROR: ${message}`)
}

export async function promptOrCancel<T>(prompt: () => Promise<T>): Promise<T | undefined> {
  try {
    return await prompt()
  } catch (reason) {
    if (reason && typeof reason === "object" && "name" in reason && reason.name === "ExitPromptError") {
      return undefined
    }
    throw reason
  }
}