# Configuration Guide

This guide explains all available environment variables for configuring Axmem.

## Quick Start

All configuration is optional except authentication: Axmem uses sensible defaults from `app/config/settings.py` when no `.env` file exists, but the server refuses to start until `AUTH_MODE` is set (see [Authentication Configuration](#authentication-configuration)). For a local server, `AUTH_MODE=none` is enough on stdio or with `SERVER_HOST=127.0.0.1`.

To customize configuration, copy the reference file from the repository root and edit it. Axmem reads
`.env` from the directory it is started in and `~/.config/axmem/.env` (the user config file, which
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
- **Default**: `Axmem`
- **Description**: Display name for the service in logs and metrics
- **Example**: `SERVICE_NAME=MyMemoryService`

### `SERVICE_VERSION`
- **Default**: The installed package version
- **Description**: Version identifier for the service
- **Example**: `SERVICE_VERSION=v1.2.3`

### `SERVICE_DESCRIPTION`
- **Default**: `Axmem Memory Service`
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

### `REQUEST_BODY_MAX_BYTES`
- **Default**: `4194304` (4 MiB), at least `1024`
- **Description**: The largest request body the HTTP server accepts, for the REST routes and for `/mcp`. A request that declares a larger `Content-Length` is answered `413` without being read; a body that declares none (chunked) is counted as it arrives and answered `413` when it passes the limit. The server used to read any body whole into memory before looking at it
- **Note**: The default is far above what the limits of a memory allow (a memory with every field at its maximum is well under 1 MiB). Raise it only together with `MEMORY_CONTENT_MAX_LENGTH` or the other limits below if you raise those by a large factor
- **Example**: `REQUEST_BODY_MAX_BYTES=4194304`

### `CORS_ENABLED`, `CORS_ORIGINS`
- **Default**: `false`, `["*"]`
- **Description**: Adds CORS headers to the HTTP server for browser clients. `CORS_ORIGINS` is a JSON list of the origins that may call it. With `AUTH_MODE=none` and `CORS_ENABLED=true` the origins you name here (a `*` does not count) also pass the `Host`/`Origin` check of a credential-free server, see [Authentication Configuration](#authentication-configuration); any other cross-origin request is refused with `403`
- **Example**: `CORS_ENABLED=true`, `CORS_ORIGINS=["http://localhost:3001"]`

### `AXMEM_ALLOWED_HOSTS`
- **Default**: `[]`
- **Description**: A JSON list of host names, besides `localhost` and the loopback addresses, that a server running `AUTH_MODE=none` answers to (compared without regard to case and port; a blank entry or `*` names nothing). Needed when a client reaches the server under another name, such as `host.docker.internal` from a container on the same machine; without it that request is refused with `421`. Never list a name that a page you do not control can point at your machine
- **Example**: `AXMEM_ALLOWED_HOSTS=["host.docker.internal"]`

### `LOG_LEVEL`
- **Default**: `INFO`
- **Description**: Logging verbosity level
- **Values**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Note**: Matched case-insensitively, like `LOG_FORMAT`. Any other value stops every command with `Error: ...` naming `LOG_LEVEL` and exit code 1; an empty value keeps the default. Log messages and the values of `extra` fields are scanned for secrets (tokens, passwords, API keys, credentials in URLs) before they are written, and a text longer than 32768 characters is shortened to its head and tail first
- **Example**: `LOG_LEVEL=DEBUG` (for troubleshooting)

### `LOG_FORMAT`
- **Default**: `console`
- **Description**: Log output format
- **Values**:
  - `console` - Human-readable format (recommended for development)
  - `json` - Structured JSON format (recommended for production)
- **Colour**: `console` colours the level name only when stderr is a terminal and `NO_COLOR` is unset or empty (any other value turns colour off, see https://no-color.org); a pipe, a file or a log collector gets plain text
- **Note**: Matched case-insensitively. Any other value stops startup with an error naming the setting; an empty value keeps the default
- **Example**: `LOG_FORMAT=json`

### `MCP_DESCRIPTOR_MODE`

- **Default**: `verbose`
- **Description**: Controls how much guidance is embedded in the MCP meta-tool descriptions exposed to clients
- **Values**:
  - `verbose` - Include detailed inline guidance in `discover_axmem_tools` and `execute_axmem_tool`
  - `compact` - Keep meta-tool descriptions short and use `how_to_use_axmem_tool` for detailed schemas and examples
- **Example**: `MCP_DESCRIPTOR_MODE=compact`

---

## Qdrant Configuration

Qdrant is the only datastore. It holds the memories, their links and their vectors. Axmem needs Qdrant 1.19.0 or
newer (the tests pin `qdrant/qdrant:v1.19.2`) and checks the version of every node that answers at start. There is no SQL database
and no migration: the collections are created at start, and a change of the stored layout is a re-embed or an
export and import.

### `QDRANT_URL`
- **Default**: `http://127.0.0.1:6333`
- **Description**: The REST address of the Qdrant node, or of every node of a cluster separated by commas
- **Notes**:
  - Each entry starts with `http://` or `https://`. Requests are spread over the nodes and move on to the next one when a node cannot be reached
  - Several URLs must be the nodes of one cluster; listing separate Qdrant instances would split the data
- **Example**: `QDRANT_URL=http://q1.internal:6333,http://q2.internal:6333,http://q3.internal:6333`

### `QDRANT_API_KEY`
- **Default**: empty (no key)
- **Description**: The API key every node requires (`QDRANT__SERVICE__API_KEY` on the node)
- **Notes**:
  - A secret: it is held as a secret value and never logged, printed or returned
  - The key is instance wide. It admits every tenant, so tenant isolation is enforced by Axmem and by nothing in Qdrant: run one Qdrant per trust domain
  - On a cluster also set `QDRANT__SERVICE__ENFORCE_INTERNAL_AUTH=true` on the nodes so they authenticate to each other
- **Example**: `QDRANT_API_KEY=<long random value>`

### `QDRANT_COLLECTION_PREFIX`
- **Default**: `axmem`
- **Description**: Prefix of the collection names. `<prefix>_memories` is an alias onto the live generation of the memories (`<prefix>_memories_<generation>`), `<prefix>_links` holds the links
- **Values**: 1 to 64 letters, digits, underscores or hyphens
- **Note**: Two deployments that share a Qdrant need different prefixes

### `QDRANT_SHARD_NUMBER`, `QDRANT_REPLICATION_FACTOR`, `QDRANT_WRITE_CONSISTENCY_FACTOR`
- **Default**: `1`, `1`, `1` (one node)
- **Description**: The layout of a collection, fixed when the collection is created
- **Cluster profile** (verified by the cluster tests on three nodes): `3`, `3`, `2`. Writes are acknowledged by two of the three replicas, with strong write ordering, and a read-back uses quorum consistency
- **Notes**:
  - The write consistency factor cannot exceed the replication factor
  - An existing collection whose layout differs from these settings stops the server with `Qdrant collection <name> does not match the settings: ...`. The server never changes a collection silently: change the settings back, or run `axmem re-embed` (server stopped). A re-embed prints the layout the new collection gets, fills a new generation in it, and rebuilds the links collection in the same layout through a staging copy (`<prefix>_links_relayout`) that is removed when it is done; a run that stopped half way is finished by running it again, which copies the staged links back before it embeds anything (until then `axmem serve`, `axmem import`, `axmem backup` and `axmem export` refuse to run: the links are in the staging copy, and a new empty links collection is never taken for them)

### `QDRANT_TIMEOUT_SECONDS`
- **Default**: `30`
- **Description**: Timeout of one request to Qdrant. Keep it above the 7 to 8 seconds a cluster can need to forward a write to the leader of a shard

### `QDRANT_WRITE_ATTEMPTS`
- **Default**: `3` (1 to 10)
- **Description**: How many times one write is tried after an ambiguous failure (timeout, connection reset, 5xx). Every retry reuses the same claim token, so a write that did land is recognised and not applied twice

### `BACKUP_DIR`
- **Default**: empty (the `backups` directory in the platform data directory)
- **Description**: Where `axmem backup` writes its snapshot sets. `axmem export <file>` does not use it: it writes exactly the file you name (the parent directory must exist), owner-only like a backup, so keep exports in a directory that is as safe as this one
- **Notes**:
  - Created as `0700` if missing; the files themselves are `0600`
  - Must be persistent storage
- **Example**: `BACKUP_DIR=/var/backups/axmem`

### Operating a cluster

- **Consistency**: every write waits for Qdrant (`wait=true`) and uses strong ordering. A write that must not overwrite another writer (an update, an obsolete mark, a restore) is conditional on the version it read, and a lost race is retried a bounded number of times and then answered with 409 (`ConflictError`). Reads that decide a write or copy data away use quorum consistency (a majority of the replicas), so they still answer while one node is down: the memory an edit or an obsolete mark works on, the read-back of the outcome, the check that a superseding memory, a link or an imported memory exists, `get_memory_by_id`, `export`, `import` and the scans of a re-embed. Qdrant returns a point from a quorum read only while the replicas asked hold the same version of it, so a memory that is being rewritten at that moment can be missing from one answer: the reads that answer for one memory (an edit, an obsolete mark, a link, `get_memory_by_id`, and the read-back of the outcome of a write) repeat the read a few times and answer 409 (`ConflictError`, try again) when the replicas never agree, instead of reporting the memory missing or the write lost, on every REST route and as a tool error over MCP (for a read-back the answer says the outcome is unknown, the write may have been applied: read the memory and repeat the change if it is missing); `export` taken while writers are active can leave out a memory that is being rewritten, so quiesce the writers first. Each point remembers the claim tokens of its 16 latest versions, so a write that landed and was then overtaken by other writers is still recognised as applied and is not replayed over their changes. If more than 16 writes landed before the writer could read its own back, the call fails with 409 and says the outcome is unknown: read the memory and repeat the change if it is missing.
- **`axmem qdrant-check`** reads every node on its own (local reads) and lists the points on which the nodes differ. It only reports and exits with 1 when the nodes differ or a node cannot be read. A node that cannot be read is named (`<url>: unreadable (<error>)`) and the report says `N of M nodes could not be read, so the nodes were not compared in full: start them and run the check again.` The sentence `The nodes that answered do not agree on N points.` appears only when the nodes that answered differ. It never changes anything. Repair is the operator's decision.
- **Backups** are taken per node: a node's snapshot holds the shards that node stores, which with a replication factor below the number of nodes is only some of them. Quiesce the writers, run `axmem qdrant-check` until the nodes agree (with a replication factor above 1 `axmem backup` refuses to start while they do not), then `axmem backup` snapshots every node into one set; the snapshot is deleted from the node as soon as it is downloaded (or when the download fails), and one that cannot be deleted is named in the log; a snapshot that Qdrant takes for longer than 3600 seconds is not waited for and stays on the node unnamed, so list the snapshots of the node with the Qdrant API and delete it. `axmem backup` and `axmem export` refuse to run while the links of an unfinished `axmem re-embed` wait in `<prefix>_links_relayout` (a set would leave them out), and `axmem restore` removes that collection once the restored data is live. Restore (`axmem restore <dir> --yes`, server stopped) uploads every node's files back to the node they came from, with the snapshot as the source of truth, and then points the live alias at the restored generation. `QDRANT_URL` must therefore list the nodes the backup was taken from, in the same order, and the manifest must hold exactly one memories and one links snapshot for each (a backup of other nodes, or of the same addresses in another order, is refused before anything is uploaded; the manifest records the address each file was taken from, and a node that has moved to a new address is restored by its number). Each upload makes Qdrant mark the other replicas of the shards it restored for recovery (`Dead`, `Recovery`, `Partial`), and they catch up by shard transfers; `axmem restore` therefore waits after every file, up to 10 minutes, until all replicas of that collection are `Active`, and only then uploads the next one (files uploaded while another node is still recovering its replicas can leave every replica of a shard `Dead`, which nothing recovers by itself). A replica that is still not `Active` after 90 seconds (Qdrant can leave the replica of a shard it just restored in `ManualRecovery` for good when its own re-sync claims the shard at that moment, seen after a restore into an emptied Qdrant) makes `axmem restore` upload that file once more, which activates it. If the replicas still do not settle it stops and says which states it saw: look at the cluster, then run the restore again (it is repeatable). Run `axmem qdrant-check` before the writers start. This is covered by the cluster tests for replication factors 3, 2 and 1, over existing collections and over an emptied Qdrant, which also check that the embedder the memory generation records (`axmem_embedder` in the collection metadata) comes back with the snapshot. Aliases are not stored in snapshots; `axmem restore` points the live alias at the restored generation.
- **Second line of defence**: `axmem export <file>` writes every memory and link as JSON lines without vectors; `axmem import <file>` embeds each memory again with the configured provider. Keep exports as well as snapshots.
- **Embedding changes**: `EMBEDDING_DIMENSIONS`, the model, the provider, the local engine's precision (`EMBEDDING_TF32`) or the gateway backend and model changed means `axmem re-embed` builds a new generation next to the live one and switches the alias when it is complete. Each memory generation records the embedder that made its vectors, and the server refuses to start on one that another embedder made, even when the width is the same: `Qdrant collection <name> holds vectors made with <provider, model, ...>, but the settings describe <...>`. A collection made before this record existed is taken to be the configured embedder's and records it with a warning. A failed run leaves the live generation untouched. The previous generation is kept after success; delete it by hand once you no longer need it (see [Embedding Migration](embedding_migration.md)).

---

## Authentication Configuration

Axmem fails closed. The server has no default authentication mode: with `AUTH_MODE` unset it
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

The loopback check looks at the bind address only. Anything that forwards traffic to that loopback port (a reverse
proxy on the same host, an SSH port forward, a container port mapping) makes every caller the `DEFAULT_TENANT`
with no credential, so `AUTH_MODE=none` is safe only while nothing forwards to the port. A proxied or otherwise
exposed deployment must use `jwt`, `introspection` or `static`.

A web page on the same machine can reach a loopback port too, so with `AUTH_MODE=none` the HTTP server (the REST
routes and `/mcp`) also looks at the request itself. A request whose `Host` is not `localhost` or a loopback address
(or that `AXMEM_ALLOWED_HOSTS` does not name) is answered `421` (DNS rebinding makes a page's own host name resolve to `127.0.0.1`), and a request that carries an
`Origin` which is neither a loopback origin (`http://localhost:5173`, `http://127.0.0.1:3000`) nor one of the origins
named in `CORS_ORIGINS` (with `CORS_ENABLED=true`) is answered `403` (a cross-origin `no-cors` POST needs no preflight; `Origin: null` is
refused; a `*` in `CORS_ORIGINS` does not count). MCP clients, the CLI, `curl` and other programs that are not
browsers send neither, so they are not affected. The credential modes do not use this check: a page cannot attach a
bearer token.

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
255 characters, is refused rather than skipped. A tenant needs no record: its identity is derived from its name,
so it exists from its first request.
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
FASTMCP_SERVER_AUTH_JWT_AUDIENCE=axmem-api
FASTMCP_SERVER_AUTH_JWT_REQUIRED_SCOPES=api:read,api:write
```

Either `FASTMCP_SERVER_AUTH_JWT_JWKS_URI` or `FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY` is required, and so are
`FASTMCP_SERVER_AUTH_JWT_ISSUER` and `FASTMCP_SERVER_AUTH_JWT_AUDIENCE`: the server refuses to start without them,
because a token that the same identity provider issued for another application would otherwise be accepted. A token
is accepted when its signature, `iss`, `aud` and `exp` check out (and it carries the required scopes); an expired
token, a token without an `exp` claim (it would never expire), a token of another issuer or audience and an invalid
one get `401`. `nbf` (not before) is not checked.

### Token introspection (`AUTH_MODE=introspection`)

Validates opaque bearer tokens at an introspection endpoint (OAuth 2.0 RFC 7662).

```bash
# .env
AUTH_MODE=introspection
FASTMCP_SERVER_AUTH_INTROSPECTION_URL=https://auth.yourcompany.com/oauth/introspect
FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID=axmem-resource-server
FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET=your-client-secret
FASTMCP_SERVER_AUTH_INTROSPECTION_REQUIRED_SCOPES=api:read,api:write
FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE=axmem-api
```

A token the endpoint reports as inactive, or whose `exp` has passed, gets `401`, and so does one that lacks a
required scope. With `FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE` set, the `aud` of the endpoint's answer (a string or
a list) must name that audience, so an active token that the authorization server issued for another resource
server is refused; leave it unset only when the authorization server issues tokens for this server alone, because
nothing else tells the two apart. The issuer is not checked: the endpoint is the authority that says whether the
token is valid. Answers are cached for `TOKEN_CACHE_TTL_SECONDS` (see below), and that is the only cache in this mode,
so a revoked token is accepted for at most that long.

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

#### `FASTMCP_SERVER_AUTH_JWT_ISSUER`, `FASTMCP_SERVER_AUTH_JWT_AUDIENCE`
- **Default**: unset
- **Description**: Required when `AUTH_MODE=jwt` (the server refuses to start without both): the `iss` and the `aud` that a token must carry.
- **Example**: `FASTMCP_SERVER_AUTH_JWT_ISSUER=https://auth.yourcompany.com`, `FASTMCP_SERVER_AUTH_JWT_AUDIENCE=axmem-api`

#### `FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE`
- **Default**: empty (the audience of the introspection answer is not checked)
- **Description**: With `AUTH_MODE=introspection`, the audience that the `aud` of the introspection answer (a string or a list) must name. A token for another audience gets `401`.
- **Example**: `FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE=axmem-api`

#### `TOKEN_CACHE_ENABLED`, `TOKEN_CACHE_TTL_SECONDS`, `TOKEN_CACHE_MAX_SIZE`
- **Default**: `true`, `300`, `1000`
- **Description**: Cache of verified tokens. With `jwt` it spares the REST routes the signature check and keeps the tenant and the token's scopes; with `introspection` it is the verifier's own cache of the endpoint's answers, shared by MCP and REST (there is no second cache on top, so revocation takes at most one TTL); a static token is a hash lookup and needs no cache. Set `TOKEN_CACHE_ENABLED=false` for revocation to take effect on the next request.

#### Removed settings
`FASTMCP_SERVER_AUTH`, `FASTMCP_SERVER_AUTH_GITHUB_*`, `FASTMCP_SERVER_AUTH_GOOGLE_*`, `OAUTH_STORAGE_PATH`
and `DEFAULT_USER_ID`, `DEFAULT_USER_NAME`, `DEFAULT_USER_EMAIL` no longer exist. Replace
`FASTMCP_SERVER_AUTH=...JWTVerifier` with `AUTH_MODE=jwt` (and `...IntrospectionTokenVerifier` with
`AUTH_MODE=introspection`), and `DEFAULT_USER_ID` with `DEFAULT_TENANT`. The FastEmbed and ONNX Runtime settings
(`FASTEMBED_*`, `EMBEDDING_ONNX_PROVIDERS`, `RERANKING_ONNX_PROVIDERS`, `RERANKING_THREADS`) and the caps and
switches of the removed features (files, planning, skills, activity, documents, entities, code artifacts) are gone
too. `FORGETFUL_SERVER`, `FORGETFUL_TOKEN` and `FORGETFUL_SCOPES` became `AXMEM_SERVER`, `AXMEM_TOKEN` and
`AXMEM_SCOPES`. The server names every removed setting it finds in the environment or a dotenv file at start (names are
matched without regard to case). A `FORGETFUL_SCOPES` that `AXMEM_SCOPES` does not replace is the one that stops the
start of the server and of the CLI in local mode (`--local`, or no `--server` and no `AXMEM_SERVER`): it used to limit the tools, and `AXMEM_SCOPES` defaults to `*`, so the instance would allow every tool without
a word. Set `AXMEM_SCOPES` to the old value, or to `*` to allow every tool on purpose. The SQL databases are gone and nothing
is migrated in place, and `axmem import` reads only the `axmem-export` JSON lines format that `axmem export` writes, which
no earlier deployment produced: memories held in SQL cannot be carried over by a command and are created again (or
converted by hand to that format).

---

### Security Best Practices

1. **Production deployments**:
   - Pick a credential mode (`jwt`, `introspection` or `static`); use `none` only on a loopback bind that nothing forwards to (a proxy, a tunnel or a port mapping would make every caller the default tenant)
   - Use HTTPS for all endpoints (put a TLS-terminating proxy in front)
   - Rotate client secrets and static tokens regularly
   - Keep tokens out of the configuration: static tokens are stored as hashes only

2. **Token scopes**:
   - Define required scopes: `FASTMCP_SERVER_AUTH_*_REQUIRED_SCOPES`
   - Implement least-privilege access
   - `AXMEM_SCOPES` and the scopes of a token limit the REST routes exactly as they limit the MCP tools: a route
     answers `403` when the tool it stands for (listed in the [API reference](api_reference.md#scopes-and-routes))
     is not permitted, so a read-only instance cannot create, change, link or obsolete a memory over REST either.
   - Axmem narrows the tools a token can use from its `scope` (or `scp`) claim, given as a
     space-separated string or a list. Axmem scopes are `*`, `read`, `write`, `read:<category>`
     and `write:<category>`, applied within the `AXMEM_SCOPES` ceiling. Any other scope
     (`openid`, `email`, another API's scope) is ignored.
   - A token that carries scopes but none of the Axmem ones is denied every tool, so add one
     (for example `*`) to tokens whose scopes are only verifier requirements such as `api:read`.
     A token with no scope claim at all gets the full `AXMEM_SCOPES` ceiling. A static token
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
   FASTMCP_SERVER_AUTH_JWT_AUDIENCE=axmem-prod
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
- Token expired, without an `exp` claim, invalid signature, wrong issuer or audience, inactive at the introspection endpoint, or not a configured static token
- Enable debug logging: `LOG_LEVEL=DEBUG` (tokens are never logged)

**"AUTH_MODE=jwt requires both FASTMCP_SERVER_AUTH_JWT_ISSUER and FASTMCP_SERVER_AUTH_JWT_AUDIENCE"**:
- Set both to the `iss` and `aud` that your identity provider puts in the tokens for Axmem
- Only `axmem serve` refuses (it prints `Error: ...` and exits with code 1). `backup`, `export`, `re-embed` and the tool commands never serve a request and run with the same environment. The same goes for the other modes that lack settings: `static` without `AUTH_STATIC_TOKEN_HASHES`, `introspection` without its URL, client id or client secret

**403 "Tool '...' is not permitted under current scopes" from a REST route**:
- `AXMEM_SCOPES`, or the `scope`/`scp` claim of the token, does not permit the tool the route stands for; give the token a scope that does (for example `read` or `write:memories`)

**415 "Content-Type must be application/json"**:
- A REST request with a body must send `Content-Type: application/json`

**421 / 403 from a server running `AUTH_MODE=none`**:
- The request came with a `Host` that is not `localhost` or a loopback address (`421`), or an `Origin` that is not a loopback origin and not in `CORS_ORIGINS` with `CORS_ENABLED=true` (`403`). Reach the server through `127.0.0.1`, `::1` or `localhost` (or list the name in `AXMEM_ALLOWED_HOSTS`); a browser app on another origin must be named in `CORS_ORIGINS`. This is how a web page is kept from a credential-free server

**"FORGETFUL_SCOPES is set but is not read any more"**:
- Printed by `axmem serve` and by the tool commands that run in process (`--local`, or no `--server` and no `AXMEM_SERVER`); `backup`, `restore`, `export`, `import`, `qdrant-check`, `re-embed` and the tool commands that call a remote server do not use the scope ceiling and do not check it
- Set `AXMEM_SCOPES` (to the old value, or `*` to allow every tool on purpose) and remove `FORGETFUL_SCOPES`

---

## CLI Configuration

The `axmem` CLI executes the same tool registry as the MCP server - locally by
default, or against a remote deployment.

### `AXMEM_SERVER`

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

  So `localhost:8020` becomes `http://localhost:8020/mcp` and `axmem.example.com`
  becomes `https://axmem.example.com/mcp`.

  Do not put credentials in the URL (`https://user:password@host`). `axmem auth
  login` drops them before saving the URL, and `auth status` and CLI error messages mask
  them. Use `AXMEM_TOKEN` (below) to authenticate.
- **Set by**: `axmem auth login --server URL` (written to `~/.config/axmem/.env`),
  or manually via shell env / any `.env` file in the standard list.

### `AXMEM_TOKEN`

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
2. Shell environment: `AXMEM_SERVER`, `AXMEM_TOKEN`
3. User config file: `~/.config/axmem/.env` (`axmem auth login --server URL` saves `AXMEM_SERVER` there)
4. Defaults: local mode, Qdrant at `http://127.0.0.1:6333`, TensorRT embeddings and TensorRT reranking (both need an NVIDIA GPU)

### `axmem auth`

`axmem auth login --server URL` checks the connection (sending `AXMEM_TOKEN`, if set) and saves
`AXMEM_SERVER` to the user config file. There is no interactive sign-in and nothing is cached on
disk. `axmem auth status` is read-only: it reports the active mode and, in remote mode, sends
`AXMEM_TOKEN` (or nothing) as a plain bearer to prove the credentials work. A `401` is reported as
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

### `MEMORY_SOURCE_FILES_MAX_COUNT`
- **Default**: `50`
- **Description**: Maximum number of `source_files` paths per memory (REST and MCP, create and update). The paths count towards the size of the stored memory but not towards the token budget of a search, so without a cap a memory could be arbitrarily large and still look small
- **Note**: Applies when memories are created or updated; memories stored earlier stay readable
- **Example**: `MEMORY_SOURCE_FILES_MAX_COUNT=50`

### `MEMORY_QUERY_MAX_LENGTH`, `MEMORY_QUERY_CONTEXT_MAX_LENGTH`
- **Default**: `2000`, `2000`
- **Description**: Maximum characters of a search `query` and of its `query_context` (REST and MCP). Both go through the embedder and the reranker, whose time grows with their length and which serve every tenant through one worker each; a text above the limit is refused (`400` over REST, a tool error over MCP) before any model runs
- **Example**: `MEMORY_QUERY_MAX_LENGTH=2000`

### `MEMORY_OBSOLETE_REASON_MAX_LENGTH`
- **Default**: `500`
- **Description**: Maximum characters of the reason given to `mark_memory_obsolete` or `DELETE /api/v1/memories/{id}`. The reason must be a text that is not blank and can be encoded as UTF-8; a number, an object, a blank text or one above the limit is refused (`400` over REST, a tool error over MCP) and the memory stays as it was. The reason is stored and returned with every read of the memory
- **Example**: `MEMORY_OBSOLETE_REASON_MAX_LENGTH=500`

### `MEMORY_LINKS_MAX_PER_REQUEST`
- **Default**: `50`
- **Description**: Most memory ids that one `link_memories` or `unlink_memories` call, or one `POST /api/v1/memories/{id}/links`, may name (duplicates and the memory itself are dropped before counting on MCP)
- **Example**: `MEMORY_LINKS_MAX_PER_REQUEST=50`

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

These settings allow you to stamp every object created through an Axmem server instance with identifying information about the agent and software doing the encoding. Useful when multiple agents or tools write to the same knowledge base.

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
- **Current Support**: TensorRT | Azure | Google | OpenAI | Ollama | gateway
- **Values**:
  - `TensorRT` - The model runs in the server process on an NVIDIA GPU with TensorRT, in strict fp32 (CLS pooling, unit-length vectors). No other service is needed. There is no CPU path and no fallback: a server that cannot start the engine refuses to start and says why. See [GPU models](gpu-models.md)
  - `Azure`, `Google`, `OpenAI`, `Ollama` - Remote providers, configured below
  - `gateway` - An optional HTTP embeddings gateway, configured below
- **Note**: Matched case-insensitively. Any other value (including the removed `FastEmbed`) stops startup with an error naming the setting; an empty value keeps the default. Changing the provider needs a re-embed of all existing memories, see the [Embedding Migration Guide](./embedding_migration.md). `gateway` is optional: no `GATEWAY_*` setting is used or required while another provider is selected, and the default configuration needs no network access once the model files and the tiktoken data are on disk
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
- **Note**: Must match the model's output dimensions. With `TensorRT` the server embeds one text at startup and refuses to start with an error that names both widths when they differ; with `gateway`, `Azure`, `Google`, `OpenAI` and `Ollama` every reply is checked (the vector must be exactly this wide and hold no NaN or infinite number; a wrong width is refused with `<provider> returned N-dimensional embeddings but EMBEDDING_DIMENSIONS is M`, and a re-embedding then records the batch as failed at once). Changing it for existing memories needs `--re-embed`
- **Example**: `EMBEDDING_DIMENSIONS=384`

### `EMBEDDING_TF32`
- **Default**: `false`
- **Values**: `true`, `false`
- **Description**: Let the embeddings engine build use TF32 tensor-core math, which is faster on recent NVIDIA GPUs and no longer bit-exact fp32
- **Note**: Only used when `EMBEDDING_PROVIDER=TensorRT`. The default builds a strict fp32 engine (the TensorRT TF32 flag is cleared explicitly). fp16 and bf16 are not supported at all. Changing the value changes the engine fingerprint and rebuilds the engine on the next start
- **Example**: `EMBEDDING_TF32=true`

### `EMBEDDING_MODEL_DIR`
- **Default**: `models/embedder` in the platform user data directory (e.g., `~/.local/share/axmem/models/embedder`)
- **Description**: Where the TensorRT embedder keeps the model files (`onnx/model.onnx`, `tokenizer.json`) and a `manifest.json` with their SHA-256, under a directory named after the repository (`BAAI--bge-small-en-v1.5`)
- **Note**: Only used when `EMBEDDING_PROVIDER=TensorRT`. Files already present are never downloaded, so a host without internet access can be seeded by copying the directory, and `HF_HUB_OFFLINE=1` then keeps the Hugging Face client from trying (see [Model files and offline hosts](gpu-models.md#model-files-and-offline-hosts)). A file that no longer matches its recorded SHA-256 stops startup
- **Example**: `EMBEDDING_MODEL_DIR=/var/lib/axmem/models/embedder`

### `EMBEDDING_ENGINE_CACHE_DIR`
- **Default**: `models/tensorrt` in the platform user data directory (e.g., `~/.local/share/axmem/models/tensorrt`), the same default as `RERANKING_ENGINE_CACHE_DIR`
- **Description**: Where the built embeddings engine is cached, one directory per fingerprint, inside a directory named after the model
- **Note**: Only used when `EMBEDDING_PROVIDER=TensorRT`. The fingerprint covers the model SHA-256, TensorRT version, GPU name, compute capability, driver major version, `EMBEDDING_TF32` and the optimization profile (batch 1 to 64, sequence 2 to 512); a change in any of them rebuilds the engine. The embedder and the reranker may share one cache directory because each engine has a directory of its own. See [GPU models](gpu-models.md#engine-cache)
- **Example**: `EMBEDDING_ENGINE_CACHE_DIR=/var/lib/axmem/models/tensorrt`

### `EMBEDDING_REQUEST_TIMEOUT_SECONDS`
- **Default**: `60`
- **Values**: Positive number of seconds
- **Description**: Per-request timeout for the remote embedding providers (Azure, Google, OpenAI, Ollama and the gateway), so a server that accepts a connection but never answers fails the request instead of hanging it
- **Note**: Ignored by TensorRT (local). The OpenAI and Azure SDK clients retry failed requests twice, so a single call can take up to three times this value; the gateway adapter makes up to four attempts per request. Raise it if your server needs longer to answer, for example while an Ollama model is loading
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

> Ollama uses native async via the `ollama` Python SDK. No API key required (Ollama runs locally). Install it with `uv sync --extra ollama` from a checkout (`uv tool install ".[ollama]"` for the CLI).

#### `OLLAMA_BASE_URL`
- **Default**: `http://localhost:11434`
- **Description**: Ollama server URL
- **Example**: `OLLAMA_BASE_URL=http://localhost:11434`

### Gateway Embedding Provider Configuration

**Note**: These settings only apply when `EMBEDDING_PROVIDER=gateway`; they are loaded with the other settings but only the gateway provider uses them (a blank or malformed `GATEWAY_METADATA` cannot stop another provider). `GATEWAY_URL`, `GATEWAY_BACKEND`, `GATEWAY_MODEL` and `GATEWAY_AGENT_ID` are required: startup fails with an error naming the missing ones.

The provider posts JSON to an HTTP embeddings gateway: `POST {GATEWAY_URL}{GATEWAY_PATH}` with the fields `request_id`, `agent_id`, `backend`, `model`, `input` (1 to 256 texts, in order), `metadata` and, when enabled, `dimensions`. The reply carries one vector per text in input order. A call with more than 256 texts is split into requests of at most 256. Each request has a fresh `request_id`, an `X-Idempotency-Key` that stays the same when that request is retried, and an `X-Correlation-Id` shared by all requests of one call.

A reply is checked before anything is stored: the number of vectors must equal the number of texts, every vector must be exactly `EMBEDDING_DIMENSIONS` numbers wide, and no number may be NaN or infinite. Otherwise the call fails with an error that names the mismatch. Statuses 429 and 503 are retried, waiting for `Retry-After` (at most 30 seconds), and 502, 504 and network errors are retried with a growing wait, up to four attempts in all. 403 (for example a budget or model refusal), 422 and every other status fail at once. Error messages carry the status and the gateway's `code` only, never the response body, the token or the texts.

> **Re-embed required**: vectors from the gateway's model are not comparable with vectors from any other model. After switching to or from `gateway`, set `EMBEDDING_DIMENSIONS` to the width the gateway's model returns and run `axmem --re-embed`. See the [Embedding Migration Guide](./embedding_migration.md).

#### `GATEWAY_URL`
- **Default**: (empty string)
- **Description**: Base URL of the gateway, `http://` or `https://`
- **Note**: Must not contain credentials, a query or a fragment (an empty `?` or `#` counts: the path would be appended after it); use `GATEWAY_TOKEN` and `GATEWAY_PATH`
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
- **Note**: Read as text, so a blank or malformed value stops only the `gateway` provider (with `GATEWAY_METADATA must be a JSON object of string values`), never a server that uses another provider; a blank value means no metadata
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
- **Note**: Matched case-insensitively. Any other value (including the removed `FastEmbed`) stops startup with an error naming the setting; an empty value keeps the default
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
- **Default**: `models/reranker` in the platform user data directory (e.g., `~/.local/share/axmem/models/reranker`)
- **Description**: Where the TensorRT reranker keeps the model files (`onnx/model.onnx`, `tokenizer.json`) and a `manifest.json` with their SHA-256
- **Note**: Only used when `RERANKING_PROVIDER=TensorRT`. Files already present are never downloaded, so an offline host can be seeded by copying the directory (see [Model files and offline hosts](gpu-models.md#model-files-and-offline-hosts)). A file that no longer matches its recorded SHA-256 stops startup
- **Example**: `RERANKING_MODEL_DIR=/var/lib/axmem/models/reranker`

#### `RERANKING_ENGINE_CACHE_DIR`
- **Default**: `models/tensorrt` in the platform user data directory (e.g., `~/.local/share/axmem/models/tensorrt`)
- **Description**: Where built TensorRT engines are cached, one directory per fingerprint
- **Note**: Only used when `RERANKING_PROVIDER=TensorRT`. The fingerprint covers the model SHA-256, TensorRT version, GPU name, compute capability, driver major version, `RERANKING_TF32` and the optimization profile; a change in any of them rebuilds the engine, and directories of other fingerprints are removed and logged. See [GPU models](gpu-models.md#engine-cache)
- **Example**: `RERANKING_ENGINE_CACHE_DIR=/var/lib/axmem/models/tensorrt`

#### `RERANKING_URL`
- **Default**: (empty string)
- **Description**: HTTP endpoint URL for re-ranking requests
- **Note**: Only used when `RERANKING_PROVIDER=HTTP`, where it is required: Axmem refuses to start with `RERANKING_ENABLED=true` and an empty URL
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
QDRANT_URL=http://127.0.0.1:6333
LOG_LEVEL=DEBUG
LOG_FORMAT=console
```

### Production
```bash
# .env
AUTH_MODE=jwt
FASTMCP_SERVER_AUTH_JWT_JWKS_URI=https://auth.example.com/.well-known/jwks.json
FASTMCP_SERVER_AUTH_JWT_ISSUER=https://auth.example.com
FASTMCP_SERVER_AUTH_JWT_AUDIENCE=axmem
SERVER_PORT=8020
SERVER_HOST=127.0.0.1
QDRANT_URL=http://q1.internal:6333,http://q2.internal:6333,http://q3.internal:6333
QDRANT_API_KEY=<strong-random-value>
QDRANT_SHARD_NUMBER=3
QDRANT_REPLICATION_FACTOR=3
QDRANT_WRITE_CONSISTENCY_FACTOR=2
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

1. **Always set a Qdrant API key in production**
   ```bash
   QDRANT_API_KEY=<use-a-long-random-value>
   ```
   The key is instance wide; do not share one Qdrant between trust domains.

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

### Qdrant Connection Issues
- The server refuses to start with `Error: Qdrant at <url> cannot be reached or did not report its version` when no node answers: check `QDRANT_URL` and that the node runs. Qdrant answers its version request without a key, so a wrong or missing API key is found by the first real request and ends the start (and `re-embed`, `backup`, `export` and the other operator commands) with `Error: Qdrant refused the request (HTTP 401): check QDRANT_API_KEY` (HTTP 403 the same). The checks `serve` makes before it binds anything (the scope ceiling parses, the embedder and the reranker build, Qdrant answers and holds the layout the settings describe) all end as one `Error: <cause>` line with exit code 1, never as a traceback. A node that does not answer while another one does is logged (`A Qdrant node did not answer; going on with the others`) and skipped, so a cluster that lost a node still starts; every node that answers must still be version 1.19.0 or later
- `Settings of an earlier version are ignored (...)` at start: the environment or a dotenv file still names a setting of the SQL store, the OAuth providers, `DEFAULT_USER_*`, the FastEmbed and ONNX providers, a removed feature, or `FORGETFUL_SCOPES`, `FORGETFUL_SERVER` or `FORGETFUL_TOKEN`. Axmem reads none of them; the warning names each one and what replaces it (an old `FORGETFUL_SCOPES` without `AXMEM_SCOPES` stops the start instead, see above). Data held in a SQL database is not read or migrated
- `Qdrant <version> at <url> is too old: version 1.19.0 or later is required`: upgrade the node
- `Qdrant collection <name> does not match the settings`: the shard, replica or vector layout of the existing collection differs from `QDRANT_*` or `EMBEDDING_DIMENSIONS`; see above
- A `409` on an update means another writer changed the memory in the same moment, or that the replicas of a cluster kept holding different versions of it; read it again and repeat the change (a `409` that says the outcome cannot be told means the change may have been applied: read the memory before repeating it)

### Port Conflicts
- Change `SERVER_PORT` if the default conflicts
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
```
� **Warning**: Very verbose output, use only for troubleshooting

---

## Additional Resources

- [README.md](../README.md) - Getting started guide
- [Connectivity Guide](connectivity_guide.md) - Connecting MCP clients
- [Search Documentation](search.md) - Search architecture details
- [Settings Source](../app/config/settings.py) - Default values and validation
