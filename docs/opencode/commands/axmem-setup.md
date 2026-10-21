---
description: Configure Axmem MCP server for OpenCode
---
# Axmem Setup

Configure the Axmem semantic memory MCP server for OpenCode.

## Step 1: Check Existing Configuration

First, check if Axmem is already configured in your `opencode.json`:

```bash
!`cat opencode.json 2>/dev/null | grep -A5 axmem || echo "Not configured"`
```

If already configured:
- Ask user if they want to reconfigure
- If no, exit with message: "Axmem is already configured."
- If yes, proceed to modify the configuration

## Step 2: Choose Setup Type

Ask the user which setup they prefer:

**Question**: "How would you like to configure Axmem?"

**Options**:
1. **Standard (Recommended)** - Runs the installed `axmem` command against a Qdrant on `http://127.0.0.1:6333`, as the one local tenant (`AUTH_MODE=none`). Perfect for most users.
2. **Custom** - For advanced setups: remote HTTP server, another Qdrant, custom embeddings, a bearer token, etc.

Axmem is not published to PyPI yet, so `uvx axmem` does not resolve. Check that `axmem` is on the `PATH` (`which axmem`);
if not, it is installed from a checkout of the repository with `uv tool install .`.

## Step 3a: Standard Setup

If user chose Standard, add this to their `opencode.json`:

```jsonc
{
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

Axmem refuses to start without `AUTH_MODE`; `none` is right for a local STDIO server (every request is the one local tenant).

Report: "Axmem is now configured! Your memories are stored in the Qdrant at `http://127.0.0.1:6333` (start one first if none runs). Restart OpenCode to activate."

## Step 3b: Custom Setup

If user chose Custom:

1. Fetch the configuration docs:
```
WebFetch: https://github.com/Axiumine/axmem/blob/main/docs/configuration.md
```

2. Ask what they need:
   - **Remote HTTP server** - Connect to Axmem running elsewhere
   - **Qdrant address** - Use a Qdrant on another host (`QDRANT_URL`, `QDRANT_API_KEY`)
   - **Custom embeddings** - Use different embedding model/provider
   - **Authentication** - A bearer token for a server on a network (`AUTH_MODE=jwt`, `introspection` or `static`, see the configuration docs); there is no OAuth sign-in

3. Based on their answers, build the appropriate configuration:

**For HTTP remote server** (a server on a network needs the token; one on this machine with `AUTH_MODE=none` needs no `headers`):
```jsonc
{
  "mcp": {
    "axmem": {
      "type": "remote",
      "url": "https://HOST:PORT/mcp",
      "headers": { "Authorization": "Bearer <token>" },
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

**For another Qdrant with custom embeddings:**
```jsonc
{
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

4. Help the user update their `opencode.json` with the configuration.

## Troubleshooting

If setup fails:
- Check that `axmem` is installed: `which axmem` (`uv tool install .` from a checkout)
- Run `AUTH_MODE=none axmem serve --transport stdio` by hand: a start that is refused prints one `Error:` line with the cause
- For HTTP: verify the server is running and accessible
- Restart OpenCode after configuration changes

## Notes

- Memories live in Qdrant, in the collections `axmem_memories` and `axmem_links` (`QDRANT_COLLECTION_PREFIX`)
- For full configuration reference: https://github.com/Axiumine/axmem/blob/main/docs/configuration.md
