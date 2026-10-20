# Configuration Guide

This guide explains all available environment variables for configuring Forgetful.

## Quick Start

All configuration is optional except authentication: Forgetful uses sensible defaults from `app/config/settings.py` when no `.env` file exists, but the server refuses to start until `AUTH_MODE` is set (see [Authentication Configuration](#authentication-configuration)). For a local server, `AUTH_MODE=none` is enough on stdio or with `SERVER_HOST=127.0.0.1`.

To customize configuration, copy the reference file from the repository root and edit it. Forgetful reads
`.env` from the directory it is started in and `~/.config/forgetful/.env` (the user config file, which
outranks the local one):

```bash
cp .env.example .env
# Edit .env with your values (set AUTH_MODE; the example file uses AUTH_MODE=none and SERVER_HOST=127.0.0.1)
uv run main.py --transport http
```

[`.env.example`](../.env.example) lists every setting with its default and the provider blocks.

---

## Application Info

### `SERVICE_NAME`
- **Default**: `Forgetful`
- **Description**: Display name for the service in logs and metrics
- **Example**: `SERVICE_NAME=MyMemoryService`

### `SERVICE_VERSION`
- **Default**: The installed package version
- **Description**: Version identifier for the service
- **Example**: `SERVICE_VERSION=v1.2.3`

### `SERVICE_DESCRIPTION`
- **Default**: `Forgetful Memory Service`
- **Description**: Human-readable description of the service
- **Example**: `SERVICE_DESCRIPTION="My custom memory MCP server"`

---

## Server Configuration

### `SERVER_HOST`
- **Default**: `0.0.0.0`
- **Description**: Network interface the server binds to
- **Values**:
  - `0.0.0.0` - Listen on all interfaces (default). Needs `AUTH_MODE=jwt`, `introspection` or `static`
  - `127.0.0.1` or `::1` - Listen only on localhost. The only HTTP binds that accept `AUTH_MODE=none`
- **Example**: `SERVER_HOST=127.0.0.1`

### `SERVER_PORT`
- **Default**: `8020`
- **Description**: Port number the MCP server listens on
- **Note**: If changed, update your MCP client configuration
- **Example**: `SERVER_PORT=8020`

### `LOG_LEVEL`
- **Default**: `INFO`
- **Description**: Logging verbosity level
- **Values**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Example**: `LOG_LEVEL=DEBUG` (for troubleshooting)

### `LOG_FORMAT`
- **Default**: `console`
- **Description**: Log output format
- **Values**:
  - `console` - Human-readable format (recommended for development)
  - `json` - Structured JSON format (recommended for production)
- **Note**: Matched case-insensitively. Any other value stops startup with an error naming the setting; an empty value keeps the default
- **Example**: `LOG_FORMAT=json`

### `MCP_DESCRIPTOR_MODE`

- **Default**: `verbose`
- **Description**: Controls how much guidance is embedded in the MCP meta-tool descriptions exposed to clients
- **Values**:
  - `verbose` - Include detailed inline guidance in `discover_forgetful_tools` and `execute_forgetful_tool`
  - `compact` - Keep meta-tool descriptions short and use `how_to_use_forgetful_tool` for detailed schemas and examples
- **Example**: `MCP_DESCRIPTOR_MODE=compact`

---

## Database Configuration (SQLite/PostgreSQL)

### `DATABASE`
- **Default**: `SQLite`
- **Description**: Database backend to use
- **Values**:
  - `SQLite` - Lightweight, zero-config (default, recommended for getting started)
  - `Postgres` - Production-grade, scalable (recommended for high load)
- **Example**: `DATABASE=SQLite`

### SQLite Settings

#### `SQLITE_PATH`
- **Default**: `forgetful.db` in the platform-specific user data directory (e.g., `~/.local/share/forgetful/forgetful.db`)
- **Description**: File path for SQLite database
- **Notes**:
  - Relative paths are relative to the directory the server is started from
  - Forgetful creates the directory (when it does not exist yet) as `0700` and keeps the database, its `-wal`/`-shm` files and backups at `0600`, so only the owning account can read your data. Group and other access is also removed from an existing database file on startup; an existing directory is never changed. Windows is left alone.
- **Example**: `SQLITE_PATH=data/forgetful.db`

#### `SQLITE_MEMORY`
- **Default**: `false`
- **Description**: Use in-memory database (ephemeral, for testing only)
- **Values**: `true`, `false`
- **Example**: `SQLITE_MEMORY=false`

### PostgreSQL Settings

**Note**: These settings only apply when `DATABASE=Postgres`

#### `POSTGRES_HOST`
- **Default**: `127.0.0.1`
- **Description**: PostgreSQL server hostname
- **Values**: a host name or address of the PostgreSQL server (it needs the pgvector extension)
- **Example**: `POSTGRES_HOST=db.internal.example.com`

### `PGPORT`
- **Default**: `5099`
- **Description**: PostgreSQL server port
- **Note**: Uses non-standard port to avoid conflicts with existing PostgreSQL installations
- **Example**: `PGPORT=5099`

### `POSTGRES_DB`
- **Default**: `forgetful`
- **Description**: Database name to connect to
- **Example**: `POSTGRES_DB=forgetful`

### `POSTGRES_USER`
- **Default**: `forgetful`
- **Description**: PostgreSQL username for authentication
- **� Security**: Change this in production deployments
- **Example**: `POSTGRES_USER=my_secure_user`

### `POSTGRES_PASSWORD`
- **Default**: `forgetful`
- **Description**: PostgreSQL password for authentication
- **� Security**: **Always change this in production deployments**
- **Example**: `POSTGRES_PASSWORD=my_secure_password_123`

### Common Database Settings

#### `BACKUP_DIR`
- **Default**: empty (next to the SQLite database, in the directory of `SQLITE_PATH`)
- **Description**: Directory for the backups that `--re-embed` takes before changing anything (SQLite snapshot or PostgreSQL `pg_dump`)
- **Notes**:
  - Created as `0700` if missing; the backup files themselves are `0600`
  - Must be persistent storage; with PostgreSQL the default location is still the directory of `SQLITE_PATH`, so point it elsewhere if that is not where you want the dumps
- **Example**: `BACKUP_DIR=/var/backups/forgetful`

#### `DB_LOGGING`
- **Default**: `false`
- **Description**: Enable SQL query logging for debugging (applies to both SQLite and PostgreSQL)
- **Values**: `true`, `false`
- **Note**: Very verbose - use only for troubleshooting. Statements are logged at INFO through the normal logging pipeline (stderr, honouring `LOG_FORMAT`), so the STDIO transport's stdout stays clean. Bound parameter values are never logged.
- **Example**: `DB_LOGGING=true`

---

## Authentication Configuration

Forgetful fails closed. The server has no default authentication mode: with `AUTH_MODE` unset it
**refuses to start**, so a server can never come up open by accident. Choose one mode:

| `AUTH_MODE` | Credential | Tenant comes from |
|---|---|---|
| `jwt` | Signed JWT in `Authorization: Bearer` | Token claims (order below) |
| `introspection` | Opaque token checked at an RFC 7662 endpoint | Introspection claims (order below) |
| `static` | Fixed token per tenant, configured as a SHA-256 hash | The tenant the token is configured for |
| `none` | No credential | `DEFAULT_TENANT` (every request is that one tenant) |

`AUTH_MODE=none` is accepted **only** when the server binds a loopback address (`127.0.0.1` or `::1`;
hostnames such as `localhost` do not count) or runs on stdio. On any other bind (`0.0.0.0`, a LAN
address) the server exits with an error instead of serving anonymous requests. The credential modes
(`jwt`, `introspection`, `static`) need a bearer token, so they are refused on stdio, which carries none.

The GitHub and Google OAuth providers and the browser login of the CLI are removed. Use a token issued
by your identity provider (`jwt`, `introspection`) or a static token.

---

### Tenants

Every user of the server is a **tenant**: the unit of ownership and isolation. A tenant can read and
change only its own memories, on every access path (MCP tools, REST routes, CLI). Another tenant's
memory id answers exactly like an id that does not exist.

The tenant of a request is read from the verified token claims in this order, first non-blank claim wins:

1. `tenant_id`
2. `tenant`
3. `sub`

A token with none of the three is refused. A claim that is not a string or integer, or is longer than
255 characters, is refused rather than skipped. The tenant is created in the database on its first request.
Names are compared exactly, so `Acme` and `acme` are two tenants.

---

### Static tokens (`AUTH_MODE=static`)

One fixed bearer token per tenant, for deployments without an identity provider. The configuration holds
only the **SHA-256 hash** of each token, never the token.

```bash
# Generate a token and its hash; hand the token to the tenant, keep only the hash
TOKEN=$(openssl rand -hex 32)
printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1
```

```bash
# .env
AUTH_MODE=static
AUTH_STATIC_TOKEN_HASHES={"acme": "<64 hex characters>", "globex": "<64 hex characters>"}
```

The client sends `Authorization: Bearer <token>`. A presented token is hashed and compared with every
configured hash in constant time; the token never appears in logs or error messages. One token belongs
to one tenant: the server refuses to start if two tenants share a hash, if a value is not a 64-character
hexadecimal digest, or if no tenant is configured. A static token does not expire; to revoke it, remove
its hash and restart. To rotate, replace the hash.

---

### JWT verification (`AUTH_MODE=jwt`)

Validates JWTs with a JWKS endpoint or a public key.

```bash
# .env
AUTH_MODE=jwt
FASTMCP_SERVER_AUTH_JWT_JWKS_URI=https://auth.yourcompany.com/.well-known/jwks.json
FASTMCP_SERVER_AUTH_JWT_ISSUER=https://auth.yourcompany.com
FASTMCP_SERVER_AUTH_JWT_AUDIENCE=forgetful-api
FASTMCP_SERVER_AUTH_JWT_REQUIRED_SCOPES=api:read,api:write
```

Either `FASTMCP_SERVER_AUTH_JWT_JWKS_URI` or `FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY` is required. Forgetful
validates the signature, issuer, audience and expiry; an expired or invalid token gets `401`.

### Token introspection (`AUTH_MODE=introspection`)

Validates opaque bearer tokens at an introspection endpoint (OAuth 2.0 RFC 7662).

```bash
# .env
AUTH_MODE=introspection
FASTMCP_SERVER_AUTH_INTROSPECTION_URL=https://auth.yourcompany.com/oauth/introspect
FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID=forgetful-resource-server
FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET=your-client-secret
FASTMCP_SERVER_AUTH_INTROSPECTION_REQUIRED_SCOPES=api:read,api:write
```

A token the endpoint reports as inactive, or whose `exp` has passed, gets `401`. Results are cached for
`TOKEN_CACHE_TTL_SECONDS` (see below).

---

### Configuration Reference

#### `AUTH_MODE`
- **Default**: unset (the server refuses to start)
- **Values**: `none`, `jwt`, `introspection`, `static` (case-insensitive)
- **Example**: `AUTH_MODE=static`

#### `DEFAULT_TENANT`
- **Default**: `default`
- **Description**: The tenant of every request when `AUTH_MODE=none`. 1 to 255 characters; surrounding whitespace is trimmed.
- **Example**: `DEFAULT_TENANT=workstation`

#### `AUTH_STATIC_TOKEN_HASHES`
- **Default**: `{}`
- **Description**: JSON object of tenant name to the SHA-256 hex digest of that tenant's token. Required when `AUTH_MODE=static`.
- **Example**: `AUTH_STATIC_TOKEN_HASHES={"acme": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"}`

#### `TOKEN_CACHE_ENABLED`, `TOKEN_CACHE_TTL_SECONDS`, `TOKEN_CACHE_MAX_SIZE`
- **Default**: `true`, `300`, `1000`
- **Description**: Cache of verified tokens for the HTTP and REST paths. Applies to `jwt` and `introspection`; a static token is a hash lookup and needs no cache. Set `TOKEN_CACHE_ENABLED=false` for revocation to take effect on the next request.

#### Removed settings
`FASTMCP_SERVER_AUTH`, `FASTMCP_SERVER_AUTH_GITHUB_*`, `FASTMCP_SERVER_AUTH_GOOGLE_*`, `OAUTH_STORAGE_PATH`
and `DEFAULT_USER_ID`, `DEFAULT_USER_NAME`, `DEFAULT_USER_EMAIL` no longer exist. Replace
`FASTMCP_SERVER_AUTH=...JWTVerifier` with `AUTH_MODE=jwt` (and `...IntrospectionTokenVerifier` with
`AUTH_MODE=introspection`), and `DEFAULT_USER_ID` with `DEFAULT_TENANT`. Existing users in the database are
not migrated: a tenant is matched to its stored record by name, so rename `DEFAULT_TENANT` to the old
`DEFAULT_USER_ID` value to keep serving the memories stored under it.

---

### Security Best Practices

1. **Production deployments**:
   - Pick a credential mode (`jwt`, `introspection` or `static`); use `none` only on a loopback bind
   - Use HTTPS for all endpoints (put a TLS-terminating proxy in front)
   - Rotate client secrets and static tokens regularly
   - Keep tokens out of the configuration: static tokens are stored as hashes only

2. **Token scopes**:
   - Define required scopes: `FASTMCP_SERVER_AUTH_*_REQUIRED_SCOPES`
   - Implement least-privilege access
   - Forgetful narrows the tools a token can use from its `scope` (or `scp`) claim, given as a
     space-separated string or a list. Forgetful scopes are `*`, `read`, `write`, `read:<category>`
     and `write:<category>`, applied within the `FORGETFUL_SCOPES` ceiling. Any other scope
     (`openid`, `email`, another API's scope) is ignored.
   - A token that carries scopes but none of the Forgetful ones is denied every tool, so add one
     (for example `*`) to tokens whose scopes are only verifier requirements such as `api:read`.
     A token with no scope claim at all gets the full `FORGETFUL_SCOPES` ceiling. A static token
     carries no scopes and gets the ceiling.

3. **Development vs production**:
   ```bash
   # Development - one local tenant, loopback bind only
   AUTH_MODE=none
   SERVER_HOST=127.0.0.1

   # Production - JWT required
   AUTH_MODE=jwt
   FASTMCP_SERVER_AUTH_JWT_JWKS_URI=https://auth.company.com/.well-known/jwks.json
   FASTMCP_SERVER_AUTH_JWT_ISSUER=https://auth.company.com
   FASTMCP_SERVER_AUTH_JWT_AUDIENCE=forgetful-prod
   ```

---

### Troubleshooting

**"No authentication mode is configured, so the server will not start"**:
- Set `AUTH_MODE` (see the table above). For a local server, `AUTH_MODE=none` with `SERVER_HOST=127.0.0.1`

**"AUTH_MODE=none is accepted only when the HTTP server is bound to a loopback address"**:
- Bind `SERVER_HOST=127.0.0.1` (or `::1`), or choose `jwt`, `introspection` or `static`

**"Authentication required but no bearer token provided" / "Missing or invalid Authorization header"**:
- Client must send `Authorization: Bearer <token>`
- Check MCP client configuration

**"Token contains no 'tenant_id', 'tenant' or 'sub' claim"**:
- The token names no tenant; configure the identity provider to include one of those claims
- Verify the token with a JWT debugger

**401 Unauthorized**:
- Token expired, invalid signature, wrong audience, inactive at the introspection endpoint, or not a configured static token
- Enable debug logging: `LOG_LEVEL=DEBUG` (tokens are never logged)

---

## CLI Configuration

The `forgetful` CLI executes the same tool registry as the MCP server - locally by
default, or against a remote deployment.

### `FORGETFUL_SERVER`

- **Default**: `` (empty - local mode)
- **Purpose**: Remote deployment URL the CLI executes against. The URL is normalized
  before use:
  - **Scheme**: when omitted, `http://` is assumed for loopback hosts
    (`localhost`, `127.0.0.1`, `[::1]`) and `https://` for every other host (secure by
    default). An explicit `http://`/`https://` scheme is kept (and lower-cased, so
    `HTTPS://host` works); any other scheme (e.g. `ftp://`) is rejected.
  - **Mount path**: when the URL has no path, the default `/mcp` mount path is set. An
    explicit path is left untouched, and a query string or fragment is preserved, so
    normalizing an already-normalized URL changes nothing.

  So `localhost:8020` becomes `http://localhost:8020/mcp` and `forgetful.example.com`
  becomes `https://forgetful.example.com/mcp`.

  Do not put credentials in the URL (`https://user:password@host`). `forgetful auth
  login` drops them before saving the URL, and `auth status` and CLI error messages mask
  them. Use `FORGETFUL_TOKEN` (below) to authenticate.
- **Set by**: `forgetful auth login --server URL` (written to `~/.config/forgetful/.env`),
  or manually via shell env / any `.env` file in the standard list.

### `FORGETFUL_TOKEN`

- **Default**: unset
- **Purpose**: Bearer token the CLI sends to a remote deployment (a JWT, an introspected
  token or a static token, as the server is configured). Read from the shell environment
  only - deliberately never stored in `.env` files or anywhere else. When unset, the CLI
  sends no credential, which only a server running `AUTH_MODE=none` accepts. The token names
  the tenant, so the CLI reaches only that tenant's memories. In local mode the CLI is the
  `DEFAULT_TENANT` tenant. Surrounding whitespace (such as a trailing newline from an env file or CI secret) is
  stripped and a blank value counts as unset. A value that still contains control or
  non-ASCII characters is rejected with an error that does not repeat the token.

### Remote request timeouts

The CLI bounds how long it waits on a remote deployment, so a URL that answers with a
non-MCP page (reverse-proxy error page, SPA catch-all, wrong `--server`) fails with
`Error: ...` and exit code 1 instead of hanging. The MCP handshake gets 30 seconds and
each request 300 seconds.

### Configuration precedence (highest wins)

1. Per-invocation flags: `--server URL` / `--local`
2. Shell environment: `FORGETFUL_SERVER`, `FORGETFUL_TOKEN`
3. User config file: `~/.config/forgetful/.env` (`forgetful auth login --server URL` saves `FORGETFUL_SERVER` there)
4. Defaults: local mode, SQLite at the platform data path, TensorRT embeddings and TensorRT reranking (both need an NVIDIA GPU)

### `forgetful auth`

`forgetful auth login --server URL` checks the connection (sending `FORGETFUL_TOKEN`, if set) and saves
`FORGETFUL_SERVER` to the user config file. There is no interactive sign-in and nothing is cached on
disk. `forgetful auth status` is read-only: it reports the active mode and, in remote mode, sends
`FORGETFUL_TOKEN` (or nothing) as a plain bearer to prove the credentials work. A `401` is reported as
not authenticated.

---

## Memory Configuration

These settings control the atomic memory system's behavior and constraints.

The length and count limits below are checked when a memory is created or updated. Lowering
one later does not make memories that were stored under the old limit unreadable.

### `MEMORY_TITLE_MAX_LENGTH`
- **Default**: `200`
- **Description**: Maximum characters for memory titles
- **Rationale**: Titles must be "easily titled" and scannable at a glance
- **Atomic Memory Principle**: Force concise, clear titles
- **Example**: `MEMORY_TITLE_MAX_LENGTH=200`

### `MEMORY_CONTENT_MAX_LENGTH`
- **Default**: `2000`
- **Description**: Maximum characters for memory content (~300-400 words)
- **Rationale**: Enforces single-concept atomic memories (Zettelkasten principle)
- **Note**: For longer content, split it into several atomic memories and link them
- **Example**: `MEMORY_CONTENT_MAX_LENGTH=2000`

### `MEMORY_CONTEXT_MAX_LENGTH`
- **Default**: `500`
- **Description**: Maximum characters for memory context field
- **Purpose**: Brief explanation of WHY this memory matters, HOW it relates, WHAT implications
- **Example**: `MEMORY_CONTEXT_MAX_LENGTH=500`

### `MEMORY_KEYWORDS_MAX_COUNT`
- **Default**: `10`
- **Description**: Maximum number of keywords per memory
- **Purpose**: Semantic clustering and search optimization
- **Example**: `MEMORY_KEYWORDS_MAX_COUNT=10`

### `MEMORY_TAGS_MAX_COUNT`
- **Default**: `10`
- **Description**: Maximum number of tags per memory
- **Purpose**: Categorization and filtering
- **Example**: `MEMORY_TAGS_MAX_COUNT=10`

### `MEMORY_KEYWORD_MAX_LENGTH`
- **Default**: `100`
- **Description**: Maximum characters for a single memory keyword (after trimming whitespace)
- **Purpose**: Keywords are search terms, not sentences. Bounding each item also bounds the text that is embedded and token-counted for every search that returns the memory
- **Note**: Applies when memories are created or updated; memories stored earlier with longer keywords stay readable
- **Example**: `MEMORY_KEYWORD_MAX_LENGTH=100`

### `MEMORY_TAG_MAX_LENGTH`
- **Default**: `100`
- **Description**: Maximum characters for a single memory tag (after trimming whitespace)
- **Purpose**: Tags are short category labels; see `MEMORY_KEYWORD_MAX_LENGTH` for why each item is bounded
- **Note**: Applies when memories are created or updated; memories stored earlier with longer tags stay readable
- **Example**: `MEMORY_TAG_MAX_LENGTH=100`

### `MEMORY_SOURCE_FILE_MAX_LENGTH`
- **Default**: `4096`
- **Description**: Maximum characters for a single `source_files` path (after trimming whitespace)
- **Purpose**: Keeps provenance paths bounded; the default matches the Linux `PATH_MAX`
- **Note**: Applies when memories are created or updated; memories stored earlier stay readable
- **Example**: `MEMORY_SOURCE_FILE_MAX_LENGTH=4096`

### `MEMORY_TOKEN_BUDGET`
- **Default**: `8000`
- **Description**: Maximum tokens for query results (protects LLM context window)
- **Behavior**: System prioritizes by importance, then truncates to fit budget (`truncated: true` in the response)
- **Note**: Increase if you have larger context windows; decrease for smaller models. A REST search request can lower the budget with `token_context_threshold` but never exceed this value
- **Example**: `MEMORY_TOKEN_BUDGET=8000`

### `MEMORY_MAX_MEMORIES`
- **Default**: `20`
- **Description**: Maximum number of memories returned per query
- **Behavior**: Hard limit regardless of token budget; memories dropped by this limit are reported with `truncated: true`
- **Example**: `MEMORY_MAX_MEMORIES=20`

### `MEMORY_NUM_AUTO_LINK`
- **Default**: `3`
- **Description**: Number of similar memories to automatically link on creation
- **Values**:
  - `0` - Disable auto-linking
  - `1-10` - Number of links to create
- **Rationale**: Builds knowledge graph automatically
- **Example**: `MEMORY_NUM_AUTO_LINK=5`

### `MEMORY_SIMILARITY_THRESHOLD`
- **Default**: `0.7`
- **Description**: Minimum cosine similarity required for an automatic memory link
- **Behavior**: Applies on creation and embedding rebuild; existing links are not removed
- **Example**: `MEMORY_SIMILARITY_THRESHOLD=0.8`

### `OBSOLETE_WARNING_ENABLED`
- **Default**: `true`
- **Description**: Warn when a created memory is similar to superseded memories
- **Behavior**: Warning-only in response payload, max 3 matches, same tenant, all project scopes; never blocks creation; adds one vector query per `create_memory`; when `false`, `obsolete_matches` is always `[]` and no query runs
- **Example**: `OBSOLETE_WARNING_ENABLED=false`

### `OBSOLETE_WARNING_THRESHOLD`
- **Default**: `0.89`
- **Description**: Minimum cosine similarity for obsolete-memory warnings
- **Behavior**: Validated in (0, 1]; ignored when OBSOLETE_WARNING_ENABLED is false
- **Example**: `OBSOLETE_WARNING_THRESHOLD=0.92`

---

## Project Scope

A project is an optional free-form scope label on a memory (the `project` argument of `create_memory`,
`update_memory`, `query_memory` and `get_recent_memories`; `-p/--project` on the CLI). It is trimmed, 1 to
500 characters, and created the first time it is used. There is nothing to configure and no project records
to manage. Each tenant has its own scopes: the same label used by two tenants is two separate scopes.

---

## Provenance Defaults

These settings allow you to stamp every object created through a Forgetful server instance with identifying information about the agent and software doing the encoding. Useful when multiple agents or tools write to the same knowledge base.

Values are checked at startup against the length limits of the fields they are copied into (noted per setting below); an over-long value stops startup with an error naming the setting, instead of failing every create later.

### `ENCODING_AGENT`
- **Default**: `""` (empty, not applied)
- **Description**: Software or tool running the agent (e.g., the AI coding tool or script)
- **Note**: At most 100 characters
- **Example**: `ENCODING_AGENT=OpenCode`

### `ENCODING_VERSION`
- **Default**: `""` (empty, not applied)
- **Description**: Version of the encoding software
- **Note**: At most 50 characters
- **Example**: `ENCODING_VERSION=1.3.13`

### `AGENT_ID`
- **Default**: `""` (empty, not applied)
- **Description**: Identity of the agent doing the encoding (logical name, not an instance ID)
- **Note**: At most 100 characters
- **Example**: `AGENT_ID=CodeAgentUltra`

### `AGENT_VERSION`
- **Default**: `""` (empty, not applied)
- **Description**: Version of the agent
- **Note**: At most 50 characters
- **Example**: `AGENT_VERSION=1.0`

### `AGENT_MODEL`
- **Default**: `""` (empty, not applied)
- **Description**: LLM model the agent is running on
- **Note**: At most 100 characters
- **Example**: `AGENT_MODEL=claude-sonnet-4-6`

### `ENFORCE_ENV_OVERWRITE`
- **Default**: `false`
- **Description**: When `true`, environment-level provenance values override any values provided by the calling agent. Use this to enforce consistent provenance across a shared server regardless of what individual agents pass in.
- **Values**: `true`, `false`
- **Example**: `ENFORCE_ENV_OVERWRITE=true`

### Example Configuration

```bash
# Tag all writes from this server with the encoding agent and model
ENCODING_AGENT=OpenCode
ENCODING_VERSION=1.3.13
AGENT_ID=CodeAgentUltra
AGENT_VERSION=1.0
AGENT_MODEL=claude-sonnet-4-6
# Prevent individual agents from overriding these values
ENFORCE_ENV_OVERWRITE=true
```

---

## Search Configuration

### `EMBEDDING_PROVIDER`
- **Default**: `TensorRT`
- **Description**: Embedding generation provider
- **Current Support**: TensorRT | FastEmbed | Azure | Google | OpenAI | Ollama | gateway
- **Values**:
  - `TensorRT` - The model runs in the server process on an NVIDIA GPU with TensorRT, in strict fp32 (CLS pooling, unit-length vectors). No other service is needed. There is no CPU path and no fallback: a server that cannot start the engine refuses to start and says why. See [GPU models](gpu-models.md)
  - `FastEmbed` - The earlier local ONNX Runtime embedder; it needs `EMBEDDING_MODEL=BAAI/bge-small-en-v1.5` or another FastEmbed model name, and is slated for removal
  - `Azure`, `Google`, `OpenAI`, `Ollama` - Remote providers, configured below
  - `gateway` - An optional HTTP embeddings gateway, configured below
- **Note**: Matched case-insensitively. Any other value stops startup with an error naming the setting; an empty value keeps the default. Changing the provider needs a re-embed of all existing memories, see the [Embedding Migration Guide](./embedding_migration.md). `gateway` is optional: no `GATEWAY_*` setting is read or required while another provider is selected, and the default configuration needs no network access once the model files are on disk
- **Example**: `EMBEDDING_PROVIDER=TensorRT`

### `EMBEDDING_MODEL`
- **Default**: `BAAI/bge-small-en-v1.5`
- **Description**: Embedding model identifier
- **Properties**: 384 dimensions, optimized for semantic similarity
- **Note**: For `TensorRT`, a Hugging Face repository id (`owner/name`) holding `onnx/model.onnx` and `tokenizer.json` of a BERT-style model whose first token (`[CLS]`) carries the embedding, as the bge models define it. The default is fetched at a pinned revision, and `Xenova/bge-small-en-v1.5` is the same file (same SHA-256). For the other providers it is the model name that provider expects. Changing this requires re-embedding all existing memories. See [Embedding Migration Guide](./embedding_migration.md)
- **Example**: `EMBEDDING_MODEL=BAAI/bge-small-en-v1.5`

### `EMBEDDING_DIMENSIONS`
- **Default**: `384`
- **Description**: Vector dimensions for embeddings
- **Note**: Must match the model's output dimensions. With `TensorRT` the server embeds one text at startup and refuses to start with an error that names both widths when they differ; with `gateway` every reply is checked. Changing it for an existing database needs `--re-embed`
- **Example**: `EMBEDDING_DIMENSIONS=384`

### `EMBEDDING_TF32`
- **Default**: `false`
- **Values**: `true`, `false`
- **Description**: Let the embeddings engine build use TF32 tensor-core math, which is faster on recent NVIDIA GPUs and no longer bit-exact fp32
- **Note**: Only used when `EMBEDDING_PROVIDER=TensorRT`. The default builds a strict fp32 engine (the TensorRT TF32 flag is cleared explicitly). fp16 and bf16 are not supported at all. Changing the value changes the engine fingerprint and rebuilds the engine on the next start
- **Example**: `EMBEDDING_TF32=true`

### `EMBEDDING_MODEL_DIR`
- **Default**: `models/embedder` in the platform user data directory (e.g., `~/.local/share/forgetful/models/embedder`)
- **Description**: Where the TensorRT embedder keeps the model files (`onnx/model.onnx`, `tokenizer.json`) and a `manifest.json` with their SHA-256, under a directory named after the repository (`BAAI--bge-small-en-v1.5`)
- **Note**: Only used when `EMBEDDING_PROVIDER=TensorRT`. Files already present are never downloaded, so a host without internet access can be seeded by copying the directory, and `HF_HUB_OFFLINE=1` then keeps the Hugging Face client from trying (see [Offline Setup Guide](OFFLINE_SETUP.md)). A file that no longer matches its recorded SHA-256 stops startup
- **Example**: `EMBEDDING_MODEL_DIR=/var/lib/forgetful/models/embedder`

### `EMBEDDING_ENGINE_CACHE_DIR`
- **Default**: `models/tensorrt` in the platform user data directory (e.g., `~/.local/share/forgetful/models/tensorrt`), the same default as `RERANKING_ENGINE_CACHE_DIR`
- **Description**: Where the built embeddings engine is cached, one directory per fingerprint, inside a directory named after the model
- **Note**: Only used when `EMBEDDING_PROVIDER=TensorRT`. The fingerprint covers the model SHA-256, TensorRT version, GPU name, compute capability, driver major version, `EMBEDDING_TF32` and the optimization profile (batch 1 to 64, sequence 2 to 512); a change in any of them rebuilds the engine. The embedder and the reranker may share one cache directory because each engine has a directory of its own. See [GPU models](gpu-models.md#engine-cache)
- **Example**: `EMBEDDING_ENGINE_CACHE_DIR=/var/lib/forgetful/models/tensorrt`

### `EMBEDDING_ONNX_PROVIDERS`
- **Default**: (empty string)
- **Values**: Comma-separated ONNX Runtime execution provider names, in priority order
- **Description**: Execution providers for the local FastEmbed embedding model
- **Note**: Only used when `EMBEDDING_PROVIDER=FastEmbed`. Empty keeps FastEmbed's default provider selection. Every listed provider must be available in the installed ONNX Runtime package (for example `onnxruntime-directml` for `DmlExecutionProvider`): FastEmbed refuses to load the model when one is missing
- **Example**: `EMBEDDING_ONNX_PROVIDERS=DmlExecutionProvider,CPUExecutionProvider`

### `EMBEDDING_REQUEST_TIMEOUT_SECONDS`
- **Default**: `60`
- **Values**: Positive number of seconds
- **Description**: Per-request timeout for the remote embedding providers (Azure, Google, OpenAI, Ollama and the gateway), so a server that accepts a connection but never answers fails the request instead of hanging it
- **Note**: Ignored by TensorRT and FastEmbed (local). The OpenAI and Azure SDK clients retry failed requests twice, so a single call can take up to three times this value; the gateway adapter makes up to four attempts per request. Raise it if your server needs longer to answer, for example while an Ollama model is loading
- **Example**: `EMBEDDING_REQUEST_TIMEOUT_SECONDS=120`

### `DENSE_SEARCH_CANDIDATES`
- **Default**: `20`
- **Description**: Number of candidates to retrieve from dense (embedding) search before re-ranking
- **Performance**: Higher values = better recall, slower queries
- **Note**: A search for `k` results always fetches at least `k` candidates, so values below `k` never reduce the number of results. Reranking only reorders candidates when there are more of them than `k`
- **Example**: `DENSE_SEARCH_CANDIDATES=100`

### Azure Embedding Provider Configuration

**Note**: These settings only apply when `EMBEDDING_PROVIDER=Azure`. `AZURE_ENDPOINT`, `AZURE_DEPLOYMENT`, `AZURE_API_VERSION` and `AZURE_API_KEY` are all required: startup fails with an error naming the missing ones.

#### `AZURE_ENDPOINT`
- **Default**: (empty string)
- **Description**: Azure OpenAI service endpoint URL
- **Format**: `https://<resource-name>.openai.azure.com/`
- **Example**: `AZURE_ENDPOINT=https://my-openai.openai.azure.com/`

#### `AZURE_DEPLOYMENT`
- **Default**: (empty string)
- **Description**: Azure OpenAI embedding model deployment name
- **Example**: `AZURE_DEPLOYMENT=text-embedding-ada-002`

#### `AZURE_API_VERSION`
- **Default**: (empty string)
- **Description**: Azure OpenAI API version to use
- **Example**: `AZURE_API_VERSION=2023-05-15`

#### `AZURE_API_KEY`
- **Default**: (empty string)
- **Description**: Azure OpenAI API authentication key
- **Security**: **Keep this secret** - never commit to version control
- **Example**: `AZURE_API_KEY=your-azure-api-key-here`

#### `AZURE_SUPPORTS_DIMENSIONS`
- **Default**: `true`
- **Description**: Whether the deployment supports the `dimensions` parameter (`EMBEDDING_DIMENSIONS` is sent with every request when enabled)
- **Note**: Set to `false` for `text-embedding-ada-002` deployments, which reject the parameter with an HTTP 400. `text-embedding-3-*` deployments support it
- **Example**: `AZURE_SUPPORTS_DIMENSIONS=false`

### Google Embedding Provider Configuration

**Note**: These settings only apply when `EMBEDDING_PROVIDER=Google`

#### `GOOGLE_AI_API_KEY`
- **Default**: (empty string)
- **Description**: Google AI API key for embedding generation
- **Security**: **Keep this secret** - never commit to version control
- **Example**: `GOOGLE_AI_API_KEY=your-google-api-key-here`

### OpenAI Embedding Provider Configuration

**Note**: These settings only apply when `EMBEDDING_PROVIDER=OpenAI`

> **Dimension mismatch warning**: If switching from another provider (e.g., TensorRT at 384 dimensions) to OpenAI, existing memory embeddings will be incompatible. You must re-embed all existing memories after changing providers. See the [Embedding Migration Guide](./embedding_migration.md) for instructions.

#### `OPENAI_API_KEY`
- **Default**: (empty string)
- **Description**: OpenAI API key for embedding generation
- **Security**: **Keep this secret** - never commit to version control
- **Example**: `OPENAI_API_KEY=sk-your-openai-api-key-here`

#### `OPENAI_BASE_URL`
- **Default**: (empty string)
- **Description**: Custom base URL for OpenAI-compatible endpoints (llama.cpp, vLLM, LiteLLM)
- **Note**: When set, `OPENAI_API_KEY` becomes optional (a placeholder is used automatically)
- **Tip**: See the [llama.cpp migration scenario](./embedding_migration.md#openai-compatible-local-server-llamacpp-vllm) for server configuration tips (batch size, etc.)
- **Example**: `OPENAI_BASE_URL=http://localhost:8080/v1`

#### `OPENAI_SUPPORTS_DIMENSIONS`
- **Default**: `true`
- **Description**: Whether the endpoint supports the `dimensions` parameter
- **Note**: Set to `false` for endpoints that error on the dimensions param (e.g. llama.cpp)
- **Example**: `OPENAI_SUPPORTS_DIMENSIONS=false`

### Ollama Embedding Provider Configuration

**Note**: These settings only apply when `EMBEDDING_PROVIDER=Ollama`

> Ollama uses native async via the `ollama` Python SDK. No API key required (Ollama runs locally). Install with `pip install forgetful-ai[ollama]`.

#### `OLLAMA_BASE_URL`
- **Default**: `http://localhost:11434`
- **Description**: Ollama server URL
- **Example**: `OLLAMA_BASE_URL=http://localhost:11434`

### Gateway Embedding Provider Configuration

**Note**: These settings only apply when `EMBEDDING_PROVIDER=gateway`; they are not read otherwise. `GATEWAY_URL`, `GATEWAY_BACKEND`, `GATEWAY_MODEL` and `GATEWAY_AGENT_ID` are required: startup fails with an error naming the missing ones.

The provider posts JSON to an HTTP embeddings gateway: `POST {GATEWAY_URL}{GATEWAY_PATH}` with the fields `request_id`, `agent_id`, `backend`, `model`, `input` (1 to 256 texts, in order), `metadata` and, when enabled, `dimensions`. The reply carries one vector per text in input order. A call with more than 256 texts is split into requests of at most 256. Each request has a fresh `request_id`, an `X-Idempotency-Key` that stays the same when that request is retried, and an `X-Correlation-Id` shared by all requests of one call.

A reply is checked before anything is stored: the number of vectors must equal the number of texts, every vector must be exactly `EMBEDDING_DIMENSIONS` numbers wide, and no number may be NaN or infinite. Otherwise the call fails with an error that names the mismatch. Statuses 429 and 503 are retried, waiting for `Retry-After` (at most 30 seconds), and 502, 504 and network errors are retried with a growing wait, up to four attempts in all. 403 (for example a budget or model refusal), 422 and every other status fail at once. Error messages carry the status and the gateway's `code` only, never the response body, the token or the texts.

> **Re-embed required**: vectors from the gateway's model are not comparable with vectors from any other model. After switching to or from `gateway`, set `EMBEDDING_DIMENSIONS` to the width the gateway's model returns and run `forgetful --re-embed`. See the [Embedding Migration Guide](./embedding_migration.md).

#### `GATEWAY_URL`
- **Default**: (empty string)
- **Description**: Base URL of the gateway, `http://` or `https://`
- **Note**: Must not contain credentials, a query or a fragment; use `GATEWAY_TOKEN` and `GATEWAY_PATH`
- **Example**: `GATEWAY_URL=https://embeddings.example.net`

#### `GATEWAY_PATH`
- **Default**: `/api/v1/embeddings`
- **Description**: Path appended to `GATEWAY_URL`
- **Example**: `GATEWAY_PATH=/api/v1/embeddings`

#### `GATEWAY_TOKEN`
- **Default**: (empty string)
- **Description**: Credential for the gateway. With the default `GATEWAY_AUTH_HEADER` it is sent as `Authorization: Bearer <token>`; with any other header name the raw token is sent. Nothing is sent when it is empty
- **Security**: **Keep this secret** - never commit to version control. It is held as a secret value, so it does not appear in the settings' representation or in logs. It must be printable ASCII
- **Example**: `GATEWAY_TOKEN=your-gateway-token-here`

#### `GATEWAY_AUTH_HEADER`
- **Default**: `Authorization`
- **Description**: Name of the header that carries the token
- **Example**: `GATEWAY_AUTH_HEADER=X-Api-Key`

#### `GATEWAY_TENANT_HEADER`
- **Default**: `X-Tenant-Id`
- **Description**: Name of the header that carries `GATEWAY_TENANT`
- **Example**: `GATEWAY_TENANT_HEADER=X-Tenant-Id`

#### `GATEWAY_TENANT`
- **Default**: (empty string)
- **Description**: Tenant the gateway should account the requests to. The header is sent only when this is set
- **Example**: `GATEWAY_TENANT=acme`

#### `GATEWAY_BACKEND`
- **Default**: (empty string)
- **Description**: Value of the request's `backend` field: which of the gateway's backends serves the model
- **Example**: `GATEWAY_BACKEND=local`

#### `GATEWAY_MODEL`
- **Default**: (empty string)
- **Description**: Value of the request's `model` field. `EMBEDDING_MODEL` is not used by this provider
- **Example**: `GATEWAY_MODEL=embed-small`

#### `GATEWAY_AGENT_ID`
- **Default**: (empty string)
- **Description**: Value of the request's `agent_id` field, the caller identity the gateway sees
- **Example**: `GATEWAY_AGENT_ID=axmem`

#### `GATEWAY_METADATA`
- **Default**: `{}`
- **Description**: JSON object of string values sent as the request's `metadata` field. Axmem adds no values of its own
- **Note**: A value that is not a string stops startup
- **Example**: `GATEWAY_METADATA={"team": "memory"}`

#### `GATEWAY_SUPPORTS_DIMENSIONS`
- **Default**: `false`
- **Description**: Send `EMBEDDING_DIMENSIONS` as the request's optional `dimensions` field, for models that can return a shorter vector
- **Note**: The returned width must equal `EMBEDDING_DIMENSIONS` whether or not the field is sent
- **Example**: `GATEWAY_SUPPORTS_DIMENSIONS=true`

### Re-ranking Configuration

#### `RERANKING_ENABLED`
- **Default**: `true`
- **Description**: Enable re-ranking of search results for improved relevance
- **Values**: `true`, `false`
- **Note**: Improves search quality at the cost of additional processing. With the default `TensorRT` provider, `true` means an NVIDIA GPU is required: see [GPU models](gpu-models.md). `false` needs no GPU and no TensorRT libraries
- **Example**: `RERANKING_ENABLED=true`

#### `RERANKING_PROVIDER`
- **Default**: `TensorRT`
- **Description**: Re-ranking model provider
- **Current Support**: `TensorRT` | `HTTP`
- **Values**:
  - `TensorRT` - Local cross-encoder run directly on an NVIDIA GPU with TensorRT, in strict fp32. There is no CPU path and no fallback: a server that cannot start the engine refuses to start and says why
  - `HTTP` - Any server exposing a `/v1/rerank`-compatible endpoint (Jina, Cohere, vLLM, llama.cpp, Infinity, etc.)
- **Note**: Matched case-insensitively. Any other value (including the earlier `FastEmbed`) stops startup with an error naming the setting; an empty value keeps the default
- **Example**: `RERANKING_PROVIDER=HTTP`

#### `RERANKING_MODEL`
- **Default**: `Xenova/ms-marco-MiniLM-L-12-v2`
- **Description**: Re-ranking model identifier
- **Purpose**: Optimized for search result re-ranking
- **Note**: For `TensorRT`, a Hugging Face repository id (`owner/name`) holding `onnx/model.onnx` and `tokenizer.json`; the default is fetched at a pinned revision. For HTTP provider, set to the model name your endpoint expects (e.g., `jina-reranker-v2-base-multilingual`)
- **Example**: `RERANKING_MODEL=Xenova/ms-marco-MiniLM-L-12-v2`

#### `RERANKING_WORKERS`
- **Default**: `1`
- **Values**: Positive integers
- **Description**: Maximum reranking jobs running concurrently on one local adapter. Each worker owns one TensorRT execution context and CUDA stream on the shared engine
- **Note**: Only used when `RERANKING_PROVIDER=TensorRT`; does not limit HTTP requests. The embedder and one reranker worker together cost about 4.1 GiB of GPU memory per server process (one shared CUDA context) and every further worker about 1.8 GiB more, because an execution context reserves memory for the largest shape in the engine profile
- **Example**: `RERANKING_WORKERS=2`

The worker limit bounds active jobs, not the waiting queue. Cancelling a request cannot stop
inference already running in a thread; that worker stays occupied until inference finishes.
Restart the service after changing it. Each server process has its own adapter, engine and limits,
so several server processes on one GPU multiply the memory use. HTTP reranking already uses
asynchronous requests. Its processing capacity and rate limits are controlled by the remote
service, not this local setting.

#### `RERANKING_TF32`
- **Default**: `false`
- **Values**: `true`, `false`
- **Description**: Let the engine build use TF32 tensor-core math. TF32 keeps the 8-bit exponent of fp32 but only 10 mantissa bits in the multiplications, which is faster on recent NVIDIA GPUs and no longer bit-exact fp32
- **Note**: Only used when `RERANKING_PROVIDER=TensorRT`. The default builds a strict fp32 engine (the TensorRT TF32 flag is cleared explicitly). fp16 and bf16 are not supported at all. Changing the value changes the engine fingerprint and rebuilds the engine on the next start
- **Example**: `RERANKING_TF32=true`

#### `RERANKING_MODEL_DIR`
- **Default**: `models/reranker` in the platform user data directory (e.g., `~/.local/share/forgetful/models/reranker`)
- **Description**: Where the TensorRT reranker keeps the model files (`onnx/model.onnx`, `tokenizer.json`) and a `manifest.json` with their SHA-256
- **Note**: Only used when `RERANKING_PROVIDER=TensorRT`. Files already present are never downloaded, so an offline host can be seeded by copying the directory (see [Offline Setup Guide](OFFLINE_SETUP.md)). A file that no longer matches its recorded SHA-256 stops startup
- **Example**: `RERANKING_MODEL_DIR=/var/lib/forgetful/models/reranker`

#### `RERANKING_ENGINE_CACHE_DIR`
- **Default**: `models/tensorrt` in the platform user data directory (e.g., `~/.local/share/forgetful/models/tensorrt`)
- **Description**: Where built TensorRT engines are cached, one directory per fingerprint
- **Note**: Only used when `RERANKING_PROVIDER=TensorRT`. The fingerprint covers the model SHA-256, TensorRT version, GPU name, compute capability, driver major version, `RERANKING_TF32` and the optimization profile; a change in any of them rebuilds the engine, and directories of other fingerprints are removed and logged. See [GPU models](gpu-models.md#engine-cache)
- **Example**: `RERANKING_ENGINE_CACHE_DIR=/var/lib/forgetful/models/tensorrt`

#### `RERANKING_URL`
- **Default**: (empty string)
- **Description**: HTTP endpoint URL for re-ranking requests
- **Note**: Only used when `RERANKING_PROVIDER=HTTP`, where it is required: Forgetful refuses to start with `RERANKING_ENABLED=true` and an empty URL
- **Examples**:
  - Jina: `https://api.jina.ai/v1/rerank`
  - Cohere: `https://api.cohere.com/v2/rerank`
  - Local llama.cpp: `http://localhost:8012/v1/rerank`
  - Local vLLM: `http://localhost:8000/v1/rerank`
- **Example**: `RERANKING_URL=https://api.jina.ai/v1/rerank`

#### `RERANKING_API_KEY`
- **Default**: (empty string)
- **Description**: API key for authenticated re-ranking endpoints
- **Note**: Only used when `RERANKING_PROVIDER=HTTP`. Leave empty for local servers that don't require authentication
- **Security**: **Keep this secret** - never commit to version control
- **Example**: `RERANKING_API_KEY=your-api-key-here`

#### `RERANKING_TIMEOUT`
- **Default**: `60`
- **Values**: Positive number of seconds
- **Description**: How long an HTTP re-ranking request may take (connect, send, and wait for the response) before the search fails
- **Note**: Only used when `RERANKING_PROVIDER=HTTP`. Raise it for CPU-only rerank servers or services that load their model on first use, since each search scores up to `DENSE_SEARCH_CANDIDATES` documents in one request. Earlier versions allowed only 5 seconds and this was not configurable
- **Example**: `RERANKING_TIMEOUT=120`

### FastEmbed Cache Configuration

#### `FASTEMBED_CACHE_DIR`
- **Default**: Platform-specific user data directory (e.g., `~/.local/share/forgetful/models/fastembed`)
- **Description**: Directory where FastEmbed models are cached
- **Purpose**: Avoids re-downloading models on each startup
- **Note**: Can be customized for offline deployments (see [Offline Setup Guide](OFFLINE_SETUP.md))
- **Example**: `FASTEMBED_CACHE_DIR=/app/data/models/fastembed`

#### `FASTEMBED_LOCAL_FILES_ONLY`

- **Default**: `false`
- **Description**: Restrict the FastEmbed embedding model to files already present in `FASTEMBED_CACHE_DIR`. The TensorRT embedder and reranker are not affected: they never download files that are already in `EMBEDDING_MODEL_DIR` and `RERANKING_MODEL_DIR`
- **Purpose**: Prevents HuggingFace/GCS download attempts in locked-down or offline environments
- **Note**: When enabled, pre-populate the cache first or startup fails with a local-cache error
- **Example**: `FASTEMBED_LOCAL_FILES_ONLY=true`

---

## Configuration Hierarchy

Settings are resolved in this order (highest priority first):

1. **Environment variables** (from `.env` file or system environment)
2. **Defaults** (from `app/config/settings.py`)

---

## Common Configuration Scenarios

### Local Development
```bash
# .env
AUTH_MODE=none
SERVER_HOST=127.0.0.1
SERVER_PORT=8020
POSTGRES_HOST=127.0.0.1
LOG_LEVEL=DEBUG
LOG_FORMAT=console
```

### Production
```bash
# .env
AUTH_MODE=jwt
FASTMCP_SERVER_AUTH_JWT_JWKS_URI=https://auth.example.com/.well-known/jwks.json
FASTMCP_SERVER_AUTH_JWT_ISSUER=https://auth.example.com
FASTMCP_SERVER_AUTH_JWT_AUDIENCE=forgetful
SERVER_PORT=8020
SERVER_HOST=127.0.0.1
POSTGRES_HOST=db.internal.example.com
POSTGRES_USER=secure_user
POSTGRES_PASSWORD=<strong-password>
LOG_LEVEL=WARNING
LOG_FORMAT=json
```

### High-Volume / Large Context
```bash
# .env
MEMORY_TOKEN_BUDGET=16000
MEMORY_MAX_MEMORIES=50
DENSE_SEARCH_CANDIDATES=100
```

---

## Security Best Practices

1. **Always change database credentials in production**
   ```bash
   POSTGRES_USER=my_secure_user
   POSTGRES_PASSWORD=<use-a-strong-password>
   ```

2. **Use localhost binding for security**
   ```bash
   SERVER_HOST=127.0.0.1
   ```
   Binding another address requires an authentication mode other than `none`.

3. **Use structured logging in production**
   ```bash
   LOG_FORMAT=json
   LOG_LEVEL=WARNING
   ```

4. **Keep `.env` out of version control**
   - Already configured in `.gitignore`
   - Use `.env.example` as template

---

## Troubleshooting

### Database Connection Issues
- Verify `POSTGRES_HOST` and `PGPORT` match your PostgreSQL server (`127.0.0.1` and `5099` by default)
- Check the port with: `netstat -an | grep 5099`

### Port Conflicts
- Change `SERVER_PORT` and `PGPORT` if defaults conflict
- Update MCP client configuration

### Embedder or Reranker Does Not Start
- The server refuses to start with `Error: the embedder cannot start: <cause>` when `EMBEDDING_PROVIDER=TensorRT` cannot run, and with `Error: the reranker cannot start: <cause>` when `RERANKING_PROVIDER=TensorRT` cannot: no NVIDIA GPU visible to CUDA, TensorRT or CUDA Python libraries missing, driver too old, model files unavailable, an engine build failure, or (embedder only) an `EMBEDDING_DIMENSIONS` that differs from the model's width
- There is no CPU fallback. Fix the host (see [GPU models](gpu-models.md)), or choose another `EMBEDDING_PROVIDER` and set `RERANKING_PROVIDER=HTTP` or `RERANKING_ENABLED=false`

### Search Performance
- Increase `DENSE_SEARCH_CANDIDATES` for better results
- Decrease for faster queries
- Adjust `MEMORY_TOKEN_BUDGET` based on your LLM's context window

### Debug Mode
```bash
LOG_LEVEL=DEBUG
DB_LOGGING=true
```
� **Warning**: Very verbose output, use only for troubleshooting

---

## Additional Resources

- [README.md](../README.md) - Getting started guide
- [Connectivity Guide](connectivity_guide.md) - Connecting MCP clients
- [Search Documentation](search.md) - Search architecture details
- [Settings Source](../app/config/settings.py) - Default values and validation
