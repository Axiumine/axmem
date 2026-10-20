---
name: axmem-mcp-setup
description: >-
  Set up an MCP client for Axmem — wire Claude Code, Cursor, Copilot, Codex, Gemini, or
  OpenCode to the server and verify the connection behaves. Covers stdio vs HTTP transport,
  auth and scopes, the three meta-tools every client sees, and delegation to subagents.
license: MIT
disable-model-invocation: true
tags: [mcp, setup, clients, transport, auth]
allowed-tools:
  - mcp__axmem__discover_axmem_tools
  - mcp__axmem__how_to_use_axmem_tool
  - mcp__axmem__execute_axmem_tool
---

# Setting up an MCP client

Every MCP client sees the same three tools fronting the full registry:
`discover_axmem_tools` (what exists, by category), `how_to_use_axmem_tool` (one
tool's docs and schema), `execute_axmem_tool` (invoke by name with a JSON arguments
object). Roughly 500 tokens of surface, with everything else disclosed at runtime.

## Pick a transport

- **stdio** — the client spawns the server per session: command `axmem serve --transport stdio` with
  `AUTH_MODE=none`, because the server refuses to start without an auth mode and stdio carries no bearer
  token. Axmem is not published to PyPI yet: install it from a checkout with `uv tool install .` (after a
  release, `uvx axmem` does the same). Right for a personal, local setup.
- **HTTP** — the client connects to a running server at `<server>/mcp`, e.g.
  `http://localhost:8020/mcp`. Right for a shared or deployed instance; send
  `Authorization: Bearer <token>` unless the server runs `AUTH_MODE=none` on a loopback bind.

Claude Code, as the worked example:

```bash
claude mcp add axmem -e AUTH_MODE=none -- axmem serve --transport stdio      # stdio (axmem on the PATH)
claude mcp add --transport http axmem https://axmem.example.com/mcp \
  --header "Authorization: Bearer $AXMEM_TOKEN"                  # HTTP, token
```

Other clients follow the same two shapes (spawn command, or HTTP endpoint) in their own
config format — full per-client walkthroughs live in `docs/connectivity_guide.md` in the
Axmem repo.

## Auth and scopes

The server fails closed: it will not start without `AUTH_MODE`. `AUTH_MODE=none` is the
no-auth single-tenant mode for stdio and loopback binds; deployed servers use bearer tokens
(`jwt`, `introspection` or `static`). The token names the tenant, and a tenant sees only its own
memories. `AXMEM_SCOPES` on the server
side sets a capability ceiling (e.g. read-only) that both MCP and CLI honour; a client that
should only recall can be scoped so writes are refused. A bearer token's own `scope`/`scp`
claim narrows access further (unrelated scopes such as `openid` are ignored); a token whose
scopes include no Axmem scope (`*`, `read`, `write`, `read:<category>`, `write:<category>`)
is refused every tool.

## Verify — the completion criterion

Behavioural, not configurational:

1. `discover_axmem_tools` returns the tool categories.
2. One `execute_axmem_tool` round-trip returns real data — `get_recent_memories`
   works as a smoke call (an empty `memories` list on a fresh tenant is a pass).

Done when: both pass in the client that was just wired, in a fresh session.

## Delegation boundary

Run Axmem MCP calls in the main conversation — subagents do not inherit MCP tool
access. When work must be delegated to a subagent, give it the CLI surface instead
(`Bash` with the `axmem` command travels into subagents; see `axmem-cli-setup`).
