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
1. **Standard (Recommended)** - Uses uvx to run Axmem against a Qdrant on `http://127.0.0.1:6333`. Perfect for most users.
2. **Custom** - For advanced setups: remote HTTP server, another Qdrant, custom embeddings, authentication, etc.

## Step 3a: Standard Setup

If user chose Standard, add this to their `opencode.json`:

```jsonc
{
  "mcp": {
    "axmem": {
      "type": "local",
      "command": ["uvx", "axmem"],
      "enabled": true
    }
  }
}
```

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
   - **Authentication** - API key or OAuth setup

3. Based on their answers, build the appropriate configuration:

**For HTTP remote server:**
```jsonc
{
  "mcp": {
    "axmem": {
      "type": "remote",
      "url": "http://HOST:PORT/mcp",
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
      "command": ["uvx", "axmem"],
      "enabled": true,
      "environment": {
        "QDRANT_URL": "http://qdrant.internal:6333",
        "QDRANT_API_KEY": "your-qdrant-api-key",
        "EMBEDDING_PROVIDER": "Google",
        "EMBEDDING_MODEL": "models/text-embedding-004"
      }
    }
  }
}
```

4. Help the user update their `opencode.json` with the configuration.

## Troubleshooting

If setup fails:
- Check if `uvx` is installed: `which uvx`
- For HTTP: verify the server is running and accessible
- Restart OpenCode after configuration changes

## Notes

- Memories live in Qdrant, in the collections `axmem_memories` and `axmem_links` (`QDRANT_COLLECTION_PREFIX`)
- For full configuration reference: https://github.com/Axiumine/axmem/blob/main/docs/configuration.md
