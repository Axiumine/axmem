# GitHub Copilot CLI Integration for Axmem

Custom agents and skills for using Axmem with [GitHub Copilot CLI](https://github.com/github/copilot-cli).

## Prerequisites

Ensure you have:
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli) installed
- A GitHub Copilot subscription (Pro, Pro+, Business, or Enterprise)
- Axmem installed. It is not published to PyPI yet, so `uvx axmem` does not resolve. From a checkout of the repository,
  `uv tool install .` puts the `axmem` command on your `PATH` (after a release, `uvx axmem` does the same)
- A Qdrant server and an NVIDIA GPU for the default embedder, as in the [README](../../README.md#quick-start)

## MCP Server Configuration

A server started by the client runs over STDIO, which carries no bearer token, and Axmem fails closed: without
`AUTH_MODE` it exits at start. For a local server set `AUTH_MODE=none` (every request is then the one local tenant,
`DEFAULT_TENANT`), once for every launch in the user config file or in the `env` of the entry below:

```bash
mkdir -p ~/.config/axmem && echo "AUTH_MODE=none" >> ~/.config/axmem/.env
```

### Standard Setup (Recommended)

Add Axmem via the interactive `/mcp add` command in Copilot CLI (the user config file above supplies `AUTH_MODE`):

```bash
# Start Copilot CLI
copilot

# Then use the /mcp add command
/mcp add
# Enter the following:
#   Name: axmem
#   Command: axmem
#   Arguments: serve --transport stdio
# Press Ctrl+S to save
```

Your memories are stored in the Qdrant at `http://127.0.0.1:6333` unless `QDRANT_URL` says otherwise (collections
`axmem_memories` and `axmem_links`; see the [Qdrant configuration](../configuration.md#qdrant-configuration)).

### Manual Configuration

Alternatively, edit `~/.copilot/mcp-config.json` directly:

```json
{
  "mcpServers": {
    "axmem": {
      "command": "axmem",
      "args": ["serve", "--transport", "stdio"],
      "env": {
        "AUTH_MODE": "none"
      }
    }
  }
}
```

### Custom Setup (Advanced)

For another Qdrant, custom embeddings, or environment variables:

```json
{
  "mcpServers": {
    "axmem": {
      "command": "axmem",
      "args": ["serve", "--transport", "stdio"],
      "env": {
        "AUTH_MODE": "none",
        "QDRANT_URL": "http://qdrant.internal:6333",
        "QDRANT_API_KEY": "your-qdrant-api-key",
        "EMBEDDING_PROVIDER": "Google",
        "EMBEDDING_MODEL": "models/gemini-embedding-001",
        "GOOGLE_AI_API_KEY": "your-google-api-key"
      }
    }
  }
}
```

### Remote HTTP Server

For connecting to an Axmem instance running elsewhere. A server on this machine started with `AUTH_MODE=none` and
`SERVER_HOST=127.0.0.1` needs no credential (`http://localhost:8020/mcp`). A server on a network runs
`AUTH_MODE=jwt`, `introspection` or `static`, and the client sends the token as a header (the token names the tenant):

```json
{
  "mcpServers": {
    "axmem": {
      "url": "https://axmem.example.com/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

See [configuration.md](../configuration.md) for all available environment variables.

## Installation

### Agents

Copy agent files to your Copilot CLI agents directory:

```bash
# Global installation (all projects)
cp docs/copilot-cli/agents/*.agent.md ~/.copilot/agents/

# Or repository-specific
mkdir -p .github/agents
cp docs/copilot-cli/agents/*.agent.md .github/agents/
```

### Skills

Skills are maintained once, in the canonical [`skills/`](../../skills/) directory at the
repository root. Copy them to your Copilot CLI skills directory:

```bash
# Global installation (all projects)
cp -r skills/axmem-* ~/.copilot/skills/

# Or repository-specific
mkdir -p .github/skills
cp -r skills/axmem-* .github/skills/
```

## Available Agents

Custom agents are specialized versions of Copilot for specific tasks. Invoke them via `/agent` or let Copilot route automatically based on your prompt.

| Agent | Description |
|---|---|
| `axmem-memory` | Memory operations: search, save, update, and manage memories |
| `memory-curator` | Memory maintenance: deduplicate, link, mark obsolete, update |
| `knowledge-explorer` | Deep knowledge graph traversal for comprehensive context |

## Available Skills

Skills provide on-demand guidance that Copilot loads when relevant to your task.

The Copilot CLI-specific skills previously documented here have been superseded by the
global skill set. See [`skills/README.md`](../../skills/README.md) for the full catalog.

## Usage Examples

### Search Memories

Using natural language (Copilot routes automatically):

```
Search my memories for authentication patterns
```

Or explicitly invoke the agent:

```
/agent axmem-memory
Search for authentication patterns in my knowledge base
```

### Save a Memory

After a conversation with important insights:

```
Save this conversation about the new caching strategy to memory with importance 8
```

### List Recent Memories

```
Show me the last 10 memories from the Axmem project
```

### Explore the Knowledge Graph

```
/agent knowledge-explorer
Trace all connections from the payment processing decision
```

### Encode a Repository

Bootstrap a new project into Axmem:

```
Analyze this repository and store its key architectural decisions and patterns as memories
```

## Agent Format

GitHub Copilot CLI agents are Markdown files with YAML frontmatter:

```markdown
---
name: agent-name
description: Brief description (required)
tools: ["tool1", "tool2"]  # Optional, omit for all tools
---
# Agent Instructions

Detailed behavioral guidance for the agent...
```

**Storage locations:**
- Global: `~/.copilot/agents/`
- Repository: `.github/agents/`

**Filename format:** `agent-name.agent.md`

See the [Custom Agents documentation](https://docs.github.com/en/copilot/reference/custom-agents-configuration) for more details.

## Skill Format

GitHub Copilot CLI skills use `SKILL.md` files in named folders:

```markdown
---
name: skill-name
description: What the skill does and when to use it
---
# Skill Content

Detailed guidance for the agent...
```

**Storage locations:**
- Global: `~/.copilot/skills/`
- Repository: `.github/skills/`

See the [Agent Skills documentation](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills) for more details.

## Troubleshooting

### MCP Server Not Found

Verify the `axmem` command is on your `PATH`:
```bash
which axmem
```

If not found, install it from a checkout with `uv tool install .` (needs
[uv](https://docs.astral.sh/uv/getting-started/installation/)).

### Server Exits at Start

Run the command by hand to read the reason; it is one `Error:` line. The usual causes: no `AUTH_MODE` (set
`AUTH_MODE=none` as above), a Qdrant that does not answer at `QDRANT_URL`, or no visible NVIDIA GPU.
```bash
AUTH_MODE=none axmem serve --transport stdio
```

### Check MCP Configuration

List configured MCP servers:
```bash
# In Copilot CLI session
/mcp list
```

Or check the config file directly:
```bash
cat ~/.copilot/mcp-config.json
```

### Connection Issues

For HTTP servers, verify the endpoint is accessible:
```bash
curl http://localhost:8020/health
```

### View Logs

Axmem writes its log to standard error and keeps no log file. For a server that Copilot CLI starts over STDIO, read
what the client records of the server's stderr; for an HTTP server, read the console or the journal of the process
that runs it. `LOG_LEVEL=DEBUG` (in the `env` of the entry, or in the HTTP server's environment) makes it more
talkative; see [configuration.md](../configuration.md).

### Agent Not Loading

Ensure agent files have the correct extension (`.agent.md`) and are in the right location:
```bash
ls ~/.copilot/agents/
ls .github/agents/
```
