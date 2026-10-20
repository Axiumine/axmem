# Changelog

All notable changes to Axmem are recorded here. Axmem is derived from
[Forgetful](https://github.com/ScottRBK/forgetful) by Scott Raisbeck (MIT), forked at upstream commit
`6d0199ed` (2026-10-18), plus the fork work on top of it. Upstream's later commits are not merged. The reason
behind each change is in [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md).

The format follows [Keep a Changelog](https://keepachangelog.com/). Axmem has no release yet, so everything is
under Unreleased. Entries marked **(planned)** are decided but have not landed; each is moved to its section
without the mark, with the commit that landed it, in the commit that lands it.

## [Unreleased]

### Added

- Quality gates run from one orchestrator, `scripts/quality`, used by the Git hooks and CI: ruff, import-linter,
  bandit, semgrep, vulture, radon, mypy, pylint, syrupy snapshots, 100 % branch coverage, memray budgets, trivy,
  Qodana, mutmut and GitNexus (proposed upstream as #163).
- `commit-msg` hook (`95c1e5f1`): `[🤖 ]<type>(<scope>): <subject>`, subject up to 150 characters, body up to 10
  lines, AI Co-Authored-By trailers and "Generated with" lines rejected, `SKIP=commit-msg` to bypass, with tests.
- `denylist-check` hook script: `pre-commit`, `commit-msg` and `pre-push` refuse staged changes, messages and
  unpushed commits that match a term in the clone-local, untracked `.git/info/denylist`; `SKIP` does not bypass it.
- This changelog and [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md), with a rule in `AGENTS.md`
  that every commit that changes behaviour updates both.
- Optional `gateway` embeddings provider (`EMBEDDING_PROVIDER=gateway`, `0e50a541`) that posts JSON to an HTTP embeddings gateway. Settings: `GATEWAY_URL`, `GATEWAY_PATH`, `GATEWAY_TOKEN` (a secret value), `GATEWAY_AUTH_HEADER`, `GATEWAY_TENANT_HEADER`, `GATEWAY_TENANT`, `GATEWAY_BACKEND`, `GATEWAY_MODEL`, `GATEWAY_AGENT_ID`, `GATEWAY_METADATA` (a JSON object of strings) and `GATEWAY_SUPPORTS_DIMENSIONS`; none is used or required unless the provider is selected (they are loaded with the other settings, and `GATEWAY_METADATA` is parsed by the adapter alone, so a blank or malformed value cannot stop a server that uses another provider). Texts go out in requests of at most 256, each with a fresh `request_id`, an `X-Idempotency-Key` that a retry of the same chunk reuses and an `X-Correlation-Id` shared by one call. Statuses 429 and 503 are retried after `Retry-After` (at most 30 s), 502, 504 and network errors with a growing wait, four attempts in all; 403, 422 and every other status fail at once, with the status and `code` in the error and never the body. A reply is refused unless it has one finite vector per text and each is `EMBEDDING_DIMENSIONS` wide. Tests run against an in-process fake gateway.
- `generate_embeddings(texts)` on the `EmbeddingsAdapter` contract and on every provider (Azure, Google, OpenAI and Ollama embed one text per request; the FastEmbed provider that embedded a batch natively was removed, see Removed). `ReEmbeddingService` embeds each batch of memories with one call; a failed batch in `rebuild_embeddings` is retried memory by memory, so one rejected memory is still reported as `embedding_failed` alone.
- Direct TensorRT 11 reranker (`RERANKING_PROVIDER=TensorRT`, the default; `d77aedcb`), strict fp32, run on the NVIDIA GPU with
  the CUDA Python bindings and no ONNX Runtime. It fetches `onnx/model.onnx` and `tokenizer.json` once into
  `RERANKING_MODEL_DIR` (pinned revision for the default model, SHA-256 manifest) and builds one engine per
  fingerprint (model SHA-256, TensorRT version, GPU name, compute capability, driver major version, TF32, optimization
  profile of batch 1 to 64 and sequence 2 to 512) in `RERANKING_ENGINE_CACHE_DIR`, behind a file lock, with sibling
  fingerprints pruned and logged. Startup warms the engine with one scored pair and logs the duration and whether the
  cache was hit or why it was rebuilt. New settings: `RERANKING_TF32` (default `false`; fp16 is not supported at all),
  `RERANKING_MODEL_DIR`, `RERANKING_ENGINE_CACHE_DIR`; `RERANKING_WORKERS` now sets the execution contexts, each
  about 1.8 GiB of GPU memory beyond the first. A server or CLI command that cannot run the engine refuses to start
  with `Error: the reranker cannot start: <cause>` and exit code 1 (no GPU, driver older than R580, TensorRT or CUDA
  libraries missing, model unavailable, build failure). New [docs/gpu-models.md](docs/gpu-models.md) (host
  requirements, TensorRT pin and bump policy, TF32, engine cache, GPU memory); dependencies `tensorrt-cu13`,
  `tensorrt-cu13-bindings`, `tensorrt-cu13-libs` (11.4.0.106, from the NVIDIA index), `cuda-python`, `tokenizers`,
  `huggingface-hub` and `numpy`.
- GPU preflight for the integration, SQLite e2e and PostgreSQL e2e suites (`tests/gpu_preflight.py`): the pytest run
  ends with exit code 2 and the cause when no GPU is visible, TensorRT cannot be imported, or an xdist worker count
  exceeds the GPU memory. New tests run the real engine: golden scores, chunking, concurrent workers, engine cache
  rebuild and pruning, damaged plans, and subprocess runs with `CUDA_VISIBLE_DEVICES=-1` and with the TensorRT
  libraries hidden.
- In-process TensorRT embeddings provider (`EMBEDDING_PROVIDER=TensorRT`, the new default; `5b047f2b`), strict fp32, on the
  same runner, engine cache, fingerprint, warm-up and startup refusal as the reranker. The model is
  `BAAI/bge-small-en-v1.5` (its `onnx/model.onnx` and `tokenizer.json` are byte-identical to the Xenova export; both
  repositories are pinned and the SHA-256 is recorded), with CLS pooling, L2 normalisation, truncation at 512 tokens,
  padding to the longest text of a batch and an engine profile of batch 1 to 64 and sequence 2 to 512; more than 64
  texts are embedded in chunks of 64, on one execution context and one worker thread. `generate_embeddings` is native.
  New settings: `EMBEDDING_TF32` (default `false`), `EMBEDDING_MODEL_DIR` and `EMBEDDING_ENGINE_CACHE_DIR`.
  `EMBEDDING_DIMENSIONS` (default 384) is checked at start: a model that produces another width refuses to start with
  the cause `dimensions` and releases the engine. A server, `--re-embed` or other command that cannot run the
  embedder exits with code 1 and `Error: the embedder cannot start: <cause>`. The embedder and the reranker share one
  CUDA context; the server logs the start-up line of each and the combined GPU memory of the process (about 4.1 GiB
  for the pair). Offline hosts copy `EMBEDDING_MODEL_DIR` and set `HF_HUB_OFFLINE=1` (`docs/gpu-models.md`).
  `docs/embedding_migration.md` gains the FastEmbed to TensorRT and wrong-width cases.
- Standalone end-to-end test (`tests/e2e/test_standalone_e2e.py`): the real server in a subprocess with the
  GPU models, a static token, no `GATEWAY_*` variable and `HF_HUB_OFFLINE=1`, behind a guard that refuses and records
  every connection that is not loopback (its Qdrant node is on loopback), runs create, auto-link, a reranked query,
  link and mark-obsolete.
- Tenants and static bearer tokens (`3201a2c3`). `AUTH_MODE=static` maps a fixed bearer token to one tenant: `AUTH_STATIC_TOKEN_HASHES` holds only the SHA-256 hash per tenant (never the token), a presented token is hashed and compared with every hash in constant time, one token belongs to one tenant, and a token never reaches a log line. For `jwt` and `introspection` the tenant is the claim `tenant_id`, else `tenant`, else `sub`; a blank claim falls through, and a claim of another type or over 255 characters is refused. `DEFAULT_TENANT` names the tenant of `AUTH_MODE=none`. Tests cover each mode (valid, invalid, expired and missing token), the claim order, the hash comparison and tenant isolation over REST, MCP and the CLI.
- `project` argument (a free-form string, trimmed, 1 to 500 characters, created on first use) on `create_memory`, `update_memory` (an empty string clears it), `query_memory`, `get_recent_memories` and `rebuild_embeddings`, the `project` field and query parameter of the REST memory routes, and `-p/--project` on the CLI memory verbs.
- Qdrant as the only store, authenticated with a Qdrant API key. A memory is a point (vector and payload) in
  `<prefix>_memories`, an alias onto the live generation `<prefix>_memories_<stamp>`; a link is a point in
  `<prefix>_links` whose id is a deterministic UUIDv5 of tenant and pair. New settings: `QDRANT_URL` (one node, or the
  nodes of one cluster separated by commas; default `http://127.0.0.1:6333`), `QDRANT_API_KEY` (a secret value),
  `QDRANT_COLLECTION_PREFIX` (default `axmem`), `QDRANT_SHARD_NUMBER`, `QDRANT_REPLICATION_FACTOR`,
  `QDRANT_WRITE_CONSISTENCY_FACTOR`, `QDRANT_TIMEOUT_SECONDS` and `QDRANT_WRITE_ATTEMPTS`; the cluster profile
  (3, 3, 2) is documented in [docs/configuration.md](docs/configuration.md#qdrant-configuration). At start every node
  must report Qdrant 1.19.0 or later, the collections are created, and an existing collection whose width, shard
  count, replication or payload indexes differ from the settings stops the server with a message that names the
  difference. Requests spread over the nodes and move on when one cannot be reached; a write that fails ambiguously
  is repeated with the same claim token, so it takes effect once. Every read and write goes through `QdrantStore`,
  which adds the tenant condition (proved for every repository method by `tests/integration/test_tenant_chokepoint.py`);
  import-linter lets only `client.py`, `schema.py` and `store.py` import `qdrant_client`, and two semgrep rules
  (`forgetful-qdrant-write-outside-store`, `forgetful-qdrant-write-without-wait`) replace the SQL rule. Edits and
  obsolete toggles are writes conditional on the version the writer read, with the claim token read back to learn
  whether the write won; a lost race is repeated against the new state up to 5 times and then fails with
  `ConflictError` (REST 409, MCP tool error).
- Backups as Qdrant snapshots plus a JSONL export of the memories without vectors, from the CLI and nowhere else:
  `forgetful backup [--force]` (a snapshot of the live memory collection and the links from every node into
  `BACKUP_DIR`, directory `0700` and files `0600`, with a manifest of SHA-256 sums; it refuses when the replicas of a
  replicated deployment disagree unless `--force`), `forgetful restore <dir> --yes` (stop the server first; the
  restored memory collection becomes the live one), `forgetful export <file>` and `forgetful import <file>` (the
  import embeds every memory again with the configured provider, leaves an existing id untouched and can be repeated;
  a line it cannot read is skipped and counted) and `forgetful qdrant-check` (reads each node on its own and reports
  the points whose version, claim token or obsolete flag differ; exit code 1 on a difference). The check reports
  only; repairing a replica is an operator decision.
- Three-node cluster end-to-end suite (`tests/cluster`, marker `cluster`, excluded from a plain `pytest` run) and the
  `e2e-qdrant` gate that runs it and appends its coverage: replication factor 3, write consistency factor 2, strong
  ordering, an API key and internal authentication. It checks the layout, the replication of writes, writes and reads
  while a node is down, races of writers spread over the nodes, and that the race detector reports a writer that
  checks on the client.

### Changed

- Project renamed to Axmem under the Axiumine organisation; README rewritten, Forgetful credited, MIT notice
  kept (`b121d350`). The package, CLI and environment variables were renamed afterwards, see the rename entry below.
- Python 3.14 is the only supported version: `requires-python`, classifiers, tool targets, CI, Dockerfiles and Nix
  (`1eea81a5`); the `compat` gate is gone; 142 ruff findings from the new target were auto-fixed (`c78d3f98`).
- The environment reference moved from `docker/.env.example` to `.env.example` in the repository root, where the
  `QODANA_TOKEN` key of the quality gates joins it (commented out, because an empty value counts as set). It now
  shows the native defaults: SQLite, PostgreSQL on `127.0.0.1`, no `SQLITE_PATH` and no compose settings. Settings no
  longer read `docker/.env`; the files read are `.env` in the working directory and `~/.config/forgetful/.env`.
  The `SERVER_HOST` default stays `0.0.0.0` (its comment no longer calls it the container default).
- The PostgreSQL e2e fixture creates the `forgetful-db` test container with `docker run` (image
  `pgvector/pgvector:pg16`, volume `forgetful_db_data`) from the `POSTGRES_*` and `PGPORT` settings instead of the
  removed compose file; an existing container is reused or restarted.
- Coverage data is written to `.coverage-data/` (`[tool.coverage.run] data_file`) instead of the repository root,
  because `parallel = true` makes every measured process write its own `.coverage.*` fragment, and interrupted
  runs left them behind. `.coverage-data/` is ignored by Git, semgrep, trivy, Qodana and radon.
- NVIDIA GPU is a hard requirement for development and runtime; there is no CPU path and no skip. The reranker,
  the default embedder, the test suites and, with the removal of FastEmbed, every local model are on the GPU.
  The remote embeddings providers, `RERANKING_PROVIDER=HTTP` and `RERANKING_ENABLED=false` are the explicit ways
  to run without one.
- Embeddings are computed in the server process on the GPU by default (`EMBEDDING_PROVIDER=TensorRT`) instead of
  coming from a remote embeddings service. The `gateway` provider stays optional and no `GATEWAY_*` setting is used
  unless it is selected. `EMBEDDING_PROVIDER` defaults to `TensorRT`
  (it was `FastEmbed`) and an empty value selects it. A database filled by the old default needs `--re-embed`,
  because TensorRT's vectors come from another runtime and are not identical (`docs/embedding_migration.md`); the
  command is also needed whenever the provider, model or width changes. `RerankerError` and `RerankerStartupError` are now `TensorRTError` and
  `TensorRTStartupError` (they cover both models), `reranker_model.py` is `tensorrt_model.py` and
  `docs/gpu-reranking.md` is `docs/gpu-models.md`.
- Users become tenants and projects become an optional scope string created on first use. The stored `users` row is the tenant record (matched by name in `external_id`) and `projects` is the table behind the scope, because the storage layer is replaced later and not migrated; no revision touches either table. A scope belongs to one tenant, so two tenants that use the same label have two scopes. Memory responses carry `project` (a string or null) instead of `project_ids`, and `ObsoleteMatch` does the same. The `AUTH_MODE` setting replaces `FASTMCP_SERVER_AUTH`, `DEFAULT_TENANT` replaces `DEFAULT_USER_ID`, `DEFAULT_USER_NAME` and `DEFAULT_USER_EMAIL`; an existing deployment keeps its memories by setting `DEFAULT_TENANT` to the old `DEFAULT_USER_ID` value. `GET /api/v1/auth/info` reports `authMode` as `disabled`, `jwt`, `introspection` or `static`. The tool scopes `users` and `projects` no longer exist (`read:memories` and `write:memories` remain). `.env.example` now sets `AUTH_MODE=none` and `SERVER_HOST=127.0.0.1`. The stdio and the HTTP examples in the README and the connectivity guide pass `AUTH_MODE=none` or a bearer token. The tool count goes from 17 to 10. The test harness server runs with `AUTH_MODE=none` on its loopback bind.
- Memory ids are UUIDs (UUIDv7, so they sort in creation order) in the REST routes, the MCP tools and the CLI, because
  Qdrant has no autoincrement. A number is refused (`memory_id must be a valid UUID`, or `ids are UUIDs` on the CLI) and a
  path id that is not a UUID is 404; the upper-case spelling, the form without hyphens and surrounding whitespace are
  accepted. `linked_memory_ids`, `superseded_by`, `related_ids` and `memory_ids` carry UUID strings.
  The CLI argument tree and settings defaults snapshots are refreshed.
- Tenants are stateless: a tenant's id is the UUIDv5 of its name under a fixed namespace, so there is no tenant store,
  no `TenantService` and no `users` table. Changing the namespace (`TENANT_NAMESPACE` in `app/models/tenant_models.py`)
  would re-key every tenant.
- Re-embedding (`forgetful --re-embed`, or `forgetful re-embed`) copies the memories, embedded again, into a new
  collection and moves the live alias only after the count, dimension and search checks pass. A failure or an
  interrupt discards the new collection and leaves the live one untouched, so there is no backup to take or restore
  first; the previous collection is kept after a success and is deleted by hand. `--dry-run` is read-only and creates
  nothing. Edits made while the copy runs are picked up by further scans, and writes into the new collection are
  conditional on the version. The live collection may hold vectors of the old width while this runs; the server
  refuses to start on a width mismatch. [docs/embedding_migration.md](docs/embedding_migration.md) is rewritten.
- The integration and end-to-end suites run on a real Qdrant node that `tests/qdrant_support.py` starts through Docker
  (`qdrant/qdrant:v1.19.2`) with an API key, once per pytest process, or on a server named by `AXMEM_TEST_QDRANT_URL`.
  The in-memory repository stubs are deleted (they would repeat Qdrant's filter, ordering and conditional-write
  semantics), `tests/e2e_sqlite` is merged into `tests/e2e`, the redundant `test_auth_cache_e2e.py` is deleted, and
  tests stay apart by tenant namespace (collections are shared) or use their own collections where counts or
  generations matter. New tests prove the tenant chokepoint, the conditional writes (barrier races, lost
  acknowledgements, a detector that must report a check-then-write updater), the pool, the schema checks, the store
  guards, the archive format, the cluster check and the operator commands. The snapshot gate pins the collection layout
  and the payload types instead of the SQL DDL and the Alembic chain. The standalone end-to-end test runs on Qdrant.
  The GPU preflight and the Docker preflight apply to every suite and never skip.
- `BACKUP_DIR` now says where `forgetful backup` and `forgetful export` write; the database backup that `--re-embed`
  made is gone with the database. `.env.example` shows the Qdrant settings instead of SQLite and PostgreSQL.
- The integration and end-to-end suites run the real TensorRT embedder and reranker on the GPU through the session
  fixtures `tensorrt_embedder` and `tensorrt_reranker` (the production adapters; there is no test-only embedder), with
  no stub embedder or reranker, fake module, `gpu` marker or skip. The embedding stubs (`MockEmbeddingsAdapter`,
  `BatchViaSingle` and the per-file hashing, constant and fixed-vector adapters) are gone: tests that depend on
  similarity (the 0.7 auto-link cutoff, the 0.89 obsolete warning, ordering of results, duplicates) use clearly
  similar and clearly unrelated texts, whose measured cosines `tests/integration/test_embedding_texts.py` pins and
  `docs/contributors.md` lists. HTTP mock transports and in-process fake endpoints for the remote providers, spies
  and wrappers that delegate to the real adapters (fault injection on cue, added latency, a vector cut to another
  width) and fault injection in the engine (a barrier around `TensorRTEngine.infer`, hidden libraries in a
  subprocess, one invalid `cudaMemcpy`) remain. Every CI job that runs tests or coverage (`ci.yml` `test`, `e2e.yml`, and the `snapshots`, `coverage`,
  `memory` and `mutation` jobs of `quality.yml`) moves to `runs-on: [self-hosted, linux, gpu]`; the static gates,
  the architecture tests, `trivy`, `qodana` and the wheel smoke test stay on hosted runners, and the static job
  installs without the TensorRT libraries. The runner is not provisioned by the repository. The Windows leg of
  `ci.yml` is gone.
- `RERANKING_PROVIDER` accepts `TensorRT` (default) and `HTTP`; `FastEmbed` is now rejected at startup with an error
  naming the setting. The reranker needs an NVIDIA GPU; `RERANKING_ENABLED=false` or `RERANKING_PROVIDER=HTTP` runs
  without one. The reranker is Linux-only (the engine cache locks with `fcntl`) and the package classifier reads
  `Operating System :: POSIX :: Linux`. `docs/OFFLINE_SETUP.md`, `docs/configuration.md`, `docs/search.md`,
  `docs/contributors.md`, `docs/quality-gates.md`, `.env.example`, `README.md` and `architecture.md` describe the new
  reranker; the reranker model no longer lives in the FastEmbed cache and `FASTEMBED_LOCAL_FILES_ONLY` does not
  affect it.
- Renamed from `forgetful` to `axmem` everywhere except attribution and history, so Axmem no longer shares a name with
  the project it is derived from:
  - Package and console script: the distribution is `axmem` and `axmem` is the only console script (`forgetful` and
    `forgetful-ai` are gone); the CLI program name, help texts and messages say `axmem`; the project URLs point at
    `https://github.com/Axiumine/axmem`; `uv.lock` is regenerated for the new name.
  - Environment variables: `FORGETFUL_SERVER`, `FORGETFUL_TOKEN` and `FORGETFUL_SCOPES` become `AXMEM_SERVER`,
    `AXMEM_TOKEN` and `AXMEM_SCOPES`, and the Nix build variable `FORGETFUL_VERSION` becomes `AXMEM_VERSION`. The old
    names are not read, so a deployment that sets them runs with the defaults (local mode, no token, scope `*`)
    until it is changed. An instance that sets `FORGETFUL_SCOPES` without `AXMEM_SCOPES` refuses to start (see the
    review fixes), so set `AXMEM_SCOPES` before the upgrade.
  - Directories: the user config directory is `~/.config/axmem` and the data directory `~/.local/share/axmem`
    (models and engine caches under `models/`); nothing is read from or moved out of the `forgetful` directories, so
    an existing config file has to be moved and the models are downloaded and built again. The git-ignored local
    state directory is `.axmem/`.
  - MCP: the server name (`SERVICE_NAME`, default `Axmem`; `SERVICE_DESCRIPTION` likewise) and the three
    meta-tools `discover_axmem_tools`, `how_to_use_axmem_tool` and `execute_axmem_tool` (were `discover_forgetful_tools`,
    `how_to_use_forgetful_tool` and `execute_forgetful_tool`); clients that registered the server as `forgetful`
    must register it as `axmem` (the tool prefix a client shows becomes `mcp__axmem__`). The example project and
    repository names in the tool catalogue are `axmem` and `axiumine/axmem`.
  - Code and tooling: `ForgetfulMCP` is `AxmemMCP`, `ThrowawayForgetful` is `ThrowawayAxmem`; the agent skill
    directories are `skills/axmem-*` and the harness image, container prefix and label are `axmem-test-harness`,
    `axmem-uat-` and `axmem.harness.requirements-hash`; the semgrep rules are in `.semgrep/axmem.yml` with `axmem-`
    ids; the trivy cache is `~/.cache/axmem-trivy`; the Nix package, app and wrapper are `axmem` (the `forgetful`
    symlink is dropped); the agent files for Copilot CLI, Gemini CLI and OpenCode are named `axmem-*`.
  - Tests and snapshots: names follow the code; four snapshots were refreshed and the diff holds only the renamed
    names (`SERVICE_NAME`, `SERVICE_DESCRIPTION`, the `AXMEM_*` settings, the CLI program name, the meta-tool names and
    the example values).
  - Docs: README, `architecture.md`, `docs/`, `skills/` and the environment reference use the new names. The word
    Forgetful stays only in the README attribution, `LICENCE.md`, this history, `docs/changes-from-forgetful.md` (and
    links to it). The architecture image of the upstream project is replaced by a Mermaid diagram in the README
    (the two `Forgetful Architecture` images are deleted). `AGENTS.md`
    describes the Qdrant test suites instead of the SQLite and PostgreSQL ones, and its GitNexus section no
    longer names the index.
- Axmem is documented as a standalone memory server. The README opens with what it needs (Qdrant, an NVIDIA GPU with
  the model files, an `AUTH_MODE`), says that remote embeddings, a remote rerank service and token providers are
  optional integrations nothing contacts unless selected, and says what it does not do (no plans, tasks, skills,
  files, documents, entities, activity stream or user and project registries). `docs/changes-from-forgetful.md`
  states the reason for each cut without naming a system outside the repository, and its Tests row is Done: the
  contract tests of the `gateway` adapter run against an in-process fake gateway. The `axmem-remember` skill no longer
  sends work items to a plan or task store. Documentation only; no setting, route, tool or schema changed.

### Removed

- Graph REST view (`GET /api/v1/graph`, `/graph/subgraph`, `/graph/memory/{id}`), `GraphService`, the graph models,
  the `MAX_GRAPH_LIMIT` setting and the subgraph SQL on both backends (`6ce740e7`). Memory links, auto-linking,
  the link tools and the `query_memory` link expansion stay. No schema change.
- Plans and tasks: services, repositories, models, REST routes, 15 MCP tools and the `PLANNING_ENABLED` flag
  (`59f3f479`). Revision `20261019_drop_planning` drops their tables and activity rows; the downgrade recreates
  the empty tables.
- Skills: service, repositories, models, REST routes, 17 MCP tools, `SKILLS_ENABLED` and the `SKILL_*` settings,
  skill import/export, `skill_ids` on memories and the skill handling in re-embedding (`e77b50e0`). Revision
  `20261019_drop_skills` drops the tables and activity rows; the downgrade recreates the empty tables. The
  `forgetful-procedures` agent skill file is deleted and the other files in the `skills/` catalogue are edited;
  the catalogue stays.
- Files (`c2cbf05a`): service, repositories, models, REST routes, 5 MCP tools, `FILES_ENABLED` and the `FILE_*` settings,
  `file_ids` on memories and the `file` activity entity type, tool category and scope. Revision
  `20261019_drop_files` drops `files`, `memory_file_association` and `entity_file_association` (the stored binary
  content included) and the file activity rows; the downgrade recreates the empty tables. The `forgetful-files`
  agent skill file is deleted and the other files in the `skills/` catalogue are edited; the catalogue stays. No
  optional feature flag is left, so the SQLite e2e feature-flag registry and its test module are gone.
- Discord badge and banner image from the README, and the PyPI install option, because `forgetful-ai` is
  upstream's package (`b121d350`).
- Documents and code artifacts (`611e76d5`): services, repositories, models, REST routes (`/api/v1/documents`,
  `/api/v1/code-artifacts`), 10 MCP tools, the `DOCUMENT_*` and `CODE_ARTIFACT_*` settings, `document_ids` and
  `code_artifact_ids` on memories and the `document` and `code_artifact` activity entity types, tool categories and
  scopes. Revision `20261019_drop_docs_artifacts` drops `documents`, `code_artifacts`, `memory_document_association`
  and `memory_code_artifact_association` (the stored content included) and the document and code artifact activity
  rows; the downgrade recreates the empty tables. The memory tool docs no longer point long text at documents.
  Agent skill files and prompts in `docs/` that taught these tools are edited; the `skills/` catalogue stays.
- Entities and relationships (`5f7f1bd1`): service, repositories, models, REST routes (`/api/v1/entities`), 16 MCP tools, the
  `entity` tool category and scope, the `ENTITY_*` settings, the `entity`, `entity_memory_link`,
  `entity_relationship` and `entity_project_link` activity entity types, and the PostgreSQL column-width clamp that
  only guarded entity columns. The `linking` tool filter now selects the memory link tools only. Revision
  `20261019_drop_entities` drops `entities`, `entity_relationships`, `entity_project_association` and
  `memory_entity_association` and deletes the entity activity rows; the downgrade recreates the empty tables. The
  `forgetful-entities` agent skill file is deleted and the other files in the `skills/` catalogue, the prompts and
  the agent commands in `docs/` are edited; the catalogue stays. The tool count goes from 33 to 17.
- Activity (`373e7eb5`): `EventBus` with its SSE stream, `ActivityService`, both activity repositories and the `activity_log`
  table, the REST routes `GET /api/v1/activity`, `/api/v1/activity/{entity_type}/{entity_id}` and
  `/api/v1/activity/stream`, the `ACTIVITY_ENABLED`, `ACTIVITY_RETENTION_DAYS`, `ACTIVITY_TRACK_READS` and
  `SSE_MAX_QUEUE_SIZE` settings, the event emission in the memory and project services, and the one-shot CLI wait for
  pending event handlers before the database closes. The memory access handler is gone with the bus, so
  `access_count` and `last_accessed_at` have no writer or reader; they are removed from the memory objects that the
  REST routes and tools return. The unused `parse_datetime_param` query helper is deleted. Revision
  `20261020_drop_activity` drops `activity_log`, `memories.access_count`, `memories.last_accessed_at` and the
  `ix_memories_last_accessed_at` index; the downgrade recreates the empty table and the two columns (every memory
  reads as never accessed). Older environment files that still set the removed settings keep working, because
  unknown settings are ignored. The removal tests of the earlier cuts now check their own revision instead of the
  head, because the log no longer exists there.
- Container deployment (`36a25494`): `docker/` (the Dockerfile and the SQLite, PostgreSQL and development compose files),
  `.dockerignore`, the `build.yml` workflow that built, tested and pushed the image to a container registry, the
  `docker-offline` job of `ci.yml` with its `tests/docker/` image test, the `docker` output of `flake.nix` (the
  dev shell, the app package and the app stay), `docs/self-hosting-guide.md` and the Docker parts of the README,
  `docs/configuration.md` (including `COMPOSE_PROJECT_NAME` and `BIND_ADDRESS`, which only the compose files read),
  `docs/OFFLINE_SETUP.md`, `docs/embedding_migration.md`, `docs/nix.md` and `docs/contributors.md`. The compose
  files still pulled the upstream image and no Axmem image was ever published. Docker stays as a test dependency
  (the PostgreSQL e2e fixture, the quality gates that fall back to a tool image, the test harness image). No schema
  change.
- SQLite, PostgreSQL, pgvector, SQLAlchemy and Alembic: `alembic/` and `alembic.ini` (every revision, no migration
  is needed because Qdrant collections are created at start), the SQLite and PostgreSQL memory repositories and
  their sessions and adapters, `vector_ddl`, the database `backup_adapter` and its `pg_dump`/`psql` handling, the user
  and tenant services, repositories and models (the tenant record lives nowhere now), the settings `DATABASE`,
  `POSTGRES_HOST`, `PGPORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `SQLITE_PATH`, `SQLITE_MEMORY` and
  `DB_LOGGING` (older environment files that still set them keep working, because unknown settings are ignored), and
  the dependencies `aiosqlite`, `alembic`, `asyncpg`, `pgvector`, `psycopg2-binary`, `sqlalchemy` and `sqlite-vec`
  (`qdrant-client` is added). The `e2e-postgres` gate, the `forgetful-db` PostgreSQL test fixture and the SQLite
  e2e suite are replaced by the Qdrant fixture and the cluster gate. Data in an existing SQLite or PostgreSQL
  database is not migrated, and no earlier version writes the export format that `forgetful import` reads: re-create the memories.
- User and project tools, routes and CLI verbs: the MCP tools `get_current_user`, `update_user_notes`, `create_project`, `list_projects`, `get_project`, `update_project` and `delete_project`, the `user` and `project` tool categories, the REST routes `/api/v1/projects`, `ProjectService`, `UserService`, the project and patch models, the user notes and `idp_metadata` handling, the `PROJECT_DESCRIPTION_MAX_LENGTH` and `PROJECT_NOTES_MAX_LENGTH` settings, `project_ids` on the memory tools and routes, the `forgetful project list` command and the CLI project name resolver. The GitHub and Google OAuth providers, the `FASTMCP_SERVER_AUTH`, `FASTMCP_SERVER_AUTH_GITHUB_*`, `FASTMCP_SERVER_AUTH_GOOGLE_*`, `OAUTH_STORAGE_PATH` and `DEFAULT_USER_*` settings, the CLI browser login with its token cache, `forgetful auth logout`, and the `py-key-value-aio` dependency that stored the OAuth state. Agent commands, prompts, the connectivity guide and the files in the `skills/` catalogue that taught these tools are edited; the catalogue stays. No schema change and no migration: the `users` and `projects` tables stay.
- FastEmbed cross-encoder reranker: `FastEmbedCrossEncoderAdapter`, its DirectML handling, the `RERANKING_THREADS` and
  `RERANKING_ONNX_PROVIDERS` settings and the tests of that adapter and of its worker pool. Older environment files
  that still set the removed settings keep working, because unknown settings are ignored.
- FastEmbed, ONNX Runtime and the local CPU embedder: `FastEmbeddingAdapter`, `fastembed_offline.py`, the first-run
  model check, `parse_onnx_providers` and the `FASTEMBED_CACHE_DIR`, `FASTEMBED_LOCAL_FILES_ONLY` and
  `EMBEDDING_ONNX_PROVIDERS` settings, with the `fastembed` dependency and the packages that only it needed
  (`onnxruntime`, `flatbuffers`, `loguru`, `mmh3`, `pillow`, `protobuf`, `py-rust-stemmers`, `win32-setctime`).
  `EMBEDDING_PROVIDER` accepts `TensorRT` (default), `Azure`, `Google`, `OpenAI`, `Ollama` and `gateway`;
  `FastEmbed` is rejected at startup with an error naming the setting. Older environment files that still set the
  removed settings keep working, because unknown settings are ignored. A database filled by FastEmbed is moved with
  `forgetful --re-embed` ([docs/embedding_migration.md](docs/embedding_migration.md)). `docs/OFFLINE_SETUP.md` is
  deleted and its offline guidance is the "Model files and offline hosts" section of
  [docs/gpu-models.md](docs/gpu-models.md); the FastEmbed cache steps of the three workflows are gone. No schema
  change.
- `org_knowledge_proposal.md` (a first-person product note by the upstream author, about a different project),
  `_typos.toml` (it only excluded that note and allowed a string of a test that no longer exists) and the upstream
  Claude Code plugin section of `docs/connectivity_guide.md` (the plugin belongs to the upstream project and is
  installed under its name).

### Fixed

Fork fixes proposed upstream as pull requests #102 to #154 (none merged there when checked on 2026-10-19).
Fixes to features that are removed above are moot once those features go.

- **Security:** token scope fails closed (#107), ownership checks on link endpoints, referenced projects and
  embeddings (#103, #113), token cache honours expiry (#110), no SQL echo or parameters in logs (#143), server
  URLs redacted (#112), owner-only backups and storage (#137), user upsert keeps notes and metadata (#108),
  pyjwt, urllib3 and fsspec locked to releases with security fixes (#102).
- **REST and MCP input handling:** 400/404 instead of 500 for malformed ids, bodies, claims and query parameters
  (#153, #132, #122, #121, #109); memory tool argument validation (#148); sanitised meta-tool errors and logs
  (#149).
- **Memory and search correctness:** query token budget (#106), update consistency and embeddings (#126, #139),
  input validation and database-compatible limits (#124, #128, #150), link listing (#123), rerank honours `k`
  (#152), idempotent links (#147), tag filters (#136), read events (#151).
- **Startup, CLI and tooling:** configuration wiring and lifecycle (#154), migration runner hardening (#127),
  embedding adapter robustness (#114), CLI fixes (#111, #112, #140 to #142), test harness fixes (#119, #120,
  #144 to #146).

Fixes found by reviewing the feature cuts (no upstream counterpart: the code they touch exists only here):

- **Start-up:** a Qdrant node that does not answer is logged and skipped while another one does, so a cluster that
  lost a node still starts and can be inspected (the alias and collection reads try each node in turn; every node that
  answers must still be 1.19.0 or later; with no answering node the start is refused as before). Settings of earlier
  versions that are ignored now are named in one warning with what replaces each, read from the environment and from
  dotenv files: the SQL store (`DATABASE`, `DB_LOGGING`, `PGPORT`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PASSWORD`,
  `POSTGRES_USER`, `SQLITE_MEMORY`, `SQLITE_PATH`), the OAuth providers and `DEFAULT_USER_*`, the FastEmbed and ONNX
  Runtime settings (`FASTEMBED_*`, `EMBEDDING_ONNX_PROVIDERS`, `RERANKING_ONNX_PROVIDERS`, `RERANKING_THREADS`), the
  caps and switches of the removed features (activity, files, planning, skills, documents, entities, code artifacts,
  `MAX_GRAPH_LIMIT`) and `FORGETFUL_SCOPES`, `FORGETFUL_SERVER` and `FORGETFUL_TOKEN`. The list holds exact names
  matched without regard to case, as the settings are read: a name that merely shares a prefix (`POSTGRES_PORT`,
  `SQLITE_TMPDIR`, `DEFAULT_USER_AGENT`) belongs to other software and is no longer reported, and a bare dotenv key
  that sets nothing is not counted. An old `FORGETFUL_SCOPES` that nothing replaces now refuses the start of `serve`
  and of the tool commands that run in process (both build the runtime; `backup`, `restore`, `export`, `import`,
  `qdrant-check`, `re-embed` and the tool commands that call a remote server never use the scope ceiling and do not
  check it) and names both settings, instead of warning while the instance allowed every
  tool: set `AXMEM_SCOPES` to the scopes you want, or to `*` on purpose. Log records go to stderr during the TensorRT
  warm-up, so the engine build and the cause of a refusal are visible before logging is configured.
- **Authentication:** `AUTH_MODE=jwt` requires `FASTMCP_SERVER_AUTH_JWT_ISSUER` and `FASTMCP_SERVER_AUTH_JWT_AUDIENCE`
  and refuses to start without them (before, both were passed on as unset and a token that the same identity provider
  issued for any other application was accepted), and a signed token without an `exp` claim is refused (it never
  expired). `AUTH_MODE=introspection` checks the `aud` of the endpoint's answer against the new
  `FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE` when it is set (a string or a list that names it); without it the
  audience is not checked, and the issuer never is, because the endpoint is the authority. `nbf` is not checked in
  either mode. In introspection mode the REST token cache is no longer stacked on the verifier's own cache, which
  made a revoked token valid for up to twice `TOKEN_CACHE_TTL_SECONDS`; the verifier's cache is the only one, for MCP
  and REST alike. The configuration reference says exactly what each mode checks. A mode whose settings are incomplete (`jwt`
  without an issuer and an audience, `static` without token hashes, `introspection` without its endpoint) refuses
  only the server: `axmem serve` prints `Error: <cause>` and exits with code 1, and `backup`, `export`, `re-embed` and
  the tool commands, which never serve a request, run as before. The provider is built when `main` is imported, and a
  mode that lacked settings used to end every command in a traceback from that import.
- **REST routes honour the scopes:** the memory REST routes ignored `AXMEM_SCOPES` and the scopes of the token, which
  only the MCP meta-tools enforced, so a read-only instance or token could still create, change, link and obsolete
  memories over REST. Each route now stands for a registry tool (`GET /api/v1/memories` is `get_recent_memories`,
  `POST /api/v1/memories` is `create_memory`, and so on, listed in the API reference) and answers `403` with the same
  message as the MCP call when the instance ceiling or the token's scopes do not permit that tool; the credential is
  still checked first (`401`). A token whose scopes are only a verifier requirement such as `api:read` (no Axmem scope) was
  already refused every tool over MCP and now is over REST too, so it needs an Axmem scope such as `read` or `*`. The cached
  identity of a token keeps its scopes. A route cannot be registered without a
  tool (a start-up error), and an end-to-end test fails for any route that is neither mapped nor public.
- **Request bodies must be JSON:** a REST request that carries a body without a JSON content type answers `415`
  before the body is read. A web page can send `text/plain` cross-origin with no preflight, and the routes used to
  parse such a body as JSON.
- **No credential, no web page:** with `AUTH_MODE=none` the HTTP server (REST and `/mcp`) answers `421` to a request
  whose `Host` is not `localhost` or a loopback address (DNS rebinding) and `403` to one whose `Origin` is neither a
  loopback origin nor an origin named in `CORS_ORIGINS` while `CORS_ENABLED=true` (a `*` never counts, `null` is
  refused, and with CORS off no origin counts). The new setting
  `AXMEM_ALLOWED_HOSTS` (a JSON list, empty by default) names further hosts that the server answers, such as
  `host.docker.internal` for a client in a container, which the test harness' throwaway server now lists. Clients that
  are not browsers send neither a foreign `Host` nor an `Origin` and are not affected. The credential modes are
  unchanged.
- **Operator commands:** `axmem import` creates the collections when they are missing (a new Qdrant), and
  `axmem export` and `axmem backup` on a store without collections stop with `The alias <name> does not exist yet`
  instead of a raw 404; an export reads the live generation it resolved first. `--re-embed` that fails or is
  interrupted after the alias switch had begun says that the live collection may already be the new one, instead of
  saying it was discarded.
- **Memory correctness:** the vector read for similar-memory search and the existence check for new links use quorum
  reads, so a write is visible to the next request on any node of a cluster; a list with an offset at or past the
  end answers an empty page with the total, without reading the collection; memory ids in the tool examples are
  UUIDs.
- **Gateway provider:** `GATEWAY_METADATA` is plain text that the adapter parses (`{}` by default, a blank value is
  `{}`; anything but a JSON object of string values is refused with a message naming the setting), so a malformed
  value no longer stops the settings from loading. A refusal that concerns the whole call (status 401, 403 or 404,
  retries used up, a reply of the wrong count or width) raises `EmbeddingUnavailableError`, and a targeted rebuild
  records the batch as failed at once instead of retrying every memory against a gateway that is down.
- **TensorRT:** an ONNX graph with a floating-point tensor that is not fp32 is refused (`build-failed`), because the
  builder would otherwise run it in a lower precision; NVML failures, an unreadable model manifest or a model
  directory that cannot be written, and an engine file that cannot be read are start-up errors with a cause
  (`gpu-error`, `model-unavailable`, `engine-unusable`) and not tracebacks; the model manifest is written
  atomically.
- **Platforms and documentation:** the Nix flake no longer lists `aarch64-darwin` (there is no NVIDIA GPU
  there); the README architecture picture of the upstream project is a Mermaid diagram; the setup skills, the
  connectivity guide, the configuration reference and the GPU guide describe what Axmem does now (the
  install route, local mode needing a GPU, the loopback and reverse-proxy warning, the real error messages).

- **Failures an operator can fix end in one line:** every command used to die in a traceback for a setting that does
  not validate (`LOG_LEVEL` was not validated at all; a port that is not a number; a `QDRANT_URL` without a host or with a
  port outside 1 to 65535), for a settings file that cannot be read or is not UTF-8, and for most start-up refusals. They
  now print `Error: <cause>` and exit with code 1, naming the setting and never repeating its value: `LOG_LEVEL` is
  matched without regard to case like `LOG_FORMAT`; `serve` builds the embeddings adapter of any provider (a remote
  provider without its key or URL, a package that is not installed, the reranker without its URL) and checks the scope
  ceiling, Qdrant (reachable, accepting the API key, version, collection layout) before it binds anything or starts the
  stdio server, instead of failing inside the web server's lifespan; an authentication provider that FastMCP's own
  constructor refuses (a JWKS URI together with a public key) is a refusal of `serve` only, like an incomplete mode.
  A wrong or missing Qdrant API key is named (`Qdrant refused the request (HTTP 401): check QDRANT_API_KEY`): Qdrant
  answers its version without a key, so the first real request found it, as a traceback, and the configuration guide
  said the version check would. `re-embed` and the operator commands (`backup`, `restore`, `export`, `import`,
  `qdrant-check`) report an unwritable `BACKUP_DIR`, a full disk, a failed HTTP call, a damaged JSON file, a model that
  cannot start (`import` on a host without a GPU printed a traceback) and an unavailable embeddings provider the same
  way; anything that is not such a cause is a bug and keeps its traceback. `axmem auth login` reports a settings file
  it cannot write. A failed `backup` no longer leaves an empty directory when Qdrant refuses the first question.
- **Bounds on requests:** a search `query` and its `query_context` (`MEMORY_QUERY_MAX_LENGTH`,
  `MEMORY_QUERY_CONTEXT_MAX_LENGTH`, 2000 characters each; one request could hold the only embedder worker and the
  reranker for every tenant), the number of `source_files` of a memory (`MEMORY_SOURCE_FILES_MAX_COUNT`, 50), the
  number of memories one link or unlink call names (`MEMORY_LINKS_MAX_PER_REQUEST`, 50) and the length of a tool or
  category name of the meta-tools (100) are bounded, and the body of an HTTP request is limited to
  `REQUEST_BODY_MAX_BYTES` (4 MiB; `413`, by `Content-Length` or counted while a chunked body is read). The reason of
  an obsolete mark must be a text that is not blank and at most `MEMORY_OBSOLETE_REASON_MAX_LENGTH` (500) characters:
  a number or an object was stored as it was and then failed every read of the memory with a `500`, which also stopped
  `axmem export` and `axmem re-embed` for every tenant, and a multi-megabyte reason was returned with every read. A
  reason a client stored before this change is read back as text, so such a memory is exported and re-embedded again.
- **Logs:** a title, a search query and the tags of a filter are no longer written to the INFO log (the length of the
  query is), and the filter that masks secrets now covers the `extra` fields that the JSON format writes and shortens
  a text above 32768 characters to its head and tail. Its pattern for credentials in a URL was quadratic, so one
  tool name of 1 MB made of `://a:` kept the server busy for about a minute; it is bounded, and the legacy-settings
  warning no longer loses a word of its advice to the pattern for `token:` and `password:`. The console format
  writes colour codes only to a terminal and not when `NO_COLOR` is set.
- **Health:** `/health` answers `503` with the reason once a local TensorRT engine met a CUDA error it cannot recover
  from (every later embedding or rerank call fails until the process restarts, and the route kept answering `200`),
  and a failed call now records such an error at the call, where the check that followed a successful call was never
  reached. With `CORS_ENABLED=true` the server starts uvicorn with the same options as without (it lacked the
  graceful-shutdown limit, so a stream held open could keep `dispose_runtime` from running).
- **Memory create:** a failure of the automatic linking after the memory was stored (a Qdrant timeout, a read-back that
  missed) no longer fails the call, which made the client retry and store the memory twice: the call succeeds without
  links and logs a warning that names the memory id (`rebuild_embeddings` for that id links it).
- **Backup, export and import:** `restore` reads the manifest as untrusted input and, before it uploads anything,
  refuses one that is not valid, that names a file other than a `node<N>-memories|links.snapshot` of the set, or a
  collection that is not this deployment's (`<prefix>_links`, `<prefix>_memories_<generation>`), or that lacks the
  memories or the links snapshot of node 1; snapshots are checksummed in chunks. `export` writes its `.partial` file
  through a descriptor it created itself (a stale file or a link at that name is removed, not followed), so a link planted in a
  shared directory no longer redirects the export onto another file. `import` skips and counts a line that is not
  UTF-8, is nested too deeply, holds a number of thousands of digits, has an id or a version of the wrong type, no
  text to embed or text that cannot be encoded, instead of stopping at the first of them.

- **Tests, CI and hooks:** the Qdrant preflight of the suites pulls `qdrant/qdrant:v1.19.2` when the image is not local (a
  clean or pruned runner, or a bumped version) and still ends the run with exit code 2 and the cause when Docker or the
  pull fails. The jobs on the self-hosted GPU runner no longer run for pull requests from forks (the head repository must
  be this one; the weekly `mutation` job runs on schedule and manual dispatch only; no workflow uses
  `pull_request_target`), and the wheel smoke test still runs for them. The weekly mutation run has a concurrency group
  of its own, so a push to the default branch no longer cancels it. The mutation sandbox holds `.githooks/` and
  `.github/`, which the hook and workflow tests read, and the protocol-coverage test ignores the names mutmut adds to a
  mutated class, so mutmut's baseline run passes (checked with the gate on `app.utils.url_helpers*`). `denylist-check` fails closed
  (exit 2, naming the line numbers of the list that are not valid expressions, never the terms) when grep cannot use the
  list, no longer drops added lines that begin with `++`, matches against the line without the diff column (so `^term`
  works) and accepts a term that begins with a dash. It checks each line of the list on its own and passes grep the
  lines as separate patterns, instead of joining them with `|` (two invalid lines, such as a term that ends in a
  backslash followed by another term, could join into a valid expression and silently stop enforcing the neighbour; a
  back-reference now keeps its own groups); a path that grep cannot read (a directory, a dangling symlink, a file
  without read permission) also exits 2 instead of passing; and `git`, `grep` and `awk` calls are captured one by one, so
  a failing or missing `awk`, a failing `git` or an unknown revision stops the check with exit 2 where the pipeline status
  used to hide it. The settings-defaults snapshot masks only credentials, so the
  defaults of `MEMORY_TOKEN_BUDGET`, `MEMORY_KEYWORDS_MAX_COUNT`, `MEMORY_KEYWORD_MAX_LENGTH` and `TOKEN_CACHE_*` are
  pinned; a schema test no longer leaves a collection behind; the tenant id derivation is pinned by tests; the trivy
  image is pinned (`aquasec/trivy:0.75.0`); the wheel smoke test installs the dependencies from `uv.lock` (NVIDIA's
  index for TensorRT, without `tensorrt-cu13-libs`) and the wheel on top, instead of resolving the PyPI placeholders of
  the TensorRT packages. `pytest`, `pytest-asyncio` and `requests` (test-only) moved from the runtime dependencies to the
  `dev` group and the unused `fastapi` pin was removed, so an installed Axmem no longer pins them (`requests` still arrives through the SDKs that need it).

### Security

- Authentication fails closed (`3201a2c3`). A server with no `AUTH_MODE` refuses to start (exit code 1), `AUTH_MODE=none` is accepted only on stdio or on a loopback bind (`127.0.0.1`, `::1`; a hostname such as `localhost` does not count), and the credential modes are refused on stdio, which carries no bearer token. A request-level check refuses to serve when no provider is attached and the mode is not `none`. Fixes the finding that, without `FASTMCP_SERVER_AUTH`, every request was the same default user (`app/middleware/auth.py`), so any caller of a network-bound server read and changed every memory. Existing deployments that relied on that default must set `AUTH_MODE` (and bind to a loopback address if it is `none`) before the next start.
- Tenant isolation is tested on every access path: tenant A cannot read, update, delete, link, unlink, search or list tenant B's memories over the MCP tools, the REST routes and the CLI (local and remote), and another tenant's id answers exactly like an id that does not exist.
- Static tokens are stored as SHA-256 hashes only, compared in constant time, and never logged; the settings validator refuses a value that is not a 64-character hexadecimal digest without repeating it, so a token pasted by mistake is not echoed. A static token has no expiry: revoke it by removing its hash and restarting.
- PostgreSQL has no row-level security: there is no `CREATE POLICY` in `app/` or `alembic/`, isolation is a
  `user_id` predicate in each query, and a role that connects as the PostgreSQL bootstrap superuser (as the removed
  Docker deployment did) would bypass row-level security anyway. This goes away with the move to Qdrant, where isolation is a tenant
  filter applied in one place.
