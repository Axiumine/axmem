# OpenCode Integration for Axmem

Custom commands and skills for using Axmem with [OpenCode](https://opencode.ai).

## Prerequisites

Ensure you have:
- [OpenCode](https://opencode.ai) installed
- Axmem installed. It is not published to PyPI yet, so `uvx axmem` does not resolve. From a checkout of the repository,
  `uv tool install .` puts the `axmem` command on your `PATH` (after a release, `uvx axmem` does the same)
- A Qdrant server and an NVIDIA GPU for the default embedder, as in the [README](../../README.md#quick-start)

## MCP Server Configuration

Add Axmem to your OpenCode configuration file (`opencode.json` or `opencode.jsonc`).

A server started by the client runs over STDIO, which carries no bearer token, and Axmem fails closed: without
`AUTH_MODE` it exits at start. For a local server set `AUTH_MODE=none` (every request is then the one local tenant,
`DEFAULT_TENANT`), once for every launch in the user config file or in the `environment` of the entry:

```bash
mkdir -p ~/.config/axmem && echo "AUTH_MODE=none" >> ~/.config/axmem/.env
```

### Standard Setup (Recommended)

Setup against a Qdrant on its default address (`http://127.0.0.1:6333`; see the
[Qdrant configuration](../configuration.md#qdrant-configuration)):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "axmem": {
      "type": "local",
      "command": ["axmem", "serve", "--transport", "stdio"],
      "enabled": true,
      "environment": {
        "AUTH_MODE": "none"
      }
    }
  }
}
```

Your memories are stored in that Qdrant (collections `axmem_memories` and `axmem_links`).

### Custom Setup (Advanced)

For another Qdrant, custom embeddings, or remote servers:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "axmem": {
      "type": "local",
      "command": ["axmem", "serve", "--transport", "stdio"],
      "enabled": true,
      "environment": {
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

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "axmem": {
      "type": "remote",
      "url": "https://axmem.example.com/mcp",
      "headers": { "Authorization": "Bearer <token>" },
      "enabled": true
    }
  }
}
```

See [configuration.md](../configuration.md) for all available environment variables.

## Installation

### Commands

Copy command files to your OpenCode commands directory:

```bash
# Global installation
cp docs/opencode/commands/*.md ~/.config/opencode/command/

# Or project-specific
cp docs/opencode/commands/*.md .opencode/command/
```

### Skills

Skills are maintained once, in the canonical [`skills/`](../../skills/) directory at the
repository root. Copy them to your OpenCode skills directory:

```bash
# Global installation
cp -r skills/axmem-* ~/.config/opencode/skill/

# Or project-specific
cp -r skills/axmem-* .opencode/skill/
```

## Available Commands

| Command | Description |
|---|---|
| `/axmem-setup` | Configure the Axmem MCP server |
| `/memory-search` | Search memories semantically |
| `/memory-save` | Save current context as an atomic memory with curation workflow |
| `/memory-list` | List recent memories from Axmem |
| `/memory-explore` | Deep exploration of the Axmem knowledge graph |
| `/encode-repo` | Bootstrap a repository into Axmem's knowledge base |

## Available Skills

Skills are automatically discovered by OpenCode and loaded on-demand via the `skill` tool.

The OpenCode-specific skills previously documented here have been superseded by the global
skill set. See [`skills/README.md`](../../skills/README.md) for the full catalog.

## Usage Examples

### Search Memories

Find relevant context from your knowledge base:

```
/memory-search authentication patterns
```

### Save a Memory

After a conversation with important insights:

```
/memory-save
```

The command analyzes the conversation, checks for related memories, and proposes an atomic memory with proper curation.

### List Recent Memories

See what's been added recently:

```
/memory-list 10
```

### Explore the Knowledge Graph

Deep traversal of related memories:

```
/memory-explore payment processing
```

### Encode a Repository

Bootstrap a new project into Axmem:

```
/encode-repo my-project
```

## Command Format

OpenCode commands are Markdown files with YAML frontmatter:

```markdown
---
description: Brief description shown in TUI
---
Template text with $ARGUMENTS placeholder
```

Supported placeholders:
- `$ARGUMENTS` - All passed arguments combined
- `$1`, `$2`, `$3` - Individual positional arguments
- `` !`command` `` - Shell command output injection
- `@path/to/file` - File content injection

See the [OpenCode commands documentation](https://opencode.ai/docs/commands/) for more details.

## Skill Format

OpenCode skills are `SKILL.md` files in named folders:

```markdown
---
name: skill-name
description: What the skill does and when to use it
---
# Skill Content
Detailed guidance for the agent...
```

See the [OpenCode skills documentation](https://opencode.ai/docs/skills/) for more details.

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

### Connection Timeout

Increase the timeout in your configuration:
```jsonc
{
  "mcp": {
    "axmem": {
      "type": "local",
      "command": ["axmem", "serve", "--transport", "stdio"],
      "timeout": 60000
    }
  }
}
```

### Check Server Status

For HTTP servers, verify the endpoint is accessible:
```bash
curl http://localhost:8020/health
```

### View Logs

Axmem writes its log to standard error and keeps no log file. For a server that OpenCode starts over STDIO, read
what the client records of the server's stderr; for an HTTP server, read the console or the journal of the process
that runs it. `LOG_LEVEL=DEBUG` (in the `environment` of the entry, or in the HTTP server's environment) makes it more
talkative; see [configuration.md](../configuration.md).
