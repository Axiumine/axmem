This section provides more detailed instructions on how to connect forgetful to various AI Agent applications.
- [Claude Code](#claude-code)
- [VS Code](#vs-code)
- [Copilot CLI](#copilot-cli)
- [Cursor](#cursor)
- [Codex](#codex)
- [Gemini CLI](#gemini-cli)
- [OpenCode](#opencode)
- [Forgetful CLI](#forgetful-cli)

## Authentication

Forgetful fails closed: a server with no `AUTH_MODE` refuses to start (see the
[Configuration Guide](configuration.md#authentication-configuration)). What that means for a client:

- **STDIO** (the client launches `uvx forgetful-ai` itself): there is no network listener and no bearer
  token, so the server needs `AUTH_MODE=none`. Set it once for every launch in the user config file, or
  add it to the `env` of a client entry:

  ```bash
  mkdir -p ~/.config/forgetful && echo "AUTH_MODE=none" >> ~/.config/forgetful/.env
  ```

  Every STDIO example below assumes this. All requests are then the one local tenant (`DEFAULT_TENANT`).
- **HTTP on this machine**: start the server with `AUTH_MODE=none` and `SERVER_HOST=127.0.0.1`. `AUTH_MODE=none`
  is refused on any other bind address, so the server cannot be exposed without a credential.
- **HTTP on a network**: the server runs `AUTH_MODE=jwt`, `introspection` or `static`, and the client sends
  `Authorization: Bearer <token>`. The token names the tenant, and a tenant sees only its own memories.
  Examples that pass the header are under Claude Code and VS Code; any client that can set a request
  header on a remote MCP server works the same way.

## Claude Code

### [Plugin](https://github.com/ScottRBK/forgetful-plugin) 

```bash
/plugin marketplace add ScottRBK/forgetful-plugin
/plugin install forgetful-plugin@forgetful-plugins
cd ~/.claude/plugins/forgetful-plugin
cp .mcp.json.stdio.example .mcp.json
```


### STDIO Transport
```bash
claude mcp add --scope user forgetful uvx forgetful-ai
```

### STDIO with Environment Variables (Google)
```bash
claude mcp add --scope user forgetful uvx forgetful-ai \
  -e DATABASE=Postgres \
  -e POSTGRES_HOST=localhost \
  -e PGPORT=5432 \
  -e POSTGRES_DB=forgetful \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=pass \
  -e EMBEDDING_PROVIDER=Google \
  -e EMBEDDING_MODEL=models/gemini-embedding-001 \
  -e GOOGLE_AI_API_KEY=your-api-key
```

### STDIO with Environment Variables (OpenAI)
```bash
claude mcp add --scope user forgetful uvx forgetful-ai \
  -e EMBEDDING_PROVIDER=OpenAI \
  -e OPENAI_API_KEY=sk-your-openai-api-key \
  -e EMBEDDING_MODEL=text-embedding-3-small \
  -e EMBEDDING_DIMENSIONS=256
```

### STDIO with Environment Variables (Ollama)
```bash
claude mcp add --scope user forgetful "uvx forgetful-ai[ollama]" \
  -e EMBEDDING_PROVIDER=Ollama \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e EMBEDDING_MODEL=nomic-embed-text \
  -e EMBEDDING_DIMENSIONS=768
```

### STDIO with Environment Variables (llama.cpp / OpenAI-compatible)
```bash
claude mcp add --scope user forgetful uvx forgetful-ai \
  -e EMBEDDING_PROVIDER=OpenAI \
  -e OPENAI_BASE_URL=http://localhost:8080/v1 \
  -e EMBEDDING_MODEL=my-model \
  -e OPENAI_SUPPORTS_DIMENSIONS=false \
  -e EMBEDDING_DIMENSIONS=384
```

### HTTP Transport
```bash
claude mcp add --transport http --scope user forgetful http://localhost:8020/mcp
```

### HTTP Transport with a bearer token
```bash
claude mcp add --transport http --scope user forgetful https://forgetful.example.com/mcp \
  --header "Authorization: Bearer $FORGETFUL_TOKEN"
```


## VS Code

VS Code has built-in MCP support through GitHub Copilot Chat. Configure MCP servers in `.vscode/mcp.json` (workspace-level) or via the command palette (`Ctrl+Shift+P` → `MCP: Open User Configuration`) for user-level configuration.

See [VS Code MCP docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) for more info.

### STDIO Transport

```json
{
  "servers": {
    "forgetful": {
      "type": "stdio",
      "command": "uvx",
      "args": ["forgetful-ai"]
    }
  }
}
```

### STDIO with Environment Variables

```json
{
  "servers": {
    "forgetful": {
      "type": "stdio",
      "command": "uvx",
      "args": ["forgetful-ai"],
      "env": {
        "DATABASE": "Postgres",
        "POSTGRES_HOST": "localhost",
        "PGPORT": "5432",
        "POSTGRES_DB": "forgetful",
        "POSTGRES_USER": "user",
        "POSTGRES_PASSWORD": "pass",
        "EMBEDDING_PROVIDER": "Google",
        "EMBEDDING_MODEL": "models/gemini-embedding-001",
        "GOOGLE_AI_API_KEY": "${input:google-api-key}"
      }
    }
  },
  "inputs": [
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
    "forgetful": {
      "type": "stdio",
      "command": "uvx",
      "args": ["forgetful-ai"],
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
    "forgetful": {
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
    "forgetful": {
      "type": "http",
      "url": "https://forgetful.example.com/mcp",
      "headers": { "Authorization": "Bearer ${input:forgetful-token}" }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "forgetful-token",
      "description": "Forgetful bearer token",
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
# Enter: Name: forgetful, Command: uvx, Arguments: forgetful-ai
# Press Ctrl+S to save
```

### Manual Configuration (~/.copilot/mcp-config.json)

```json
{
  "mcpServers": {
    "forgetful": {
      "command": "uvx",
      "args": ["forgetful-ai"]
    }
  }
}
```

### HTTP Transport

```json
{
  "mcpServers": {
    "forgetful": {
      "url": "http://localhost:8020/mcp"
    }
  }
}
```

### Custom Agents & Skills

For enhanced workflows with Forgetful, we provide ready-to-use Copilot CLI agents and skills for memory management, search, and knowledge graph exploration.

See [Copilot CLI Integration](copilot-cli/README.md) for installation and usage.


## Cursor
Pasting the following configuration into your Cursor `~/.cursor/mcp.json` file is the recommended approach. You may also install in a specific project by creating `.cursor/mcp.json` in your project folder. See [Cursor MCP docs](https://docs.cursor.com/context/model-context-protocol) for more info.


### STDIO Transport

```json
{
  "mcpServers": {
    "forgetful": {
      "command": "uvx",
      "args": ["forgetful-ai"]
      }
    }
}
```

### HTTP Transport
```json
{
  "mcpServers": {
    "forgetful": {
      "url": "http://localhost:8020/mcp"
      }
    }
}
```

[![Install MCP Server](https://cursor.com/deeplink/mcp-install-dark.svg)](cursor://anysphere.cursor-deeplink/mcp/install?name=forgetful_ai&config=eyJjb21tYW5kIjoidXZ4IGZvcmdldGZ1bC1haSJ9)


## Codex

### STDIO Transport

```bash
codex mcp add forgetful uvx forgetful-ai 
```

### HTTP Transport

```bash
codex mcp add forgetful --url http://localhost:8020/mcp
```



## Gemini CLI

### STDIO Transport

```bash
gemini mcp add forgetful uvx forgetful-ai
```
### HTTP Transport

```bash
gemini mcp add -t http forgetful http://localhost:8020/mcp
```

### Custom Commands

For enhanced workflows with Forgetful, we provide ready-to-use Gemini CLI commands for memory management, search, and repository encoding.

See [Gemini CLI Commands](gemini-cli/README.md) for installation and usage.


## OpenCode

Add to your `opencode.json` or `opencode.jsonc` configuration file.

### STDIO Transport

```jsonc
{
  "mcp": {
    "forgetful": {
      "type": "local",
      "command": ["uvx", "forgetful-ai"]
    }
  }
}
```

### STDIO with Provenance Tracking

```jsonc
{
  "mcp": {
    "forgetful": {
      "type": "local",
      "command": ["uvx", "forgetful-ai"],
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
    "forgetful": {
      "type": "remote",
      "url": "http://localhost:8020/mcp"
    }
  }
}
```

### Custom Commands & Skills

For enhanced workflows with Forgetful, we provide ready-to-use OpenCode commands and skills for memory management, search, and repository encoding.

See [OpenCode Integration](opencode/README.md) for installation and usage.

## Forgetful CLI

Forgetful ships its own terminal client alongside the MCP server - no MCP client
required. It executes the same tool registry the meta-tools use.

### Local (default)

```bash
uv tool install forgetful-ai
forgetful memory search "wsl dns" -c "wsl networking"
forgetful memory save "content" --title "Title" --importance 7
```

Local mode uses the same database and settings as a locally launched server
(`~/.config/forgetful/.env` is shared by both).

### From a source checkout (before a PyPI release)

`uv tool install forgetful-ai` pulls from PyPI. To exercise the CLI from a clone,
run the console script through the project venv - no install, and edits apply
immediately:

```bash
uv run forgetful --help          # top-level help lists the subcommands
uv run forgetful tools list
uv run forgetful memory recent
```

To put a real `forgetful` on your PATH from local source (the shipped experience),
install the checkout editable:

```bash
uv tool install --editable .     # run from the repo root
forgetful memory recent
uv tool uninstall forgetful-ai   # to remove
```

Bare `forgetful` (and `uvx --from . forgetful`) still boots the MCP server for
back-compat, so lead with a verb - `forgetful memory --help`, `forgetful tools
--help` - to explore the CLI.

### Remote deployment with a bearer token

Against a remote PostgreSQL-backed deployment that requires a credential (see
[Configuration Guide](configuration.md#authentication-configuration)). There is no
interactive sign-in: the CLI sends the bearer token in `FORGETFUL_TOKEN` (a JWT, an
introspected token or a static token, as the server is configured). The token is read from the
environment only and is never written to a file.

```bash
export FORGETFUL_TOKEN="<token>"

forgetful auth login --server https://forgetful.example.com
# Checks the connection and credentials, then saves FORGETFUL_SERVER to
# ~/.config/forgetful/.env

forgetful auth status    # server, whether the credentials work (read-only)
forgetful memory search "deployment checklist" -c "release prep"   # runs remotely
```

The token names the tenant, so the CLI reaches only that tenant's memories. Memories can be
grouped under a free-form scope label with `-p/--project` (for example
`forgetful memory save "..." --title "..." -p billing`); the label is created on first use.

Local mode needs no token and is the `DEFAULT_TENANT` tenant. A local CLI invocation never
starts a network listener, so it works whatever `AUTH_MODE` is set to.

Per-invocation overrides: `--server URL` targets any deployment, `--local` forces
local mode. `--json` on any tool command emits machine-readable output for scripting.
