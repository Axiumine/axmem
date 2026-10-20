<h1 align="center">Axmem</h1>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.14-blue" alt="Python 3.14">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/MCP-server-purple" alt="MCP">
  <a href="https://github.com/jlowin/fastmcp">
    <img src="https://img.shields.io/badge/FastMCP-powered-orange" alt="FastMCP">
  </a>
  <a href="https://github.com/qdrant/fastembed">
    <img src="https://img.shields.io/badge/FastEmbed-powered-orange" alt="FastEmbed">
  </a>
</p>


**Axmem** is a storage and retrieval tool for AI agents, built as a Model Context Protocol (MCP) server on the FastMCP framework. Once connected, MCP clients such as coding agents, chatbots or your own custom agents can store and retrieve information from the same knowledge base.

> **Axmem** is maintained by [Axiumine](https://github.com/Axiumine). It starts from the
> [Forgetful](https://github.com/ScottRBK/forgetful) codebase by Scott Raisbeck (MIT) and goes
> in directions an upstream-tracking fork cannot. Planned direction, not yet shipped: fewer features,
> **Qdrant as the only store** (SQLite, PostgreSQL and Alembic removed), embeddings computed in-process on the GPU
> (or through an optional HTTP gateway) and GPU reranking. Every change, its reason and its status are listed in
> [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md) and [CHANGELOG.md](CHANGELOG.md).
>
> Until the rename lands, the commands, package names and environment variables below still say
> `forgetful`. See [License](#license) for attribution.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Quick Start](#quick-start)
- [CLI](#cli)
- [Usage Examples](#usage-examples)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## Overview
Many of us now work with AI agents, especially in software development. Work and decisions move fast, which makes it hard to keep up with notes and context persistence.

So if you are following something like the [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) for example and you want to take the brainstorming session you've just had with Claude on your desktop or phone and use it as the basis of your next Claude Code session, then having a shared knowledge base across the two agents can help with this.

This is just one example. As more agentic applications appear, so will the use cases for sharing data across them.

Knowledge bases are becoming a key infrastructure component for interactions with AI. There are many excellent knowledge base solutions available (many of them free on GitHub), so check them out and find one that works for you, even if it isn't Axmem. Interactions with agents get easier and more rewarding once they know more about you, your work and your previous interactions with them or other AI systems.

What makes **Axmem** different from other memory-based MCP services is that it takes an opinionated view on how AI agents should store and retrieve data.

**Axmem** imposes the [Zettelkasten principle](https://en.wikipedia.org/wiki/Zettelkasten) when clients wish to record memories, that is, each memory must be atomic (one concept per note). Along with the note (title and content), we also ask the client / agent to provide context around what it was doing when creating the note, along with keywords and tags. With this information we create semantic embeddings and store these to aid with later retrieval and in addition to this we also automatically link the memory to existing memories that have a particular similarity score, allowing for the automatic construction of a knowledge graph.

In this sense **Axmem** becomes a little like Obsidian for AI agents, where the auto linking nudges them in building up a graph of the knowledge.

We find, [as do others (A-MEM: Agentic Memory for LLM Agents)](https://arxiv.org/abs/2502.12110), all this helps in ensuring that when the agent requires relevant information from the memory system later, the correct information is returned.

Memories belong to a **tenant**, the owner of a set of memories, and a tenant never sees another tenant's memories. A memory can also carry an optional **project** scope, a free-form label that groups memories and narrows a search.


![Architecture](docs/images/Forgetful%20Architecture.drawio_transparent.png)

## Features
- Configure either **STDIO** or **HTTP** transport mechanism (or stand up two services to support both)
- Fail-closed authentication: JWT, token introspection or static bearer tokens, one tenant per credential; a server with no `AUTH_MODE` refuses to start
- Tenant isolation: a tenant reads and changes only its own memories, over MCP, REST and the CLI
- Meta-tool discovery: only three tools are exposed to the client application to preserve the context window.
- Flexible storage – SQLite (default, zero-config; slated for removal, see the note above) or PostgreSQL (for scale and production deployments)
- Stores memories as vectors so they can be retrieved with natural-language queries from AI agents.
- Cross-encoder reranking to improve recall and precision of memory retrieval.
- Flexible embedding and cross-encoder providers; run everything locally without calls to the cloud thanks to FastEmbed, or send embeddings to an optional HTTP embeddings gateway (`EMBEDDING_PROVIDER=gateway`; switching providers needs a re-embed, see [Embedding Migration](docs/embedding_migration.md))
- Automatic linking of semantically similar memories, automating the creation of the knowledge graph.

For the complete roadmap, see [Features Roadmap](docs/features_roadmap.md).

---

## Quick Start

### Option 1: From Source

```bash
git clone https://github.com/Axiumine/axmem.git
cd axmem

# Install dependencies with uv
uv sync

# Run the server (uses SQLite by default). The server fails closed: with no AUTH_MODE it
# refuses to start. AUTH_MODE=none serves one local tenant and is accepted only on stdio or
# when the HTTP server binds a loopback address (127.0.0.1 or ::1)
AUTH_MODE=none uv run main.py
```
Data stored in platform-appropriate locations (`~/.local/share/forgetful` on Linux/Mac, `AppData` on Windows).

The server starts with stdio transport, which is what MCP clients expect. For HTTP on this machine: `AUTH_MODE=none uv run main.py --transport http --host 127.0.0.1 --port 8020`. To keep `AUTH_MODE=none` out of every command, put it in `~/.config/forgetful/.env`.

> **Note**: Axmem is not published to PyPI. `uvx forgetful-ai` installs the upstream Forgetful package, not Axmem.

### Option 2: Native Setup with PostgreSQL (multi-user)

Axmem runs directly on the host; there are no container images or compose files. You need:

- [uv](https://docs.astral.sh/uv/) (it installs the Python 3.14 the project pins in `.python-version`)
- a PostgreSQL server with the [pgvector](https://github.com/pgvector/pgvector) extension available, and an empty
  database and a role that can create tables in it. The server runs its Alembic migrations and
  `CREATE EXTENSION IF NOT EXISTS vector` at startup, so either that role may create extensions or an admin runs
  `CREATE EXTENSION vector` in the database once

```bash
git clone https://github.com/Axiumine/axmem.git
cd axmem
uv sync --locked

# Settings come from environment variables; copy the reference file and edit it
cp .env.example .env
# In .env set at least:
#   DATABASE=Postgres
#   POSTGRES_HOST=127.0.0.1   PGPORT=5099   POSTGRES_DB=forgetful
#   POSTGRES_USER=...         POSTGRES_PASSWORD=...
#   AUTH_MODE=jwt | introspection | static   (a shared server needs a credential, see below)

# Run the HTTP server (stdio is the default transport). Bound to all interfaces by default, it
# needs AUTH_MODE=jwt, introspection or static; AUTH_MODE=none needs SERVER_HOST=127.0.0.1
uv run main.py --transport http --port 8020
```

Run it under your service manager of choice (for example a systemd unit whose `WorkingDirectory` is the clone and whose
`ExecStart` is `uv run main.py --transport http`). Environment variables set in the unit take precedence over `.env`.

**Note**: If no `.env` file exists, the application uses defaults from `app/config/settings.py`, except
`AUTH_MODE`, which has no default. For all configuration options, see [Configuration Guide](docs/configuration.md).

### Authentication and tenants

Every request acts as one tenant. `AUTH_MODE` decides how the tenant is found, and the server will not start without it:

| `AUTH_MODE` | Credential | Tenant |
|---|---|---|
| `jwt` | Signed JWT as `Authorization: Bearer` | Claim `tenant_id`, else `tenant`, else `sub` |
| `introspection` | Opaque bearer token checked at an RFC 7662 endpoint | Same claim order |
| `static` | Fixed bearer token per tenant; only its SHA-256 hash is configured (`AUTH_STATIC_TOKEN_HASHES`) | The tenant the token is configured for |
| `none` | None | `DEFAULT_TENANT` |

`AUTH_MODE=none` is accepted only on stdio or on a loopback bind (`127.0.0.1`, `::1`); on any other address the server refuses to
start. The GitHub and Google OAuth providers and the CLI browser login are removed. See the
[Configuration Guide](docs/configuration.md#authentication-configuration) for the details and for generating a static token hash.

### Connecting to an Agent

For detailed connection guides (Claude Code, Claude Desktop, other clients that support MCP), see [Connectivity Guide](docs/connectivity_guide.md).

- [Claude Code](docs/connectivity_guide.md#claude-code)
- [VS Code](docs/connectivity_guide.md#vs-code)
- [Copilot CLI](docs/connectivity_guide.md#copilot-cli) (includes [custom agents and skills](docs/copilot-cli/README.md))
- [Cursor](docs/connectivity_guide.md#cursor)
- [Codex](docs/connectivity_guide.md#codex)
- [Gemini CLI](docs/connectivity_guide.md#gemini-cli) (includes [custom commands](docs/gemini-cli/README.md))
- [Opencode](docs/connectivity_guide.md#opencode) (includes [custom commands and skills](docs/opencode/README.md))

Add Axmem to your MCP client configuration (replace `/path/to/axmem` with your clone):

**stdio transport (recommended for local use):**
```json
{
  "mcpServers": {
    "axmem": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/axmem", "main.py"],
      "env": { "AUTH_MODE": "none" }
    }
  }
}
```

**HTTP transport (for remote or shared use):**
```json
{
  "mcpServers": {
    "axmem": {
      "type": "http",
      "url": "https://axmem.example.com/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

A local HTTP server started with `AUTH_MODE=none` needs no header (`http://localhost:8020/mcp`).


---

## CLI

The `forgetful` command is also a full terminal client over the same tool registry the
MCP meta-tools use - against your local database by default (as the `DEFAULT_TENANT` tenant), or a remote
deployment after `auth login`, as the tenant that `FORGETFUL_TOKEN` names.

```bash
# From the repository root (the command is still named forgetful)
uv tool install .

# Curated verbs for daily use
forgetful memory save "Set generateResolvConf false to fix WSL2 DNS" \
    --title "WSL2 DNS fix" --importance 7
forgetful memory search "wsl dns" -c "wsl networking" -n 5
forgetful memory get 812
forgetful memory recent -n 10 -p my-project

# Generic passthrough to any available tool
forgetful tools list --category memory
forgetful tools info query_memory
forgetful call get_recent_memories --args '{"limit": 5, "project": "homelab"}'

# Remote deployment (bearer token from the environment; saves FORGETFUL_SERVER to ~/.config/forgetful/.env)
export FORGETFUL_TOKEN="<token>"
forgetful auth login --server https://forgetful.example.com
forgetful auth status
forgetful memory search "wsl dns" -c "wsl networking"          # now runs remotely
forgetful memory search "wsl dns" -c "wsl networking" --local  # force local mode per invocation

# Scripting: --json emits machine-readable output
forgetful memory search "wsl dns" -c "wsl networking" --json | jq '.primary_memories[0].id'
```

`forgetful serve` is the canonical way to run the MCP server (`forgetful serve
--transport http --port 8020`); the bare `forgetful` invocation and
the legacy `--transport`/`--re-embed` flags keep working indefinitely, so existing MCP
client configurations are unaffected. A remote deployment is reached with the bearer token in
`FORGETFUL_TOKEN`; there is no browser login. See the
[Configuration Guide](docs/configuration.md#cli-configuration) for precedence rules, or walk
through the [`forgetful-cli-setup`](skills/forgetful-cli-setup/SKILL.md) skill for install,
auth, and verification steps end-to-end.


---

## Usage Examples

Axmem exposes only three meta-tools to MCP clients. The tools available through
`execute_forgetful_tool` depend on the permitted scopes. Use
`discover_forgetful_tools` for the current runtime catalog.

### Example 1: Project-Scoped Memory

Give a memory a project scope for better organization and scoped retrieval. A project is just a label;
it is created the first time it is used, so there is nothing to set up first.

```python
# Create a memory in the "ecommerce-redesign" project scope
memory = execute_forgetful_tool(
    "create_memory",
    {
        "title": "Payment gateway: Stripe chosen over PayPal",
        "content": "Selected Stripe for its API, fees, and fraud detection.",
        "context": "Choosing the payment provider for the redesign",
        "keywords": ["payment", "stripe", "paypal"],
        "tags": ["payment", "decision"],
        "importance": 9,
        "project": "ecommerce-redesign"
    }
)

# Later, query within project scope
results = execute_forgetful_tool(
    "query_memory",
    {
        "query": "payment processing implementation",
        "query_context": "Implementing payments for the redesign",
        "project": "ecommerce-redesign"
    }
)
# Returns: Stripe decision + auto-linked related memories
```

### Tool Categories

The catalog covers memories, with an optional project scope on create, update, query and recent.

For complete documentation with extensive examples, see [Complete Tool Reference](docs/tool_reference.md).

---

## How It Works

### Atomic Memory Principle

Inspired by Zettelkasten, each memory stores **one concept** in ~300-400 words:
- **Easily titled** – Forces clarity (200 char limit)
- **Self-contained** – Understandable without external context
- **Linkable** – Small units enable precise knowledge graphs

For detailed content, split it into 3-7 atomic memories and link them to each other.

### Automatic Knowledge Graph

When you create a memory:
1. **Embedding generated** – FastEmbed converts content to 384-dimensional vector
2. **Similarity search** – Finds related memories (similarity ≥0.7 by default)
3. **Auto-linking** – Creates bidirectional links to top 3-5 matches (configurable)
4. **Graph traversal** – Queries return primary results + 1-hop linked memories

### Token Budget Management

Prevents context window overflow:
- Configurable budget (default 8K tokens)
- Search finds relevant memories; primary results are then sorted by importance (high first)
- Equal-importance primary results keep their search order, even when nothing is truncated
- Truncates gracefully if over budget
- Respects max memory count (default 20)

This keeps query results within the LLM's context budget.

For vector search and optional cross-encoder reranking, see
[Search Documentation](docs/search.md).

---

## Configuration

**No configuration required** – Axmem uses sensible defaults out of the box.

### Key Settings (Optional)

- `MEMORY_TOKEN_BUDGET` – Max tokens for query results (default: `8000`)
- `EMBEDDING_PROVIDER` – `FastEmbed` (default), `Azure`, `Google`, `OpenAI`, `Ollama` or `gateway`
- `EMBEDDING_MODEL` – Embedding model (default: `BAAI/bge-small-en-v1.5`)
- `MEMORY_NUM_AUTO_LINK` – Auto-link count (default: `3`, set `0` to disable)
- `MEMORY_SIMILARITY_THRESHOLD` – Minimum cosine similarity for auto-links (default: `0.7`)
- `SERVER_PORT` – HTTP server port (default: `8020`)
- `AUTH_MODE` – `none`, `jwt`, `introspection` or `static`; no default, the server will not start without it

For all environment variables with detailed explanations, see [Configuration Guide](docs/configuration.md).

---

## Documentation

### Guides

- [Core Concepts](docs/concepts.md) – Memories, tenants and project scopes explained
- [Complete Tool Reference](docs/tool_reference.md) – Runtime tools and examples
- [REST API Reference](docs/api_reference.md) – HTTP endpoints for web UI integration
- [Configuration Guide](docs/configuration.md) – All environment variables explained
- [Connectivity Guide](docs/connectivity_guide.md) – Connect Claude and other MCP clients
- [Nix Guide](docs/nix.md) – Reproducible dev shell and builds with Nix flakes
- [Search Documentation](docs/search.md) – Embedding pipeline and retrieval architecture
- [Embedding Migration](docs/embedding_migration.md) – Switch embedding providers safely
- [Features Roadmap](docs/features_roadmap.md) – Planned features and priorities
- [Changelog](CHANGELOG.md) – Notable changes to Axmem by section
- [Changes from Forgetful](docs/changes-from-forgetful.md) – Every difference from upstream, the reason and its status

### External Resources

- [MCP Protocol Specification](https://modelcontextprotocol.io/) – Model Context Protocol docs
- [pgvector](https://github.com/pgvector/pgvector) – PostgreSQL vector extension
- [FastEmbed](https://github.com/qdrant/fastembed) – Local embedding generation
- [Zettelkasten Principle](https://en.wikipedia.org/wiki/Zettelkasten) – Atomic note-taking method

---

## Contributing

We welcome contributions! Axmem uses integration + E2E testing; Docker is needed only for the PostgreSQL E2E database.

See [Contributors Guide](docs/contributors.md) for:
- Testing workflows (integration tests, E2E tests, GitHub Actions)
- Development setup and running from source
- CI/CD pipeline details
- Release process

### Development

Python 3.14 is the supported version. After `uv sync --locked --all-extras --dev`, enable the
Git hooks with `uv run python -m scripts.quality install`. Commits and pushes then run the lint, type,
security, snapshot, coverage (100 %), memory-budget and optional mutation gates described in
[docs/quality-gates.md](docs/quality-gates.md), which also explains how to run or bypass a single gate.
Before changing code, read the [architecture overview](architecture.md).

---

## License

Axmem is released under the MIT License - see [LICENCE.md](LICENCE.md).

It is derived from [Forgetful](https://github.com/ScottRBK/forgetful), Copyright (c) 2025 Scott Raisbeck,
also released under the MIT License. The original copyright and permission notice is kept unchanged in
[LICENCE.md](LICENCE.md), as the license requires.
