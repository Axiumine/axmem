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
- Optional `gateway` embeddings provider (`EMBEDDING_PROVIDER=gateway`, `0e50a541`) that posts JSON to an HTTP embeddings gateway. Settings: `GATEWAY_URL`, `GATEWAY_PATH`, `GATEWAY_TOKEN` (a secret value), `GATEWAY_AUTH_HEADER`, `GATEWAY_TENANT_HEADER`, `GATEWAY_TENANT`, `GATEWAY_BACKEND`, `GATEWAY_MODEL`, `GATEWAY_AGENT_ID`, `GATEWAY_METADATA` (a JSON object of strings) and `GATEWAY_SUPPORTS_DIMENSIONS`; none is read or required unless the provider is selected, and the default provider is unchanged. Texts go out in requests of at most 256, each with a fresh `request_id`, an `X-Idempotency-Key` that a retry of the same chunk reuses and an `X-Correlation-Id` shared by one call. Statuses 429 and 503 are retried after `Retry-After` (at most 30 s), 502, 504 and network errors with a growing wait, four attempts in all; 403, 422 and every other status fail at once, with the status and `code` in the error and never the body. A reply is refused unless it has one finite vector per text and each is `EMBEDDING_DIMENSIONS` wide. Tests run against an in-process fake gateway.
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
- Standalone end-to-end test (`tests/e2e_sqlite/test_standalone_sqlite.py`): the real server in a subprocess with the
  GPU models, a static token, no `GATEWAY_*` variable and `HF_HUB_OFFLINE=1`, behind a guard that refuses and records
  every connection that is not loopback, runs create, auto-link, a reranked query, link and mark-obsolete. It uses
  file-backed SQLite until the Qdrant store lands.
- Tenants and static bearer tokens (`3201a2c3`). `AUTH_MODE=static` maps a fixed bearer token to one tenant: `AUTH_STATIC_TOKEN_HASHES` holds only the SHA-256 hash per tenant (never the token), a presented token is hashed and compared with every hash in constant time, one token belongs to one tenant, and a token never reaches a log line. For `jwt` and `introspection` the tenant is the claim `tenant_id`, else `tenant`, else `sub`; a blank claim falls through, and a claim of another type or over 255 characters is refused. `DEFAULT_TENANT` names the tenant of `AUTH_MODE=none`. Tests cover each mode (valid, invalid, expired and missing token), the claim order, the hash comparison and tenant isolation over REST, MCP and the CLI.
- `project` argument (a free-form string, trimmed, 1 to 500 characters, created on first use) on `create_memory`, `update_memory` (an empty string clears it), `query_memory`, `get_recent_memories` and `rebuild_embeddings`, the `project` field and query parameter of the REST memory routes, and `-p/--project` on the CLI memory verbs.
- **(planned)** Qdrant as the only store, authenticated with a Qdrant API key.
- **(planned)** Backups as Qdrant snapshots plus a JSONL export of the memories without vectors.

### Changed

- Project renamed to Axmem under the Axiumine organisation; README rewritten, Forgetful credited, MIT notice
  kept (`b121d350`). The package, CLI and environment variables still say `forgetful`.
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
  coming only from the router. The earlier plan for router-only embeddings is replaced; the `gateway` provider stays
  optional and no `GATEWAY_*` setting is read unless it is selected. `EMBEDDING_PROVIDER` defaults to `TensorRT`
  (it was `FastEmbed`) and an empty value selects it; existing databases need `forgetful --re-embed` only when the
  provider, model or width changes. `RerankerError` and `RerankerStartupError` are now `TensorRTError` and
  `TensorRTStartupError` (they cover both models), `reranker_model.py` is `tensorrt_model.py` and
  `docs/gpu-reranking.md` is `docs/gpu-models.md`.
- Users become tenants and projects become an optional scope string created on first use. The stored `users` row is the tenant record (matched by name in `external_id`) and `projects` is the table behind the scope, because the storage layer is replaced later and not migrated; no revision touches either table. A scope belongs to one tenant, so two tenants that use the same label have two scopes. Memory responses carry `project` (a string or null) instead of `project_ids`, and `ObsoleteMatch` does the same. The `AUTH_MODE` setting replaces `FASTMCP_SERVER_AUTH`, `DEFAULT_TENANT` replaces `DEFAULT_USER_ID`, `DEFAULT_USER_NAME` and `DEFAULT_USER_EMAIL`; an existing deployment keeps its memories by setting `DEFAULT_TENANT` to the old `DEFAULT_USER_ID` value. `GET /api/v1/auth/info` reports `authMode` as `disabled`, `jwt`, `introspection` or `static`. The tool scopes `users` and `projects` no longer exist (`read:memories` and `write:memories` remain). `.env.example` now sets `AUTH_MODE=none` and `SERVER_HOST=127.0.0.1`. The stdio and the HTTP examples in the README and the connectivity guide pass `AUTH_MODE=none` or a bearer token. The tool count goes from 17 to 10. The test harness server runs with `AUTH_MODE=none` on its loopback bind.
- **(planned)** Memory ids become UUIDs.
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
- **(planned)** Tests use a real Qdrant.
- `RERANKING_PROVIDER` accepts `TensorRT` (default) and `HTTP`; `FastEmbed` is now rejected at startup with an error
  naming the setting. The reranker needs an NVIDIA GPU; `RERANKING_ENABLED=false` or `RERANKING_PROVIDER=HTTP` runs
  without one. The reranker is Linux-only (the engine cache locks with `fcntl`) and the package classifier reads
  `Operating System :: POSIX :: Linux`. `docs/OFFLINE_SETUP.md`, `docs/configuration.md`, `docs/search.md`,
  `docs/contributors.md`, `docs/quality-gates.md`, `.env.example`, `README.md` and `architecture.md` describe the new
  reranker; the reranker model no longer lives in the FastEmbed cache and `FASTEMBED_LOCAL_FILES_ONLY` does not
  affect it.
- **(planned)** The package, CLI, environment variables and docs are renamed from `forgetful` to `axmem`.

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
- **(planned)** SQLite, PostgreSQL, pgvector and Alembic.
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

### Security

- Authentication fails closed (`3201a2c3`). A server with no `AUTH_MODE` refuses to start (exit code 1), `AUTH_MODE=none` is accepted only on stdio or on a loopback bind (`127.0.0.1`, `::1`; a hostname such as `localhost` does not count), and the credential modes are refused on stdio, which carries no bearer token. A request-level check refuses to serve when no provider is attached and the mode is not `none`. Fixes the finding that, without `FASTMCP_SERVER_AUTH`, every request was the same default user (`app/middleware/auth.py`), so any caller of a network-bound server read and changed every memory. Existing deployments that relied on that default must set `AUTH_MODE` (and bind to a loopback address if it is `none`) before the next start.
- Tenant isolation is tested on every access path: tenant A cannot read, update, delete, link, unlink, search or list tenant B's memories over the MCP tools, the REST routes and the CLI (local and remote), and another tenant's id answers exactly like an id that does not exist.
- Static tokens are stored as SHA-256 hashes only, compared in constant time, and never logged; the settings validator refuses a value that is not a 64-character hexadecimal digest without repeating it, so a token pasted by mistake is not echoed. A static token has no expiry: revoke it by removing its hash and restarting.
- PostgreSQL has no row-level security: there is no `CREATE POLICY` in `app/` or `alembic/`, isolation is a
  `user_id` predicate in each query, and a role that connects as the PostgreSQL bootstrap superuser (as the removed
  Docker deployment did) would bypass row-level security anyway. This goes away with the move to Qdrant, where isolation is a tenant
  filter applied in one place.
