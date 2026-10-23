---
name: axmem-cli-setup
description: >-
  Set up the Axmem CLI and connect from a terminal — install, local or remote mode,
  auth, and verification. Use when connecting a human or headless agent via shell, wiring
  CI with token auth, or operating a local server (serve, Qdrant connection, auth mode,
  re-embedding, backup). Also covers the machine contract: --json output, exit codes, and
  the discovery ladder.
license: AGPL-3.0-only
disable-model-invocation: true
tags: [cli, setup, auth, operations, terminal]
allowed-tools:
  - Bash(axmem:*)
---

# Setting up the Axmem CLI

The CLI is the shell-native surface: same tool registry as MCP, reachable by anything that
can run a command. Setup ends with a verified round-trip, not an installed binary.

## Install

```bash
uv tool install .       # from a checkout of the repository: puts `axmem` on the PATH
axmem --version
```

Axmem is not published to PyPI yet, so `uvx axmem` and `pip install axmem` do not work until a release exists.
A built wheel (`uv build`) or a pip install needs the NVIDIA index for the TensorRT packages: `uv tool install --extra-index-url https://pypi.nvidia.com dist/axmem-*.whl` or the same flag with `pip install` (the README, section Installing the package, lists every way).
`axmem` is the only console script.

## Choose a mode

Connection precedence: `--local` > `--server URL` > `AXMEM_SERVER` env > local default.

- **Local** (default): runs in-process against the locally configured Qdrant, with no server
  to start. It builds the same runtime as the server, so the machine needs an NVIDIA GPU
  (driver R580 or newer) and the model files, and the command exits with the cause when they
  are missing (see `docs/gpu-models.md`; `EMBEDDING_PROVIDER` and `RERANKING_PROVIDER=HTTP` or
  `RERANKING_ENABLED=false` are the explicit ways to run without one). Right for a personal
  knowledge base on one machine.
- **Remote**: drives a deployed server. Set `AXMEM_TOKEN` (a bearer token: JWT,
  introspected or static, as the server is configured) in the environment, then
  `axmem auth login --server <url>` checks the connection and saves the server to the
  user config; or set `AXMEM_SERVER` as well and skip the login. The token is read from
  the environment only and never stored; there is no browser flow. The token names the
  tenant, and a tenant sees only its own memories. Local mode is the `DEFAULT_TENANT` tenant.

Config merges shell env over `~/.config/axmem/.env`. Check state anytime with
`axmem auth status`.

## Verify — the completion criterion

```bash
axmem tools list                          # registry categories come back
axmem memory recent --json                # one real call round-trips
```

Done when: both succeed against the intended target (local or the remote URL).

## The machine contract

For scripts and agents, pass `--json` on data commands — `tools`, `call` and `memory`
verbs: results are machine JSON, errors arrive as `{"error": ...}` on stderr. `auth`
subcommands are human-oriented and don't take `--json`. Exit codes are contractual — `0`
success, `1` tool or runtime error, `2` usage error. Parse output, branch on exit code.

## The discovery ladder

1. **Curated verbs** for the common paths: `axmem memory search|save|get|recent`
   (`-p/--project <label>` scopes them to a project).
2. **`axmem tools list [--category <c>]`** to discover every registry operation.
3. **`axmem tools info <operation>`** for the schema.
4. **`axmem call <operation> --args '<JSON>'`** to invoke anything the registry has.

The judgment for what to store, query, and link lives in the domain skills
(`axmem-remember`, `axmem-recall`, ...) — the ladder is how the CLI reaches the
same operations.

## Operating a local server

- The server and local CLI mode start the TensorRT embedder and reranker first and refuse to start, printing
  `Error: the embedder cannot start: ...` or `the reranker`, when the GPU, the driver or the model files are
  unusable (`docs/gpu-models.md` lists each cause).
- The server refuses to start without `AUTH_MODE`. `AUTH_MODE=none` serves one local tenant and is
  accepted only on stdio or on a loopback bind: `AUTH_MODE=none axmem serve --transport http
  --host 127.0.0.1 --port 8020` (or `--transport stdio` for MCP clients that spawn a process). A
  server bound to another address needs `AUTH_MODE=jwt`, `introspection` or `static`. A credential-free HTTP
  server also answers `421` to a `Host` that is not `localhost` or a loopback address and `403` to a foreign
  `Origin`, so point `AXMEM_SERVER` at `http://127.0.0.1:<port>` (or `localhost`), not at another name for the machine.
- Storage via env: Qdrant is the only store. `QDRANT_URL` (one node or a comma-separated cluster, default
  `http://127.0.0.1:6333`), `QDRANT_API_KEY`, and `QDRANT_COLLECTION_PREFIX` to keep several deployments apart.
- Operator commands against the configured Qdrant: `axmem backup`, `axmem restore <dir> --yes` (stop the
  server first; every node's file goes back to the node it came from, so `QDRANT_URL` must list the same nodes
  in the same order), `axmem export <file>` (complete or it fails; refuses when writers made it miss memories and warns when
  the data changed meanwhile: stop the writers for an exact copy), `axmem import <file>`, `axmem qdrant-check` (compares the
  nodes of a cluster; a node it cannot read is reported as such, not as a disagreement).
- Memory ids are UUIDs: `axmem memory get <uuid>`; a number is refused.
- After changing embedding provider, model, precision or the `QDRANT_SHARD_NUMBER`,
  `QDRANT_REPLICATION_FACTOR` or `QDRANT_WRITE_CONSISTENCY_FACTOR` layout (the server refuses to start on
  either mismatch): stop every server (one left running refuses writes with a 409 once the alias has moved to another
  embedder, and must be restarted; a create or edit that the switch overtook says it may be in the previous generation, so look for the memory before repeating it; an import that it overtook ends with an error that says it was written, wholly or in part, and is repeated after the restart), `axmem re-embed --dry-run` to preview, then `axmem re-embed` (batch size
  tunable via `--batch-size`); it also rebuilds the links collection in the new layout. If a run was stopped while it moved
  the links, `serve`, `import`, `backup` and `export` refuse and name `<prefix>_links_relayout`: run `axmem re-embed` again, which copies
  the links back before it embeds anything.

Done when: `/health` answers (HTTP) or an MCP client lists the three meta-tools (stdio).
