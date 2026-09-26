# Security Policy

This document describes how Seed Code CLI handles user data, how to report a
security problem, and the practices that keep a Seed Code installation safe. It
describes the architecture as it is actually implemented in this repository;
where something is not claimed, it is deliberately not claimed.

## Security model

Seed Code is a **local-first** desktop/terminal application. Its design goal is
that your data stays on your machine.

- **No central database for user project or application data.** Seed Code does
  not operate a hosted service that stores your projects, conversations, or
  application data. Those live on your machine: configuration, history, memory
  and logs under `~/.seedcode/`, and project memory under `.seedcode/` inside
  each project directory.
- **No telemetry or analytics.** A source audit of this repository found no
  telemetry, analytics, or central upload of user data. The application makes
  no network request for the purpose of reporting usage.
- **Network communication is limited.** Requests are made only to the model
  provider endpoints you configure (for example OpenRouter, Ollama, or your own
  OpenAI-compatible endpoint) and, where applicable, to **localhost** computer
  and browser-control interfaces. Provider requests carry the conversation
  content needed to answer your prompt; that is the function of the tool, not a
  separate data collection mechanism.
- **Credentials are not hardcoded.** API keys and other credentials are not
  embedded in the source. They are stored per provider in your local
  configuration and are shown masked or not at all — never printed in full,
  never written to logs, and never included in an error message. A release
  artifact may carry a build-time credential for the built-in *Default*
  provider; that credential lives only in the release artifact and is never
  committed to source control.
- **Local is powerful.** Seed Code can read and write files and run commands
  within the permission level you grant. See *Scope* and *Best Practices*
  below.

No software is perfectly secure, and no absolute guarantee is made here. Seed
Code is provided under the project license, without warranty.

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.** Public
disclosure before a fix is available puts every user at risk.

Instead, use the repository's **private vulnerability reporting** — open the
[Security tab](https://github.com/Alshahriar-07/seedcode-cli/security) and
choose **"Report a vulnerability"** to open a private advisory visible only to
the maintainers. This is the project's supported private reporting channel;
there is no separate security email address.

In your report, please include:

- what the issue is and where it lives (file, module, command, or installer);
- how to reproduce it, with a minimal example if possible;
- the version you tested (`seedcode --version`) and your platform;
- the impact you believe it has.

**Never include real API keys, tokens, passwords, private credentials, or
working exploit details in a public issue, discussion or pull request.** Redact
secrets before sharing them anywhere, including in a private report.

You can expect an acknowledgement, an assessment, and an update on whether and
how the issue will be fixed. Please allow reasonable time for a fix and a
release before any public discussion.

## Scope

Areas of particular interest, and of particular risk:

- **Credential handling** — storage, masking, logging and display of API keys
  and other credentials.
- **Provider configuration** — isolation between providers, and the resolution
  order of `Default`'s built-in credential.
- **Local project data** — `.seedcode/` project memory and `~/.seedcode/`
  configuration, history and logs.
- **Command execution** — the terminal tool that runs commands for Chat-adjacent
  agent workflows.
- **Agent Mode** — tool use and permission gating during multi-step execution,
  including the workspace coding capability (file edits within the workspace
  boundary and project indexing).
- **Installer and release artifacts** — download, checksum verification, install
  paths, and any embedded credential.
- **The `.env` build step** — the release build reads a local `.env`; that file
  must never be printed, logged, committed, or packaged.

## Best practices for users

- **Protect your API keys.** Store them only where Seed Code expects them
  (provider configuration or an environment variable). Do not paste keys into
  issues, screenshots, or shared terminals.
- **Do not commit secrets.** Keep `.env` files and any generated credential
  modules out of version control; the repository's `.gitignore` already
  excludes the generated default-key module.
- **Review agent actions.** Agent Mode can change files and run commands within
  the permission level you grant. Start with the least permission that does the
  job, and review what was changed.
- **Keep Seed Code updated.** Install from the official channels (the remote
  installers, `pip`, or GitHub Releases) so you receive the checksum-verified
  current build.
- **Use providers you trust.** Your prompts and the context the agent reads are
  sent to the provider you configure. Choose a provider whose data handling you
  accept, and prefer a local provider such as Ollama when the content is
  sensitive.
- **Avoid sharing sensitive project data.** Do not point the agent at
  repositories or directories containing secrets, private keys, or personal
  data unless you intend for that content to be processed.
- **Verify a download before running it.** Both remote installers check
  `SHA256SUMS.txt` before installing; if you download a binary manually, verify
  its checksum too.
