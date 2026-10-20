# Embedding Provider Migration

When you switch embedding providers or models (e.g., from the default `TensorRT` provider to OpenAI, or from `bge-small-en-v1.5` to `text-embedding-3-small`), existing memory embeddings become incompatible. Different providers produce vectors with different dimensions and semantic spaces, so a search query embedded with the new provider won't match memories embedded with the old one.

The default provider, `TensorRT`, embeds on the NVIDIA GPU of the machine that runs Axmem with `BAAI/bge-small-en-v1.5` (384 dimensions). Axmem refuses to start when `EMBEDDING_DIMENSIONS` is not the width the model produces, and the message points here. Moving to another provider, or to a model of another width, is done with the steps below.

Axmem includes a built-in CLI tool to handle this migration safely.

---

## When You Need to Re-embed

You need to run a re-embedding migration whenever you change any of these settings:

| Setting | Example Change |
|---------|---------------|
| `EMBEDDING_PROVIDER` | `TensorRT` -> `OpenAI`, or any provider -> `gateway` |
| `EMBEDDING_MODEL` | `bge-small-en-v1.5` -> `text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | `384` -> `1536` |
| `EMBEDDING_TF32` | `false` -> `true` (TensorRT only; TF32 changes the vectors slightly) |
| `GATEWAY_BACKEND`, `GATEWAY_MODEL` | another backend or model behind the same gateway |

If you only change non-embedding settings (Qdrant, auth, reranking, etc.), no re-embedding is needed.

---

## Quick Start

```bash
# 1. Update your .env with the new provider settings
EMBEDDING_PROVIDER=OpenAI
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
OPENAI_API_KEY=sk-...

# 2. Preview what will happen (no changes made)
axmem --re-embed --dry-run

# 3. Run the migration
axmem --re-embed
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
tenants, including obsolete memories, with the currently configured embedding provider and dimensions.
Counts and progress include obsolete memories. Their status and supersession metadata are preserved.
The memories are copied, embedded again, into a new Qdrant collection of the configured width; the
collection that serves searches changes only when the copy is complete and valid. This works even when
the live collection still holds vectors of another width, and when it holds no memories yet.

```bash
axmem --re-embed [--batch-size N] [--dry-run]
```

`--batch-size` and `--dry-run` only apply together with `--re-embed`; used alone they are rejected with a usage error instead of starting the server.

### `--batch-size N`

Number of memories to process per batch. Must be a whole number of at least `1`; anything else is rejected before anything is created in Qdrant. Default: `20`.

Lower values use less memory and are safer for rate-limited APIs. Higher values are faster for local providers like TensorRT. Each batch is handed to the provider as one list of texts: TensorRT embeds it natively (in chunks of 64 texts, one GPU call per chunk) and the `gateway` provider sends it as one request (split into requests of at most 256 texts when the batch is larger); the other remote providers still embed the texts of a batch one at a time.

```bash
axmem --re-embed --batch-size 50    # faster for local providers
axmem --re-embed --batch-size 5     # conservative for rate-limited APIs
```

### `--dry-run`

Shows configuration and memory count without making any changes. Use this to verify your setup before committing to a migration.
The preview is read-only: it creates no collection and writes no point. A Qdrant instance that holds no
Axmem collections yet is reported as empty.

```bash
axmem --re-embed --dry-run
```

Output:

```
[1/4] Validating configuration...
  Provider: OpenAI (text-embedding-3-small)
  Dimensions: 1536
  Qdrant: http://localhost:6333 (collections axmem_*)
  Batch size: 20
  Memories to process: 127

[DRY RUN] Would re-embed 127 memories with the above configuration.
  No changes were made.
```

---

## What Happens During Migration

The tool follows a 4-step process. Nothing in the live collection is changed until the last moment, so the
server can keep serving searches from the old vectors while it runs (stop it anyway if you can, see the tips).

### Step 1: Validate Configuration

Displays the target provider, model, dimensions, Qdrant URLs with the collection prefix, and batch size. Confirms
the settings are what you intend before proceeding.

### Step 2: Open Qdrant

Connects to Qdrant, checks the collections (without insisting on the configured width, since the point is to replace
vectors of another width) and counts the memories to process.

```
[2/4] Opening Qdrant...
  Memories to process: 127
```

### Step 3: Re-embed Memories Into a New Collection

Creates a new collection of the configured `EMBEDDING_DIMENSIONS` and copies every active and obsolete memory into it
in batches:
1. Reads a batch of memories from the live collection
2. Generates new embeddings using the configured provider
3. Writes the memories with their new embeddings into the new collection
4. Reports progress via a live progress bar

```
[3/4] Re-embedding memories into a new collection...
  [####################-----] 102/127 memories (80%)
```

Every write into the new collection is conditional on the memory's version, so an edit that happens while the copy
runs is never overwritten with an older text. Edits made meanwhile are picked up by further scans of the old
collection and once more after the switch.

### Step 4: Validate and Switch

Runs three automated checks on the new collection:

1. **Count check** - Verifies every memory, including obsolete memories, has an embedding
2. **Dimension check** - Samples embeddings and confirms correct dimension count
3. **Search check** - Tests vector retrieval, including when all memories are obsolete

```
[4/4] Validating...
  Count check: 127 memories, 127 embeddings OK
  Dimension check: OK
  Search check: OK

  Successfully re-embedded 127 memories
  The live collection is now axmem_memories_20261020t101500ab12
  The previous collection axmem_memories_20261001t093000cd34 still holds the old vectors;
  delete it with the Qdrant API when satisfied with the results.
```

Only when all three pass does the live name move to the new collection. The previous collection is kept untouched.

---

## Recovering Obsolete-Memory Warnings

Older full rebuilds removed vectors for obsolete memories. This prevented `create_memory` from
warning when new content closely matched them. After upgrading, run `axmem --re-embed` once,
even if your embedding settings have not changed. The rebuild regenerates missing vectors from the
stored memory content using the current model and dimensions. It also works when every memory is
obsolete.

Obsolete memories remain excluded from normal queries and automatic links. The targeted
`rebuild_embeddings` tool still processes active memories only; use the full CLI rebuild for
recovery.

Embedding refreshes (both the CLI rebuild and `rebuild_embeddings`) never change a memory's
`updated_at`. The targeted tool also does not overwrite a newer vector: each memory is written only
if its version is still the one seen when the batch was read, and the check is part of the write
(a conditional write in Qdrant), so there is no window between check and write. A memory edited while the
rebuild is running is reported in `skipped_ids` and keeps the embedding generated for its new content.
`failed` entries carry a short reason code (`embedding_failed`, `persistence_failed`,
`auto_link_failed`, `not_found_or_not_owned`); the underlying error is written to the server log.
An unknown project scope, or one that holds none of your memories, is rejected with a validation error.

---

## Failure Handling

The tool is designed to be safe. The live collection is never modified by a run that does not finish, so there is
nothing to restore.

### Automatic Discard

The new collection is deleted, and the live one keeps serving exactly as before, in these scenarios:

- **Embedding API error** (rate limit, network failure, invalid key)
- **Validation failure** (count mismatch, dimension mismatch, broken search)
- **Interrupt signal** (Ctrl+C / SIGTERM)

```
[3/4] Re-embedding memories into a new collection...
  [████░░░░░░░░░░░░░░░░░░░░░] 8/127 memories (6%)
  ERROR: OpenAI API rate limit exceeded
  The new collection was discarded; the live collection was not changed.
```

Run the command again once the cause is fixed; it starts from the live collection each time. A run that was killed
without a chance to clean up (power loss, `kill -9`) leaves an unused collection behind; it never serves searches
and you can delete it with the Qdrant API.

### Going Back After a Successful Migration

If you notice search quality issues after a successful migration, the previous collection still holds the old
vectors. Point the live name back to it with the Qdrant API (alias `<prefix>_memories`, see
[Qdrant Configuration](./configuration.md#qdrant-configuration)), or restore a snapshot made with
`axmem backup` before the migration. Set the embedding settings back to what that collection was built with
before you start the server again, since startup refuses a collection whose width differs from
`EMBEDDING_DIMENSIONS`.

### Cleanup

The previous collection is retained after a successful migration. Delete it with the Qdrant API once you're satisfied:

```bash
curl -X DELETE -H "api-key: $QDRANT_API_KEY" "$QDRANT_URL/collections/<previous collection name>"
```

---

## Common Migration Scenarios

### FastEmbed (removed) to TensorRT (local GPU)

The `FastEmbed` provider (local CPU, ONNX Runtime) no longer exists. `EMBEDDING_PROVIDER=FastEmbed` stops startup with an error that names the setting, and the `FASTEMBED_CACHE_DIR`, `FASTEMBED_LOCAL_FILES_ONLY` and `EMBEDDING_ONNX_PROVIDERS` settings are ignored. `TensorRT` is the default provider, so memories that were embedded by `FastEmbed` are moved over by removing the old line and re-embedding. The model name and the width stay the same, but the vectors come from another runtime and are not identical, so `--re-embed` rewrites all of them:

```bash
# settings file (or remove the EMBEDDING_PROVIDER line: TensorRT is the default)
EMBEDDING_PROVIDER=TensorRT
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DIMENSIONS=384

# Migrate
axmem --re-embed --batch-size 50
```

The first start downloads the model files once into `EMBEDDING_MODEL_DIR` and builds the engine (a few seconds); see [GPU models](./gpu-models.md) and [offline hosts](./gpu-models.md#model-files-and-offline-hosts). If the GPU, the TensorRT libraries or the model files are missing, `--re-embed` stops before anything is created in Qdrant with `Error: the embedder cannot start: ...`.

### Wrong width at start-up

`EMBEDDING_DIMENSIONS` must equal the width the model produces. With the TensorRT provider the check runs at every start (and before `--re-embed` changes anything) and refuses with a `dimensions` cause that names both widths. Change `EMBEDDING_DIMENSIONS` to the width of the model, or choose a model of the width you configured; when the width of existing memories changes, `axmem --re-embed` copies them into a collection of the new width with fresh vectors.

### TensorRT (local GPU) to OpenAI (cloud)

```bash
# .env
EMBEDDING_PROVIDER=OpenAI
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
OPENAI_API_KEY=sk-...

# Migrate
axmem --re-embed --batch-size 20
```

### TensorRT to Ollama (local)

```bash
# .env
EMBEDDING_PROVIDER=Ollama
OLLAMA_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSIONS=768

# Migrate
axmem --re-embed --batch-size 50
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
axmem --re-embed
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
axmem --re-embed --batch-size 100
```

The gateway answers with vectors of its model's width. If that differs from `EMBEDDING_DIMENSIONS`, the run stops at the first batch with an error that names both widths, and the new collection is discarded. A budget or model refusal (HTTP 403) and a rejected request (422) stop the run at once; a rate limit or an unavailable backend is retried a few times first. See [the gateway settings](./configuration.md#gateway-embedding-provider-configuration).

### Upgrading Model Within Same Provider

Even when staying on the same provider, switching models requires re-embedding:

```bash
# .env - switching from small to large model
EMBEDDING_PROVIDER=OpenAI
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=3072
OPENAI_API_KEY=sk-...

# Migrate
axmem --re-embed
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
axmem --re-embed
```

**Server-side batch size**: Local inference servers (llama.cpp, vLLM) have an internal processing batch size that limits the maximum input token count per request. llama.cpp defaults to `512` tokens, which may be too small for longer memories. If you see errors like `input (N tokens) is too large to process`, increase the server's batch size:

```bash
# llama.cpp - increase batch size to handle longer texts
llama-server -m model.gguf --embedding --batch-size 2048

# vLLM
vllm serve model-name --max-model-len 2048
```

A batch size of `2048` is recommended as a starting point. This is a server-side setting, not an Axmem setting — Axmem's `--batch-size` flag controls how many memories are processed per Qdrant write, not the token limit.

---

## Snapshots Before a Migration

The migration needs no backup of its own. To keep a copy of the old vectors independent of the previous collection
(for example on another machine), take a snapshot first with `axmem backup` (see
[Qdrant Configuration](./configuration.md#qdrant-configuration)); the snapshot files are written to `BACKUP_DIR` and
are readable by their owner only.

---

## Tips

- **Always dry-run first** to verify your configuration and memory count
- **Use a conservative batch size** (10-20) for cloud providers to avoid rate limits
- **Use a larger batch size** (50-100) for local providers (TensorRT, Ollama) for faster processing, and for the `gateway` provider, which sends a batch as a single request
- **Don't delete the previous collection immediately** - verify search quality first by querying a few memories
- **Stop the server** before running re-embed, so the switch of the live collection happens while nothing is writing
- The migration processes all users' memories in a single run (it's a global operation)

---

## Further Reading

- [Configuration Guide](./configuration.md) - All embedding provider settings
- [Search Documentation](./search.md) - How embeddings are used in search
