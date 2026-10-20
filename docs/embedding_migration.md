# Embedding Provider Migration

When you switch embedding providers or models (e.g., from FastEmbed to OpenAI, or from `bge-small-en-v1.5` to `text-embedding-3-small`), existing memory embeddings become incompatible. Different providers produce vectors with different dimensions and semantic spaces, so a search query embedded with the new provider won't match memories embedded with the old one.

Forgetful includes a built-in CLI tool to handle this migration safely.

---

## When You Need to Re-embed

You need to run a re-embedding migration whenever you change any of these settings:

| Setting | Example Change |
|---------|---------------|
| `EMBEDDING_PROVIDER` | `FastEmbed` -> `OpenAI`, or any provider -> `gateway` |
| `EMBEDDING_MODEL` | `bge-small-en-v1.5` -> `text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | `384` -> `1536` |
| `GATEWAY_BACKEND`, `GATEWAY_MODEL` | another backend or model behind the same gateway |

If you only change non-embedding settings (database, auth, reranking, etc.), no re-embedding is needed.

---

## Quick Start

```bash
# 1. Update your .env with the new provider settings
EMBEDDING_PROVIDER=OpenAI
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
OPENAI_API_KEY=sk-...

# 2. Preview what will happen (no changes made)
forgetful --re-embed --dry-run

# 3. Run the migration
forgetful --re-embed
```

If running from source:

```bash
uv run main.py --re-embed --dry-run
uv run main.py --re-embed
```

---

## CLI Reference

### `--re-embed`

Runs the re-embedding migration instead of starting the server. Processes all memories across all
users, including obsolete memories, with the currently configured embedding provider and dimensions.
Counts and progress include obsolete memories. Their status and supersession metadata are preserved.
The vector storage is resized to the configured dimensions even when the database holds no
memories yet.

```bash
forgetful --re-embed [--batch-size N] [--dry-run]
```

`--batch-size` and `--dry-run` only apply together with `--re-embed`; used alone they are rejected with a usage error instead of starting the server.

### `--batch-size N`

Number of memories to process per batch. Must be a whole number of at least `1`; anything else is rejected before any backup or schema change. Default: `20`.

Lower values use less memory and are safer for rate-limited APIs. Higher values are faster for local providers like FastEmbed. Each batch is handed to the provider as one list of texts: FastEmbed embeds it natively and the `gateway` provider sends it as one request (split into requests of at most 256 texts when the batch is larger); the other remote providers still embed the texts of a batch one at a time.

```bash
forgetful --re-embed --batch-size 50    # faster for local providers
forgetful --re-embed --batch-size 5     # conservative for rate-limited APIs
```

### `--dry-run`

Shows configuration and memory count without making any changes. Use this to verify your setup before committing to a migration.
The preview is read-only: it does not run database schema migrations, create the SQLite database
file or touch the vector tables. A SQLite database that does not exist yet is reported as empty.

```bash
forgetful --re-embed --dry-run
```

Output:

```
[1/5] Validating configuration...
  Provider: OpenAI (text-embedding-3-small)
  Dimensions: 1536
  Database: SQLite (/home/user/.local/share/forgetful/forgetful.db)
  Batch size: 20
  Memories to process: 127

[DRY RUN] Would re-embed 127 memories with the above configuration.
  No changes were made.
```

---

## What Happens During Migration

The tool follows a 5-step process:

### Step 1: Validate Configuration

Displays the target provider, model, dimensions, database backend, and memory count. Confirms the settings are what you intend before proceeding.

### Step 2: Create Backup

Creates a timestamped backup of your database:

- **SQLite**: Takes a consistent snapshot of the database with SQLite's online backup, including changes that are still in the WAL, and writes it as one file, `forgetful.db.bak-YYYYMMDD-HHMMSS`
- **PostgreSQL**: Runs `pg_dump --clean --if-exists` to `forgetful-pg-YYYYMMDD-HHMMSS.sql`

The backup is kept alongside your database, in the directory of `SQLITE_PATH`, unless [`BACKUP_DIR`](./configuration.md#backup_dir) points elsewhere. Backups contain all of your data, so they are created readable by their owner only (`0600`, in a `0700` directory if Forgetful creates it). In-memory SQLite databases skip this step, and so do
databases without any memories: there is nothing to lose, so only the vector storage is
resized to the configured dimensions.

### Step 3: Update Vector Schema

Resizes the vector storage to match the new `EMBEDDING_DIMENSIONS`:

- **SQLite**: Drops and recreates the `vec_memories` virtual table
- **PostgreSQL**: Alters the `embedding` column type of `memories` to `vector(N)`.
  The `NOT NULL` constraint is lifted while the vectors are rebuilt and restored once validation passes.

### Step 4: Re-embed Memories

Processes all active and obsolete memories in batches:
1. Reads a batch of memories from the database
2. Generates new embeddings using the configured provider
3. Writes the new embeddings back
4. Reports progress via a live progress bar

```
[4/5] Re-embedding memories...
  [####################-----] 102/127 memories (80%)
```

### Step 5: Validate

Runs three automated checks:

1. **Count check** - Verifies every memory, including obsolete memories, has an embedding
2. **Dimension check** - Samples embeddings and confirms correct dimension count
3. **Search check** - Tests vector retrieval, including when all memories are obsolete

```
[5/5] Validating...
  Count check: 127 memories, 127 embeddings OK
  Dimension check: OK
  Search check: OK
```

---

## Recovering Obsolete-Memory Warnings

Older full rebuilds removed vectors for obsolete memories. This prevented `create_memory` from
warning when new content closely matched them. After upgrading, run `forgetful --re-embed` once,
even if your embedding settings have not changed. The rebuild regenerates missing vectors from the
stored memory content using the current model and dimensions. It also works when every memory is
obsolete.

Obsolete memories remain excluded from normal queries and automatic links. The targeted
`rebuild_embeddings` tool still processes active memories only; use the full CLI rebuild for
recovery.

Embedding refreshes (both the CLI rebuild and `rebuild_embeddings`) never change a memory's
`updated_at`. The targeted tool also does not overwrite a newer vector: each row is written only if
its `updated_at` is still the value seen when the batch was read, and the check is part of the write
(the `UPDATE` condition on PostgreSQL, a guard `UPDATE` that takes SQLite's write lock for the
batch). A memory edited while the rebuild is running is reported in `skipped_ids` and keeps the
embedding generated for its new content. An edit that arrives during the short write itself waits
for it. `failed` entries carry a short reason code (`embedding_failed`, `persistence_failed`,
`auto_link_failed`, `not_found_or_not_owned`); the underlying error is written to the server log.
An unknown project scope, or one that holds none of your memories, is rejected with a validation error.

---

## Failure Handling

The tool is designed to be safe. If anything goes wrong, your database is automatically restored.

### Automatic Restore

The backup is restored automatically in these scenarios:

- **Embedding API error** (rate limit, network failure, invalid key)
- **Validation failure** (count mismatch, dimension mismatch, broken search)
- **Interrupt signal** (Ctrl+C / SIGTERM)

```
[4/5] Re-embedding memories...
  [████░░░░░░░░░░░░░░░░░░░░░] 8/127 memories (6%)
  ERROR: OpenAI API rate limit exceeded

  Restoring from backup...
  Restored: /home/user/.local/share/forgetful/forgetful.db.bak-20260220-143022
  Database returned to pre-migration state.
```

A PostgreSQL restore runs in a single transaction and stops at the first error. If the restore itself fails, the error from `psql` is reported and the database is left exactly as it was, so a half-restored database is never reported as restored.

### Manual Restore

If you need to manually restore after a successful migration (e.g., you notice search quality issues):

**SQLite:**
```bash
# The backup path is printed at the end of the migration. Stop Forgetful first.
cp /path/to/forgetful.db.bak-YYYYMMDD-HHMMSS /path/to/forgetful.db
rm -f /path/to/forgetful.db-wal /path/to/forgetful.db-shm   # leftovers of the replaced database
```

**PostgreSQL:**
```bash
psql -h localhost -p 5099 -U forgetful -d forgetful -v ON_ERROR_STOP=1 --single-transaction \
  -f /path/to/forgetful-pg-YYYYMMDD-HHMMSS.sql
```

### Backup Cleanup

Backups are retained after a successful migration. Delete them manually once you're satisfied:

```bash
rm /path/to/forgetful.db.bak-*          # SQLite
rm /path/to/forgetful-pg-*.sql          # PostgreSQL
```

---

## Common Migration Scenarios

### FastEmbed (local) to OpenAI (cloud)

```bash
# .env
EMBEDDING_PROVIDER=OpenAI
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
OPENAI_API_KEY=sk-...

# Migrate
forgetful --re-embed --batch-size 20
```

### FastEmbed to Ollama (local)

```bash
# .env
EMBEDDING_PROVIDER=Ollama
OLLAMA_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSIONS=768

# Migrate
forgetful --re-embed --batch-size 50
```

### OpenAI to Azure OpenAI

```bash
# .env
EMBEDDING_PROVIDER=Azure
EMBEDDING_MODEL=text-embedding-ada-002
EMBEDDING_DIMENSIONS=1536
AZURE_ENDPOINT=https://my-resource.openai.azure.com/
AZURE_DEPLOYMENT=text-embedding-ada-002
AZURE_API_VERSION=2023-05-15
AZURE_API_KEY=...
# text-embedding-ada-002 does not accept the `dimensions` parameter
AZURE_SUPPORTS_DIMENSIONS=false

# Migrate (same dimensions, but different provider = different vector space)
forgetful --re-embed
```

### Any provider to an HTTP embeddings gateway

```bash
# Settings (environment or the config file)
EMBEDDING_PROVIDER=gateway
EMBEDDING_DIMENSIONS=1024          # the width the gateway's model returns
GATEWAY_URL=https://embeddings.example.net
GATEWAY_BACKEND=local
GATEWAY_MODEL=embed-small
GATEWAY_AGENT_ID=axmem
GATEWAY_TOKEN=...

# Migrate (a new provider is a new vector space, even at the same width)
forgetful --re-embed --batch-size 100
```

The gateway answers with vectors of its model's width. If that differs from `EMBEDDING_DIMENSIONS`, the run stops at the first batch with an error that names both widths, and the backup is restored. A budget or model refusal (HTTP 403) and a rejected request (422) stop the run at once; a rate limit or an unavailable backend is retried a few times first. See [the gateway settings](./configuration.md#gateway-embedding-provider-configuration).

### Upgrading Model Within Same Provider

Even when staying on the same provider, switching models requires re-embedding:

```bash
# .env - switching from small to large model
EMBEDDING_PROVIDER=OpenAI
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=3072
OPENAI_API_KEY=sk-...

# Migrate
forgetful --re-embed
```

### OpenAI-Compatible Local Server (llama.cpp, vLLM)

Use the `OpenAI` provider with a custom `OPENAI_BASE_URL` to point at a local inference server serving GGUF or other models:

```bash
# .env
EMBEDDING_PROVIDER=OpenAI
EMBEDDING_MODEL=your-model-name
EMBEDDING_DIMENSIONS=1024
OPENAI_BASE_URL=http://localhost:8080/v1
OPENAI_SUPPORTS_DIMENSIONS=false

# Migrate
forgetful --re-embed
```

**Server-side batch size**: Local inference servers (llama.cpp, vLLM) have an internal processing batch size that limits the maximum input token count per request. llama.cpp defaults to `512` tokens, which may be too small for longer memories. If you see errors like `input (N tokens) is too large to process`, increase the server's batch size:

```bash
# llama.cpp - increase batch size to handle longer texts
llama-server -m model.gguf --embedding --batch-size 2048

# vLLM
vllm serve model-name --max-model-len 2048
```

A batch size of `2048` is recommended as a starting point. This is a server-side setting, not a Forgetful setting — Forgetful's `--batch-size` flag controls how many memories are processed per database write, not the token limit.

---

## Backups

### PostgreSQL Backup Requirements

For PostgreSQL backups to work, the machine running the migration needs `pg_dump` and `psql` available on `PATH` (the PostgreSQL client tools, for example the `postgresql-client` package).

### Where Backups Are Kept

By default the backup is written in the directory of `SQLITE_PATH`, for SQLite and PostgreSQL alike. Set `BACKUP_DIR` to keep backups elsewhere, for example on separate storage; it is created as `0700` if missing and the backup files are `0600`.

---

## Tips

- **Always dry-run first** to verify your configuration and memory count
- **Use a conservative batch size** (10-20) for cloud providers to avoid rate limits
- **Use a larger batch size** (50-100) for local providers (FastEmbed, Ollama) for faster processing, and for the `gateway` provider, which sends a batch as a single request
- **Don't delete backups immediately** - verify search quality first by querying a few memories
- **Stop the server** before running re-embed to avoid concurrent access issues
- The migration processes all users' memories in a single run (it's a global operation)

---

## Further Reading

- [Configuration Guide](./configuration.md) - All embedding provider settings
- [Search Documentation](./search.md) - How embeddings are used in search
