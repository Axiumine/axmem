# Gemini CLI Commands for Axmem

Custom commands for using Axmem with [Gemini CLI](https://github.com/google-gemini/gemini-cli).

> **Credits**: These commands were contributed by [@CharlieBytesX](https://github.com/CharlieBytesX) to the project Axmem is derived from.

## Prerequisites

Ensure Axmem MCP server is configured. Axmem is not published to PyPI yet, so `uvx axmem` does not resolve: from a
checkout of the repository, `uv tool install .` puts the `axmem` command on your `PATH` (after a release, `uvx axmem`
does the same). Axmem also fails closed: a server with no `AUTH_MODE` exits at start. A server that Gemini CLI starts
runs over STDIO, which carries no bearer token, so set `AUTH_MODE=none` for it once, in the user config file (every
request is then the one local tenant, `DEFAULT_TENANT`):

```bash
mkdir -p ~/.config/axmem && echo "AUTH_MODE=none" >> ~/.config/axmem/.env

# STDIO transport (recommended for local use); a bare `axmem` serves over STDIO
gemini mcp add axmem axmem

# Or HTTP transport. A server on this machine started with AUTH_MODE=none and SERVER_HOST=127.0.0.1 needs no credential
gemini mcp add -t http axmem http://localhost:8020/mcp

# A server on a network runs AUTH_MODE=jwt, introspection or static and needs the token as a header
gemini mcp add -t http --header "Authorization: Bearer $AXMEM_TOKEN" axmem https://axmem.example.com/mcp
```

## Installation

Copy all commands to your Gemini CLI commands directory:

```bash
cp docs/gemini-cli/commands/*.toml ~/.gemini/commands/
```

Or copy individual commands as needed.

## Available Commands

| Command | Description |
|---|---|
| `/encode-repo` | Bootstrap a repository into Axmem's knowledge base using a 7-phase protocol |
| `/axmem-setup` | Configure the Axmem MCP server |
| `/memory-explore` | Deep exploration of the Axmem knowledge graph |
| `/memory-list` | List recent memories from Axmem |
| `/memory-save` | Save current context as an atomic memory with curation workflow |
| `/memory-search` | Search memories semantically |

## Usage Examples

### Encode a Repository

Bootstrap a new project into Axmem:

```
/encode-repo my-project
```

The command walks through 7 phases: discovery, foundation, architecture, patterns, features, decisions, and validation.

### Save a Memory

After a conversation where you learned something important:

```
/memory-save
```

The command will analyze the conversation, check for existing related memories, and propose a new atomic memory with proper curation.

### Search Memories

Find relevant context from your knowledge base:

```
/memory-search authentication patterns
```

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

## Command Format

Gemini CLI commands are TOML files supporting:

- `{{args}}` - Argument injection from user input
- `@{path}` - File content injection
- `!{command}` - Shell command output injection

See the [Gemini CLI documentation](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/custom-commands.md) for more details.
