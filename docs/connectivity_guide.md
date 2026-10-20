This section provides more detailed instructions on how to connect axmem to various AI Agent applications.

> **Install first.** Axmem is not published to PyPI yet, so the `uvx axmem` commands below only work once a release
> exists. Until then, install it from a checkout with `uv tool install .` (see the README) and replace `uvx axmem` with
> `axmem serve --transport stdio` (or just `axmem` for the CLI) in every example. `uvx axmem[ollama]` becomes
> `uv tool install ".[ollama]"`.

- [Claude Code](#claude-code)
- [VS Code](#vs-code)
- [Copilot CLI](#copilot-cli)
- [Cursor](#cursor)
- [Codex](#codex)
- [Gemini CLI](#gemini-cli)
- [OpenCode](#opencode)
- [Axmem CLI](#axmem-cli)

## Authentication

Axmem fails closed: a server with no `AUTH_MODE` refuses to start (see the
[Configuration Guide](configuration.md#authentication-configuration)). What that means for a client:

- **STDIO** (the client launches `uvx axmem` itself): there is no network listener and no bearer
  token, so the server needs `AUTH_MODE=none`. Set it once for every launch in the user config file, or
  add it to the `env` of a client entry:

  ```bash
  mkdir -p ~/.config/axmem && echo "AUTH_MODE=none" >> ~/.config/axmem/.env
  ```

  Every STDIO example below assumes this. All requests are then the one local tenant (`DEFAULT_TENANT`).
- **HTTP on this machine**: start the server with `AUTH_MODE=none` and `SERVER_HOST=127.0.0.1`. `AUTH_MODE=none`
  is refused on any other bind address, so the server cannot be exposed without a credential. Reach it as `127.0.0.1`,
  `::1` or `localhost` (or a name listed in `AXMEM_ALLOWED_HOSTS`): a request with any other `Host` answers `421`, and one with an `Origin` that is not a loopback
  origin (or one named in `CORS_ORIGINS` while `CORS_ENABLED=true`) answers `403`, which keeps a web page from driving it.
- **HTTP on a network**: the server runs `AUTH_MODE=jwt`, `introspection` or `static`, and the client sends
  `Authorization: Bearer <token>`. The token names the tenant, and a tenant sees only its own memories.
  Examples that pass the header are under Claude Code and VS Code; any client that can set a request
  header on a remote MCP server works the same way.

## Claude Code

### STDIO Transport
```bash
claude mcp add --scope user axmem uvx axmem
```

### STDIO with Environment Variables (Google)
```bash
claude mcp add --scope user axmem uvx axmem \
  -e QDRANT_URL=http://localhost:6333 \
  -e QDRANT_API_KEY=your-qdrant-api-key \
  -e EMBEDDING_PROVIDER=Google \
  -e EMBEDDING_MODEL=models/gemini-embedding-001 \
  -e GOOGLE_AI_API_KEY=your-api-key
```

### STDIO with Environment Variables (OpenAI)
```bash
claude mcp add --scope user axmem uvx axmem \
  -e EMBEDDING_PROVIDER=OpenAI \
  -e OPENAI_API_KEY=sk-your-openai-api-key \
  -e EMBEDDING_MODEL=text-embedding-3-small \
  -e EMBEDDING_DIMENSIONS=256
```

### STDIO with Environment Variables (Ollama)
```bash
claude mcp add --scope user axmem "uvx axmem[ollama]" \
  -e EMBEDDING_PROVIDER=Ollama \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e EMBEDDING_MODEL=nomic-embed-text \
  -e EMBEDDING_DIMENSIONS=768
```

### STDIO with Environment Variables (llama.cpp / OpenAI-compatible)
```bash
claude mcp add --scope user axmem uvx axmem \
  -e EMBEDDING_PROVIDER=OpenAI \
  -e OPENAI_BASE_URL=http://localhost:8080/v1 \
  -e EMBEDDING_MODEL=my-model \
  -e OPENAI_SUPPORTS_DIMENSIONS=false \
  -e EMBEDDING_DIMENSIONS=384
```

### HTTP Transport
```bash
claude mcp add --transport http --scope user axmem http://localhost:8020/mcp
```

### HTTP Transport with a bearer token
```bash
claude mcp add --transport http --scope user axmem https://axmem.example.com/mcp \
  --header "Authorization: Bearer $AXMEM_TOKEN"
```


## VS Code

VS Code has built-in MCP support through GitHub Copilot Chat. Configure MCP servers in `.vscode/mcp.json` (workspace-level) or via the command palette (`Ctrl+Shift+P` → `MCP: Open User Configuration`) for user-level configuration.

See [VS Code MCP docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) for more info.

### STDIO Transport

```json
{
  "servers": {
    "axmem": {
      "type": "stdio",
      "command": "uvx",
      "args": ["axmem"]
    }
  }
}
```

### STDIO with Environment Variables

```json
{
  "servers": {
    "axmem": {
      "type": "stdio",
      "command": "uvx",
      "args": ["axmem"],
      "env": {
        "QDRANT_URL": "http://localhost:6333",
        "QDRANT_API_KEY": "${input:qdrant-api-key}",
        "EMBEDDING_PROVIDER": "Google",
        "EMBEDDING_MODEL": "models/gemini-embedding-001",
        "GOOGLE_AI_API_KEY": "${input:google-api-key}"
      }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "qdrant-api-key",
      "description": "Qdrant API Key",
      "password": true
    },
    {
      "type": "promptString",
      "id": "google-api-key",
      "description": "Google AI API Key",
      "password": true
    }
  ]
}
```

### STDIO with Provenance Tracking

Tag all objects written by this server instance with agent and model identity. Set `ENFORCE_ENV_OVERWRITE=true` to ensure these values cannot be overridden by individual agents.

```json
{
  "servers": {
    "axmem": {
      "type": "stdio",
      "command": "uvx",
      "args": ["axmem"],
      "env": {
        "ENCODING_AGENT": "VS Code Copilot",
        "ENCODING_VERSION": "1.0",
        "AGENT_ID": "my-coding-agent",
        "AGENT_VERSION": "1.0",
        "AGENT_MODEL": "claude-sonnet-4-6",
        "ENFORCE_ENV_OVERWRITE": "true"
      }
    }
  }
}
```

### HTTP Transport

```json
{
  "servers": {
    "axmem": {
      "type": "http",
      "url": "http://localhost:8020/mcp"
    }
  }
}
```

### HTTP Transport with a bearer token

```json
{
  "servers": {
    "axmem": {
      "type": "http",
      "url": "https://axmem.example.com/mcp",
      "headers": { "Authorization": "Bearer ${input:axmem-token}" }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "axmem-token",
      "description": "Axmem bearer token",
      "password": true
    }
  ]
}
```


## Copilot CLI

### STDIO Transport (via /mcp add)

```bash
# Start Copilot CLI
copilot

# Use the /mcp add command interactively
/mcp add
# Enter: Name: axmem, Command: uvx, Arguments: axmem
# Press Ctrl+S to save
```

### Manual Configuration (~/.copilot/mcp-config.json)

```json
{
  "mcpServers": {
    "axmem": {
      "command": "uvx",
      "args": ["axmem"]
    }
  }
}
```

### HTTP Transport

```json
{
  "mcpServers": {
    "axmem": {
      "url": "http://localhost:8020/mcp"
    }
  }
}
```

### Custom Agents & Skills

For enhanced workflows with Axmem, we provide ready-to-use Copilot CLI agents and skills for memory management, search, and knowledge graph exploration.

See [Copilot CLI Integration](copilot-cli/README.md) for installation and usage.


## Cursor
Pasting the following configuration into your Cursor `~/.cursor/mcp.json` file is the recommended approach. You may also install in a specific project by creating `.cursor/mcp.json` in your project folder. See [Cursor MCP docs](https://docs.cursor.com/context/model-context-protocol) for more info.


### STDIO Transport

```json
{
  "mcpServers": {
    "axmem": {
      "command": "uvx",
      "args": ["axmem"]
      }
    }
}
```

### HTTP Transport
```json
{
  "mcpServers": {
    "axmem": {
      "url": "http://localhost:8020/mcp"
      }
    }
}
```

[![Install MCP Server](https://cursor.com/deeplink/mcp-install-dark.svg)](cursor://anysphere.cursor-deeplink/mcp/install?name=axmem&config=eyJjb21tYW5kIjoidXZ4IGF4bWVtIn0=)


## Codex

### STDIO Transport

```bash
codex mcp add axmem uvx axmem 
```

### HTTP Transport

```bash
codex mcp add axmem --url http://localhost:8020/mcp
```



## Gemini CLI

### STDIO Transport

```bash
gemini mcp add axmem uvx axmem
```
### HTTP Transport

```bash
gemini mcp add -t http axmem http://localhost:8020/mcp
```

### Custom Commands

For enhanced workflows with Axmem, we provide ready-to-use Gemini CLI commands for memory management, search, and repository encoding.

See [Gemini CLI Commands](gemini-cli/README.md) for installation and usage.


## OpenCode

Add to your `opencode.json` or `opencode.jsonc` configuration file.

### STDIO Transport

```jsonc
{
  "mcp": {
    "axmem": {
      "type": "local",
      "command": ["uvx", "axmem"]
    }
  }
}
```

### STDIO with Provenance Tracking

```jsonc
{
  "mcp": {
    "axmem": {
      "type": "local",
      "command": ["uvx", "axmem"],
      "env": {
        "ENCODING_AGENT": "OpenCode",
        "ENCODING_VERSION": "1.3.13",
        "AGENT_ID": "my-coding-agent",
        "AGENT_VERSION": "1.0",
        "AGENT_MODEL": "claude-sonnet-4-6",
        "ENFORCE_ENV_OVERWRITE": "true"
      }
    }
  }
}
```

### HTTP Transport

```jsonc
{
  "mcp": {
    "axmem": {
      "type": "remote",
      "url": "http://localhost:8020/mcp"
    }
  }
}
```

### Custom Commands & Skills

For enhanced workflows with Axmem, we provide ready-to-use OpenCode commands and skills for memory management, search, and repository encoding.

See [OpenCode Integration](opencode/README.md) for installation and usage.

## Axmem CLI

Axmem ships its own terminal client alongside the MCP server - no MCP client
required. It executes the same tool registry the meta-tools use.

### Local (default)

```bash
uv tool install axmem
axmem memory search "wsl dns" -c "wsl networking"
axmem memory save "content" --title "Title" --importance 7
```

Local mode uses the same Qdrant and settings as a locally launched server
(`~/.config/axmem/.env` is shared by both).

### From a source checkout (before a PyPI release)

`uv tool install axmem` needs a PyPI release, which does not exist yet; from a clone, `uv tool install .` installs
the checkout. To exercise the CLI without installing, run the console script through the project venv - edits apply
immediately:

```bash
uv run axmem --help          # top-level help lists the subcommands
uv run axmem tools list
uv run axmem memory recent
```

To put a real `axmem` on your PATH from local source (the shipped experience),
install the checkout editable:

```bash
uv tool install --editable .     # run from the repo root
axmem memory recent
uv tool uninstall axmem   # to remove
```

Bare `axmem` (and `uvx --from . axmem`) still boots the MCP server for
back-compat, so lead with a verb - `axmem memory --help`, `axmem tools
--help` - to explore the CLI.

### Remote deployment with a bearer token

Against a remote deployment that requires a credential (see
[Configuration Guide](configuration.md#authentication-configuration)). There is no
interactive sign-in: the CLI sends the bearer token in `AXMEM_TOKEN` (a JWT, an
introspected token or a static token, as the server is configured). The token is read from the
environment only and is never written to a file.

```bash
export AXMEM_TOKEN="<token>"

axmem auth login --server https://axmem.example.com
# Checks the connection and credentials, then saves AXMEM_SERVER to
# ~/.config/axmem/.env

axmem auth status    # server, whether the credentials work (read-only)
axmem memory search "deployment checklist" -c "release prep"   # runs remotely
```

The token names the tenant, so the CLI reaches only that tenant's memories. Memories can be
grouped under a free-form scope label with `-p/--project` (for example
`axmem memory save "..." --title "..." -p billing`); the label is created on first use.

Local mode needs no token and is the `DEFAULT_TENANT` tenant. A local CLI invocation never
starts a network listener, so it works whatever `AUTH_MODE` is set to.

Per-invocation overrides: `--server URL` targets any deployment, `--local` forces
local mode. `--json` on any tool command emits machine-readable output for scripting.
