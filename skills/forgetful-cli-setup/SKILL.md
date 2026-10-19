---
name: forgetful-cli-setup
description: >-
  Set up the Forgetful CLI and connect from a terminal — install, local or remote mode,
  auth, and verification. Use when connecting a human or headless agent via shell, wiring
  CI with token auth, or operating a local server (serve, database selection, feature
  flags, re-embedding). Also covers the machine contract: --json output, exit codes, and
  the discovery ladder.
license: MIT
disable-model-invocation: true
tags: [cli, setup, auth, operations, terminal]
allowed-tools:
  - Bash(forgetful:*)
---

# Setting up the Forgetful CLI

The CLI is the shell-native surface: same tool registry as MCP, reachable by anything that
can run a command. Setup ends with a verified round-trip, not an installed binary.

## Install

```bash
uvx forgetful-ai --version     # ephemeral, no install
pip install forgetful-ai       # or: uv tool install forgetful-ai
```

Both `forgetful` and `forgetful-ai` invoke the same CLI.

## Choose a mode

Connection precedence: `--local` > `--server URL` > `FORGETFUL_SERVER` env > local default.

- **Local** (default): runs in-process against the locally configured database — zero
  config, no server needed. Right for a personal knowledge base on one machine.
- **Remote**: drives a deployed server. Interactive use: `forgetful auth login --server
  <url>` (browser OAuth; tokens cached under `~/.config/forgetful/`, server written to the
  user config). Headless/CI use: set `FORGETFUL_SERVER` and `FORGETFUL_TOKEN` (bearer) in
  the environment — no browser involved.

Config merges shell env over `~/.config/forgetful/.env`. Check state anytime with
`forgetful auth status`.

## Verify — the completion criterion

```bash
forgetful tools list                          # registry categories come back
forgetful project list --json                 # one real call round-trips
```

Done when: both succeed against the intended target (local or the remote URL).

## The machine contract

For scripts and agents, pass `--json` on data commands — `tools`, `call`, `memory`, and
`project` verbs: results are machine JSON, errors arrive as `{"error": ...}` on stderr. `auth`
subcommands are human-oriented and don't take `--json`. Exit codes are contractual — `0`
success, `1` tool or runtime error, `2` usage error. Parse output, branch on exit code.

## The discovery ladder

1. **Curated verbs** for the common paths: `forgetful memory search|save|get|recent`,
   `forgetful project list`.
2. **`forgetful tools list [--category <c>]`** to discover every registry operation.
3. **`forgetful tools info <operation>`** for the schema.
4. **`forgetful call <operation> --args '<JSON>'`** to invoke anything the registry has.

The judgment for what to store, query, and link lives in the domain skills
(`forgetful-remember`, `forgetful-recall`, ...) — the ladder is how the CLI reaches the
same operations.

## Operating a local server

- `forgetful serve --transport http --host 0.0.0.0 --port 8020` (or `--transport stdio`
  for MCP clients that spawn a process).
- Database via env: `DATABASE=SQLite` with `SQLITE_PATH` (file) or `SQLITE_MEMORY=true`
  (ephemeral), or `DATABASE=Postgres` with `POSTGRES_HOST/PORT/DB/USER/PASSWORD`.
- Feature flag (default off): `FILES_ENABLED` —
  discovery reflects whatever is enabled.
- After changing embedding provider or model: `forgetful re-embed --dry-run` to preview,
  then `forgetful re-embed` (batch size tunable via `--batch-size`).

Done when: `/health` answers (HTTP) or an MCP client lists the three meta-tools (stdio).
