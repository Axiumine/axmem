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

- Quality gates run from one orchestrator, `scripts/quality`, used by the Git hooks and by hosted CI for its static gates: ruff, import-linter,
  bandit, semgrep, vulture, radon, mypy, pylint, syrupy snapshots, 100 % branch coverage, memray budgets, trivy,
  Qodana, mutmut and GitNexus (proposed upstream as #163).
- `GOVERNANCE.md`, `MAINTAINERS.md` and `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), adapted from the ones of Sankey Studio
  to Axmem: the roles and the access rules of the two maintainers, who decides, and how conduct reports are made and handled.
- `CONTRIBUTING.md`: how to report a bug and send a pull request, the rules for code,
  tests and commits, the license of contributions (`AGPL-3.0-only`), and the hardware policy: Axmem supports NVIDIA GPUs on
  Linux only, requests for other devices are not taken, and a pull request with code that works natively on macOS is the one
  way to add one.
- `package` quality gate and `python -m scripts.quality dist-check <dir>`: the wheel and the sdist hold Axmem's source and
  nothing else, so the model weights, the TensorRT engines and the NVIDIA libraries, which are not part of the AGPL
  release, cannot reach a package. The gate builds both from the files a build picks up (tracked, and untracked that git
  does not ignore) in a copy, and refuses any file that is not Python source under `app/` or `main.py`, a native library,
  a file above 256 KiB, a `.py` file with binary content, a package above 2 MiB, a wheel that is not pure Python, and
  license metadata or license files that differ from `pyproject.toml`. It runs in `pre-commit` (when a staged file goes into
  the package), in `pre-push`, in the `ci` stage and in the `smoke-test` job of `ci.yml`; `publish.yml` runs `dist-check` on
  the files it uploads, before the upload.
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

- **License:** Axmem is released under the GNU Affero General Public License, version 3 (`AGPL-3.0-only`), no longer under
  the MIT License. The MIT License of Forgetful (Copyright (c) 2025 Scott Raisbeck) allows this; its notice moved unchanged
  to `LICENSE-FORGETFUL-MIT.md` (it was `LICENCE.md`) and the AGPL text is in `LICENSE`. `pyproject.toml` declares
  `AGPL-3.0-only AND MIT` (PEP 639) with both files as `license-files`, and the sdist include list now names a `LICENSE` that
  exists (it named a file that did not). The models and the NVIDIA libraries are fetched by the user and are not part of the
  release; [docs/gpu-models.md](docs/gpu-models.md) lists their licenses, including that the default reranker was trained on
  MS MARCO, whose terms say non-commercial research only. The skills are under the AGPL too (`license: AGPL-3.0-only`, they
  said `MIT`), the copyright of Axmem's own code is Giovanni Manzoni's (README, `authors` in `pyproject.toml`), and the sdist
  `include` list is anchored (`/README.md`, not `README.md`), which kept the README files of `docs/`, `skills/` and
  `test_harness/` out of the sdist.
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
- `BACKUP_DIR` now says where `forgetful backup` writes (an export writes the file named on its command line); the database backup that `--re-embed`
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
  subprocess, one invalid `cudaMemcpy`) remain. The suites that run these engines need an NVIDIA GPU, which hosted runners do not have, and the project has no
  self-hosted runner: they run on the developer machine in the `pre-push` hook (see the CI entry under Fixed). The
  static gates, the architecture tests, `trivy` and the wheel smoke test run on hosted runners, and the static job
  installs without the TensorRT libraries. The Windows leg of `ci.yml` is gone.
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
    Forgetful stays only in the README attribution, `LICENSE-FORGETFUL-MIT.md`, this history, `docs/changes-from-forgetful.md` (and
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
  collection that is not this deployment's (`<prefix>_links`, `<prefix>_memories_<generation>`), or that does not hold
  exactly one memories and one links snapshot for each configured node (see the review of the storage layer below);
  snapshots are checksummed in chunks. `export` writes its `.partial` file
  through a descriptor it created itself (a stale file or a link at that name is removed, not followed), so a link planted in a
  shared directory no longer redirects the export onto another file. `import` skips and counts a line that is not
  UTF-8, is nested too deeply, holds a number of thousands of digits, has an id or a version of the wrong type, no
  text to embed or text that cannot be encoded, instead of stopping at the first of them.
- **Review of the robustness fixes:** a `DELETE /api/v1/memories/{id}` whose chunked body passed `REQUEST_BODY_MAX_BYTES`
  was answered `413` and still marked the memory obsolete, because the route took the cut-off body for an empty one; it
  answers `400` and changes nothing. A setting that is not a choice (`LOG_LEVEL`, `LOG_FORMAT`, `EMBEDDING_PROVIDER`,
  `RERANKING_PROVIDER`, `AUTH_MODE`) printed its value back in the `Error:` line (`Unsupported LOG_LEVEL setting:
  '<value>'`), so a key pasted into the wrong setting reached stderr; the line names the setting and the choices only.
  `restore` of a manifest nested deeper than the JSON parser goes ended in a traceback, and is one `Error:` line now. The
  bounded pattern for credentials in a URL masked less than the first one: a password with `/`, `?` or `#` was left
  in the log. It is masked again, up to 4096 characters; one with a space, or longer, is still not masked. A `GATEWAY_URL`
  with an empty `?` or `#` (which the check for a query or a fragment let through, and the path was appended after the
  marker) is refused at start-up.

- **Review of the storage layer:**
  - **`restore` on a cluster:** it uploaded only the first node's snapshot, so with a replication factor below the number
    of nodes (where a node stores only some of the shards) it brought back part of the data, silently. It now checks
    that the manifest holds one memories and one links snapshot for each node of `QDRANT_URL` and no other node (the
    manifest records each file's node URL; the file name must be `node<N>-<role>.snapshot` for its own node and the
    memories snapshots must be of one generation), verifies every checksum, uploads each file to the node it came from
    under a one hour timeout, waits after each file until every replica of that collection is `Active` again (up to 10 minutes;
    uploading the next node's file while replicas recover left every replica of a shard `Dead` in two of four repeated
    restores at replication factor 2; a replica still not `Active` after 90 seconds, which Qdrant sometimes leaves in
    `ManualRecovery` after a restore into an emptied cluster, gets its file uploaded once more, which activates it), and
    makes the generation live after the last file. A backup of other nodes, or of the same
    addresses in another order, is refused before anything is uploaded. The cluster tests restore for replication factors 3, 2 and 1, each over
    collections that exist and over an emptied Qdrant.
    `backup` deletes the snapshot from its node whatever happens to the download (a failed download left a snapshot as
    large as the node's shards behind each time; one that cannot be deleted is named in the log), and takes the snapshot
    under the same long timeout instead of the ordinary request timeout, which gave up on a large collection while the
    node went on and kept the snapshot unnamed (a snapshot that takes longer than that one hour is still not waited for; the
    cluster-scale entry below says how it is found and deleted).
  - **`re-embed` and the layout settings:** a changed `QDRANT_SHARD_NUMBER`, `QDRANT_REPLICATION_FACTOR` or
    `QDRANT_WRITE_CONSISTENCY_FACTOR` stopped the server with a message that told the operator to "create a new
    collection", which `re-embed` could not do (it refused to open the old collection with the same message, and left the
    links collection in the old layout). `re-embed` now opens the old collections whatever their layout, prints the layout
    the new one gets, fills the new generation in it, and rebuilds the links collection in that layout through a staging
    copy that is removed at the end; nothing is removed before the copy holds every link, and a run that stopped half
    way is finished by running it again. `ensure(check_width=False)` became `ensure(replacing=True)`, which tolerates the
    layout, the width and the embedder that are about to be replaced and nothing else. The start-up message names
    `axmem re-embed`.
  - **Another embedder of the same width:** a provider, model, precision (`EMBEDDING_TF32`) or gateway backend that
    produced vectors of the same width passed the start-up check, and queries were then embedded in another vector
    space than the stored vectors. Every new memory generation records the embedder that fills it (collection metadata
    `axmem_embedder`; a test now checks that it comes back with a snapshot restored into an emptied Qdrant, on one node and in
    the cluster), and the server refuses to start on a mismatch with a message that names both
    and `axmem re-embed`. A collection that recorded nothing is taken to be the configured embedder's and records it,
    with a warning.
  - **Edits that were overtaken:** a write that landed and was then overtaken by two more writes before the writer read it
    back was reported as lost and replayed over the newer state (the point kept only the latest claim token). Each point
    now keeps the tokens of its 16 earlier versions (`op_history`, a payload field written from now on; points without it
    work), the read-back finds its token by version, and a write that more than 16 writes overtook is answered with
    `OutcomeUnknownError` (409, "the outcome cannot be told ... read it again and repeat the change if it is missing")
    instead of being replayed or reported as lost.
  - **An edit that straddled a re-embedding switch:** `update_memory` read the memory from the live alias and wrote
    through the alias again, so a switch of the alias between the two wrote a vector of the old model into the new
    generation. An attempt now works on one generation from its read to its read-back and, when the alias moved
    meanwhile, is made again on the live one.
  - **Reads that decide a write or copy data away** ask a majority of the replicas: the memory an edit or an obsolete
    mark works on, the superseding memory, the existence checks of unlink, `get_memory_by_id`, `export`, `import`, and the
    scans and checks of the re-embedding copy (a stale replica could report a memory missing, apply an edit to an old
    copy, or leave a newer memory out of an export or of the new generation). Qdrant returns a point from such a read
    only while the replicas it asks hold the same version of it, so a memory that other writers are rewriting at that
    moment can be missing from the answer: the reads that decide a write or answer for one memory (the memory an edit
    or an obsolete mark works on, the superseding memory, the targets of a link, `get_memory_by_id`) repeat it a few
    times and, when the replicas never agree, fail with `ConflictError` instead of reporting the memory missing or
    skipping a link. The cluster test of overlapping edits found it.
  - **A conflict is a 409 on every REST route:** `GET /api/v1/memories/{id}`, `POST` and `GET .../links` and
    `DELETE .../links/{target}` caught only `NotFoundError`, so a memory the replicas kept disagreeing about
    (`ConflictError`, which `get_memory_by_id` and the targets of a link now raise instead of reporting the memory missing)
    ended as a 500, and `GET .../links` failed as a whole for one such linked memory where it used to skip a missing one.
    `handle_request_errors`, which wraps every memory route, now answers any `ConflictError` as `409` with the message, so
    no route can leave one unanswered; the explicit handlers of `PUT` and `DELETE` are gone for it. A list of links with
    a linked memory the replicas disagree about is a 409, never a shorter list.
  - **The read-back of a write waits for the replicas as well:** `QdrantStore.landed` made one majority read and, when it
    returned nothing, took the write for lost. A majority read returns nothing while the replicas hold different versions
    of the point, which is how a point looks that another writer has just overtaken, so an edit that had landed was
    replayed over the newer one (and an obsolete mark that had landed was refused as "already obsolete" or retried). It
    now reads like every read that answers for one point (`get_agreed`): it waits for the replicas to agree, and when they
    never do it raises `OutcomeUnknownError` (409; the write may have been applied: read the memory and repeat the change
    if it is missing) instead of guessing. A point that no read finds is still a write that did not apply.
  - **A `re-embed` that stopped while it moved the links left a server that started without them:** the links collection
    is removed and made again in the new layout before the staged links are copied back, and a run that stopped in between
    left a collection that was missing or empty and passed every start-up check, so `axmem serve` started with no links
    and no warning (and a backup or an export would have left them out). `serve` and `import` (the schema check), `backup`
    and `export` now refuse, naming `<prefix>_links_relayout` and `axmem re-embed`, while that staging collection exists,
    and never create an empty links collection in its place. The next `re-embed` finishes the move first, before it embeds
    anything (`rebuild_links(unfinished_only=True)`), so the links are back within moments whatever becomes of the rest of
    that run; the report says the links were rebuilt. `restore` removes the staging collection, since the snapshot is the
    source of truth for the links. [docs/embedding_migration.md](docs/embedding_migration.md) said that a half-finished run
    left nothing to restore and now describes this state and the cost of the run that ends it.
  - **Statements that said more than the code or the tests:** a snapshot "never stays on its node" (one that cannot be
    deleted is only named in the log, and one that the node finishes after the 3600 s and the look for it stays on the node), the cluster restore tests
    covering replication factor 3 over an emptied Qdrant (that layout is now tested) and the embedder record "kept in
    snapshots" (no test restored one; the single-node end-to-end test and every cluster restore layout now read it back
    before `ensure()` could record it again).
  - **Smaller:** moving the alias and creating it are repeated after a lost answer without failing on the alias that
    had moved (one request, checked first); a generation that cannot be discarded is retried, and a failed discard no
    longer replaces the error that ended the run (or the validation report). `qdrant-check` says when nodes could not be
    read (`N of M nodes could not be read, so the nodes were not compared in full`) and no longer words that as a
    disagreement of the points it could not compare. `get_linked_memories` returned the most important of only the first
    1000 neighbours of a memory; it now ranks every neighbour (their importance is read in chunks of 1000) before it takes
    the most important `max_links`. The checksum of a snapshot was already computed in chunks at this revision, and a test
    now pins it.

- **Review of the engine and provider errors:**
  - **A `tokenizer.json` that cannot be parsed** raised a bare `Exception` from the `tokenizers` library, which no start-up
    handler knows, so `serve` and `re-embed` ended in a traceback. It is a `model-unavailable` start-up refusal that names the
    file and the model directory to delete, like the other model-file errors.
  - **A cached engine that TensorRT refuses is no longer deleted on the spot:** every deserialize failure of a plan whose
    fingerprint matched removed the cached directory, which a GPU too full to hold the engine (or a driver that does not
    suit it) caused as well as a damaged file. An entry that lost its plan or its fingerprint is still removed and
    rebuilt; for a plan that TensorRT refuses the start builds a plan from the same model first and replaces the entry
    only when that one loads, and when TensorRT refuses the new plan too it keeps the cached entry, stores nothing and
    refuses the start with `engine-unusable` and TensorRT's message. A plan is cached only after this process has loaded
    it, a plan or a fingerprint file that cannot be read is no proof of damage (the cache is the cause of the refusal,
    nothing is removed; the fingerprint of another, older entry that cannot be read only leaves the reason of the
    rebuild without its comparison), and an engine loaded before the cache failed to store it or to prune its siblings
    is released.
  - **A closed engine, or one that met a CUDA error it cannot recover from, raises `TensorRTUnavailableError`**, a
    `TensorRTError` that is also a `ModelUnavailableError` (not an `EmbeddingUnavailableError`, see Operator errors
    below; so does the check that finds the error after a call that returned, and
    so does the call that first meets the error: it used to raise the plain `TensorRTError` of its own failure, and only
    the calls after it were refused, so the batch that met the error was still retried memory by memory; the failure of
    that call stays in the exception chain), so a targeted rebuild records the batch as failed at once instead of
    retrying every memory against an engine that refuses them all. The messages of the later calls are unchanged.
  - **A failed start releases the reranker's engine:** only the embedder's warm-up was guarded, so a reranker whose
    warm-up failed kept its engine and device buffers while anything held the exception. Both adapters now release the
    engine when anything after the engine was loaded fails, including the start-up report (CUDA context and GPU memory).
  - **`probe_gpu` fails with `TensorRTStartupError` at every step:** a CUDA error after the device count (properties,
    compute capability, memory) was a plain `TensorRTError`, which the documented causes and the preflight of the test
    suites do not expect. It is `no-gpu`, `driver` or `gpu-error` by the same table as the device count.
  - **The Azure, Google, OpenAI and Ollama adapters check every reply:** a vector of another width than
    `EMBEDDING_DIMENSIONS` was returned as it came (only the TensorRT and gateway providers checked), to be refused by
    Qdrant on a write or searched against a collection of another width. It now raises `EmbeddingUnavailableError`
    (`<provider> returned N-dimensional embeddings but EMBEDDING_DIMENSIONS is M. Set EMBEDDING_DIMENSIONS=N, or choose a
    model that produces M ...`) and a number that is NaN or infinite is refused. The adapters stay as they were otherwise;
    none of them is asked at start-up.
  - **`BACKUP_DIR` is where `axmem backup` writes, not `axmem export`:** the code was right and the documentation
    (the setting comment, `.env.example`, the configuration reference) was not. `export` takes the file to write on its
    command line, owner-only like a backup, and never touches `BACKUP_DIR`; a test pins it.

- **Tests, CI and hooks:** pylint no longer checks the members of the native modules `tensorrt` and `cuda.*`, which it
  could not see and reported as missing whenever they were installed, so a commit that touched `tensorrt_runtime.py`
  with few other files failed the pre-commit score on errors that were never real. The Qdrant preflight of the suites pulls `qdrant/qdrant:v1.19.2` when the image is not local (a
  clean or pruned runner, or a bumped version) and still ends the run with exit code 2 and the cause when Docker or the
  pull fails. The mutation sandbox holds `.githooks/` and
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
  index for TensorRT, without `tensorrt-cu13-libs`) and the wheel on top, instead of resolving the two PyPI placeholders of
  the TensorRT packages (`tensorrt-cu13`, `tensorrt-cu13-libs`). `pytest`, `pytest-asyncio` and `requests` (test-only) moved from the runtime dependencies to the
  `dev` group and the unused `fastapi` pin was removed, so an installed Axmem no longer pins them (`requests` still arrives through the SDKs that need it).
- **CI runs only what needs no GPU and no Docker; the pre-push hook is the GPU gate:** the earlier CI moved the test,
  snapshot, coverage, memory and mutation jobs to a self-hosted GPU runner behind a guard against pull requests from
  forks. There is no such runner and hosted runners have no GPU, so those jobs (`ci.yml` `test`, the whole of `e2e.yml`,
  and the `snapshots`, `coverage`, `memory`, `qodana` and weekly `mutation` jobs of `quality.yml`) are gone, with the fork
  guard that protected them and the schedule. GitHub Actions now runs, on hosted `ubuntu-latest` runners only, ruff, the
  architecture tests (which include the rules of the workflow files), the static gates of `quality.yml` (ruff, import
  boundaries, bandit, semgrep with the registry packs, vulture, radon, mypy, pylint), `trivy` (the pinned release binary,
  checked against its SHA-256, instead of a Docker image) and the wheel smoke test, all installed with `--locked` and
  without `tensorrt-cu13-libs`; the steps were run in a clean container with no GPU and no Docker. `tests/architecture/test_workflows.py` now fails
  on a self-hosted or non-Ubuntu runner, a container or service, a schedule, `pull_request_target`, a secret, a token that
  is not read-only, a checkout that keeps its credentials, a gate that needs the GPU or Docker, an install that fetches the
  TensorRT libraries, a missing concurrency group, and a trivy version that differs from the gate's; `test_mutation_sandbox.py`
  checks that mutation is a gate of the pre-push hook and of no workflow. Both concurrency groups are now
  `workflow-ref` (the event no longer matters without a schedule). The `pre-push` hook is the one place that runs the
  suites: tests, the Qdrant cluster suite, 100 % coverage, memory budgets, Qodana and (opt-in) mutation. A gate whose
  tool or daemon is missing (the cluster suite without Docker, trivy with neither a binary nor Docker, Qodana without
  its CLI, semgrep with nothing to run it) now fails with the cause instead of reporting `SKIP`, and `trivy` runs with
  the static gates, before the long suites. The hook refuses the push of a commit that is not the checked-out `HEAD`
  (it used to skip the pipeline for such a push and let it through unchecked), and still lets a deletion and a tag on
  `HEAD` through its checks. Tests run real `git push` commands against a bare repository for the hook, each gate of the
  pipeline failing, the order of the gates, and the real suites ending their run without a GPU and without Docker.
  `stage ci` is the set of hosted gates. Two findings of the first clean run were fixed: semgrep's registry packs
  flagged the two log lines that name a rejected token's client id (inline `nosemgrep` with the reason, in
  `app/middleware/token_verifiers.py`), and `trivy` reported `certifi` 2026.1.4 (GHSA-6q48-6c7w-9gw2, critical, fixed in
  2026.10.19), so `uv.lock` now locks `certifi` 2026.10.19 (nothing else moved).
- **Documentation review:** statements that the code contradicted were corrected. `docs/tool_reference.md` gives the
  real return values (`mark_memory_obsolete` and `unobsolete_memory` return `{"success": true}`, `query_memory` the
  whole result object), lists every parameter of `get_recent_memories` and the provenance fields `agent_id`,
  `agent_version` and `agent_model`, and documents the tenth tool, `rebuild_embeddings`; `docs/api_reference.md` shows
  the real search response (`primary_memories`, `linked_memories`, `scores`, `total_count`, `token_count`,
  `truncated`) and `obsolete_matches` in the create response; `docs/prompts/custom_agent_integration.md` says ten tools
  and names `linked_memory_ids` instead of a field that never existed. The Copilot CLI, OpenCode and Gemini CLI guides
  and the two `axmem-setup` commands start `axmem` from a checkout with `AUTH_MODE=none` (or a bearer token for a
  server on a network) instead of a `uvx axmem` that has no package to resolve and a server that exits without an
  auth mode, drop the OAuth sign-in, and no longer point at a log file that nothing writes (the log goes to standard
  error); the Axmem CLI section of the connectivity guide installs with `uv tool install .`. `docs/search.md` lists
  all six embedding providers and the search settings in place of an empty section; the architecture overview names the
  tenant in the link id and anchors every line link at the right symbol; `docs/quality-gates.md` describes how
  the mutation gate is scoped instead of `SKIP`;
  `docs/dev/architecture-tests.md` names the `lint` job and seven rules; the test harness README counts its six
  skills. `.env.example` no longer pins `SERVICE_VERSION` (a copied file made `/` and `/health` report a stale
  version for every release) and lists the settings it left out; `docs/configuration.md` warns about the `["*"]`
  default of `CORS_ORIGINS`. `docs/gpu-models.md` explains that the tiktoken data is fetched on the first query unless
  `TIKTOKEN_CACHE_DIR` holds it, and that a host which drops packets makes that query wait for the connect timeout, with
  the pre-load command. Three of these corrections were themselves wrong and were fixed: `skipped_ids` of
  `rebuild_embeddings` holds only a memory that was edited, made obsolete or deleted while the rebuild ran (an id that
  does not exist, belongs to another tenant or is obsolete is a `failed` entry with `not_found_or_not_owned`, and an
  `auto_link_failed` entry also carries `phase`), in `docs/tool_reference.md`, `docs/embedding_migration.md` and the
  tool's own description (the discovered tool catalogue snapshot follows); `.env.example` puts the explanation of
  `FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY` (and of `GATEWAY_TENANT` and the provenance defaults, which had the same fault)
  on its own line, because a comment after an empty value is read as the value and a copied block made both a JWKS URI
  and a public key reach the verifier, which refuses to start; `docs/gpu-models.md` sets `TIKTOKEN_CACHE_DIR` in the
  process environment, since tiktoken reads only that and a line in a settings file never reaches it. The mutation
  sandbox copies `.env.example`, which `tests/integration/test_reference_docs.py` parses.
- **Start-up, tokenizer:** the tiktoken encoding is loaded before the server serves (`preload_token_counter`, in a
  daemon thread that `TIKTOKEN_LOAD_TIMEOUT_SECONDS` bounds, 30 seconds by default) instead of on the first query. A host
  with the data in `TIKTOKEN_CACHE_DIR` starts with no network; a host that has no cache and cannot download the data
  within the limit (it drops packets, or has no network) ends the start with one line, which names `TIKTOKEN_CACHE_DIR`
  and the command that pre-loads the data: `Error: the token counter cannot start: ...` for `serve`, and
  `Error: the tiktoken encoding of the token counter cannot be loaded (...)` for the commands that run the tools in
  process (`axmem memory`, `axmem call`, `axmem tools`), where the first query used to wait out the connect timeout and
  then fall back to an estimate. A host without a cache but with a working network downloads the data and starts.
- **Wheel install:** a wheel names the three pinned TensorRT packages without an index, because `[tool.uv.sources]` is not
  part of a built wheel, and on PyPI `tensorrt-cu13` and `tensorrt-cu13-libs` are placeholders that fetch the 3.8 GB of
  libraries from NVIDIA's index while they build (`tensorrt-cu13-bindings` is a real wheel there). The README (Installing
  the package) lists the commands that work for `uv tool install` and pip from a checkout, a Git URL and a built wheel
  (`--extra-index-url https://pypi.nvidia.com`); the smoke test of
  CI resolves the built wheel with that flag and checks that the packages come from NVIDIA's index; a test keeps the
  documented commands in step with `pyproject.toml`. The redundant `ollama` optional extra (the SDK is a dependency) is
  removed from the package and from `uv.lock`.
- **Old configuration directory:** the settings file of the directory the old name used (`~/.config/forgetful/.env` on
  Linux) and `docker/.env` are no longer read, and the start now says so when one exists, naming the file and the
  settings in it (never a value) and the files that are read now; the file is left as it was.
- **CORS:** `Access-Control-Allow-Credentials: true` was sent whatever `CORS_ORIGINS` said, and the default is `["*"]`,
  so any web page could make credentialed calls and read the answers. Credentials are now off, and
  `CORS_ALLOW_CREDENTIALS=true` (refused together with `*` in `CORS_ORIGINS`) sends them to the named origins only.
- **Tool catalogue:** `link_memories` returns `{"linked_memory_ids": [...]}` (the targets that were newly linked) and
  `mark_memory_obsolete` and `unobsolete_memory` return `{"success": true}` and raise on every failure; the catalogue
  said "List of memory IDs" and "Boolean", and `execute_axmem_tool` callers that trusted it looked for a value that was
  never there. A test runs the three tools through the registry and compares the answers with the catalogue text.
- **Operator errors:** an SDK or HTTP error of a service other than Qdrant was named for Qdrant, or for the embeddings
  provider whatever failed. It now says which service failed (the embeddings gateway, the rerank service, the token
  introspection endpoint, Ollama, Google, OpenAI or Azure OpenAI, by the settings and the request that failed) without a
  URL or a key, and a failure that the web server's lifespan meets (Qdrant or a provider failing between the start-up
  check and the lifespan) ends as `Error: <cause>` with exit code 1 instead of the traceback of the lifespan. A closed
  TensorRT reranker or embedder raises `TensorRTUnavailableError`, which is no longer an `EmbeddingUnavailableError`
  (both are a `ModelUnavailableError`), so the reranker is not reported as the embeddings provider; `rebuild_embeddings`
  still stops at either.
- **TensorRT teardown after a failure:**
  - **A release that fails no longer hides the error that led to it, and no longer leaves the rest allocated.** After a
    sticky CUDA error (an illegal address, a hardware fault) every CUDA call reports it, the calls that destroy a stream
    and free a buffer included. `_Slot.release` stopped at the first of them, so the buffers behind it were never freed,
    `TensorRTEngine.close` stopped at the first slot (the later slots, the engine and the runtime stayed, and a second
    `close` did nothing because the engine already counted as closed), and the error of the release replaced the error
    of the warm-up that the adapter had been cleaning up after (a plain `TensorRTError` instead of the
    `TensorRTUnavailableError` the warm-up met). Every step now runs once, in the order context, stream, buffers, then
    the engine and the runtime, whatever a step reports; the steps that failed are one `ERROR` log line (the steps and
    each distinct reason) and `close` does not raise, so the error that ended the start is the one that surfaces.
  - **An engine or a slot that cannot be made releases what it made.** `_Slot` made its stream and buffers one after the
    other and `TensorRTEngine.__init__` made its slots in a loop; an out-of-memory error at the second buffer or the
    third slot left the stream, the buffers and the slots already made allocated on the device, with nothing that could
    free them (the half-made object is never handed out). Both now release what they had made and raise the original
    error. The adapters make their worker pool before the engine (a count the pool refuses used to build the engine
    first) and log the start-up report inside the guard that closes the engine on a failure, and their `close` releases
    the engine even when the wait for the workers fails. `_release_tensorrt_adapters` (at exit) closes every shared
    adapter, the latest first, even when one of them raises.
  - **Tests** (`tests/integration/test_tensorrt_teardown.py`): the failures are injected at the CUDA wrapper calls
    (`cudaMalloc`, `cudaStreamCreate`, `cudaFree`, `cudaStreamDestroy`, `cudaStreamSynchronize`,
    `cudaPeekAtLastError`) of `tensorrt_runtime` on the real engines, never in a model. They assert that the original
    error surfaces, that every stream and buffer that was made was released exactly once, that the secondary failures
    are one log line, and that the GPU memory of the process (read through `gpu_memory()`) is back to where it was; a
    loop of five starts, failed warm-ups and closes of a real embedder and of a real reranker shows no growth.
- **Tests:** pytest's base temp directory is made at the start of the session (a session fixture of `tests/conftest.py`),
  so that no test that records or replaces `atexit.register` can meet pytest's own registration of the clean-up of that
  directory (it passes five arguments), and the recorder of `tests/integration/test_cli_operator_commands.py` accepts
  the arguments of any registration. This is hardening: that file already made the directory first in its own fixture.
- **Documentation, corrected after a review of the runtime commit:** a closed TensorRT engine is a `ModelUnavailableError`
  and no longer an `EmbeddingUnavailableError` in the earlier notes too; on PyPI only `tensorrt-cu13` and
  `tensorrt-cu13-libs` are placeholders (`tensorrt-cu13-bindings` is a real wheel); `axmem memory`, `axmem call` and
  `axmem tools` print the tokenizer refusal without the prefix that `serve` adds (a test runs the command). The tests of
  the GPU-gate paragraphs of the README and `docs/contributors.md` (paragraphs of their own) and of the Quality row of
  `docs/changes-from-forgetful.md` (it no longer says that hosted CI runs every check), which the runtime commit left
  uncommitted, are committed with this one.
- **Cluster scale** (found by running the scans, the snapshots and the restore against writers and against clusters of
  other sizes; the cluster tests are the proof):
  - **Scans are complete or they fail.** A quorum read that asks for the content of a page leaves out a point whose
    replicas are not all at the same version at that moment. Beside eight writers rewriting 600 memories on three
    nodes (3 shards, replication factor 3, write consistency factor 2) such a scan missed a memory in about one round
    in three (and with one shard it once lost a whole page of 100); a read of the ids alone never did. `export`,
    `import` and the copy of `re-embed` (`outdated_in`, `copy_into`) could therefore write a file or
    a generation without a memory that existed (`qdrant-check` was never affected: it reads each node on its own, not by
    quorum, and keeps doing so; the first wording of this entry listed it by mistake). Every page is now listed by id with a plain read (`QdrantStore.page`)
    and the listed memories are fetched with the repeating quorum read (`get_agreed`, which now asks again only for
    the ids still missing); a memory the replicas never agree about ends the scan with a `ConflictError` (one `Error:`
    line on the command line, 409 over REST) instead of being left out. `import` and the copy checked their
    "already there" reads with a plain quorum read and now use `get_agreed` too. `export` counts the memories before it
    starts and refuses to write a file with fewer than the collection held then (memories are never deleted, so it
    would have lost one), and prints a `Warning:` on stderr when the counts at its end differ from the file's (writers
    created memories meanwhile). With writers running a scan is still a moving picture, which
    [docs/configuration.md](docs/configuration.md) now says; an exact copy needs the writers stopped. The cluster tests
    (`tests/cluster/test_cluster_scans.py`) hold the omission (a detector shows the old scan loses memories), 25 rounds of a complete scan, an
    export and the copy of a `re-embed` beside writers, and, as a control, `qdrant-check`'s per-node counts beside writers; 17 of the 21 new
    single-node tests (`tests/integration/test_qdrant_complete_scans.py`, replicas that disagree injected between the
    pool and the client) fail without the change.
  - **A failed snapshot request deletes the snapshot it left behind.** A request that gave up after the 3600 seconds the client
    waits (or whose connection dropped, or whose answer carried no name) left the snapshot the node went on to make
    on the node, unnamed, as large as its shards. `NodePool.create_snapshot` lists the snapshots of the collection
    before the request, and after any failure deletes the ones that were not there; after a timeout or a dropped
    connection it looks for up to 5 minutes more, because the node goes on, and says in the log which snapshots existed
    before when none shows up in that time. The listing, the deletion and the wait never replace the error the backup
    ends with. Tests: `tests/integration/test_qdrant_snapshot_cleanup.py` against an in-process stand-in for the snapshot
    routes of a Qdrant node (`tests/qdrant_fake_node.py`).
  - **The number a re-embed reports is the number it wrote.** `_replace_older` counted the memories carrying its own claim
    token after the write, so a write that another writer overtook (the memory is then newer than the copy, which is
    fine) was not counted, and the report could name fewer memories than the copy had processed. `copy_into` now reads back
    what it wrote and counts a memory when the new generation holds it at the version written or a newer one; a write
    that did not take effect is not counted. The cluster test with writers asserts the total (60 of 60; it was 58 before).
  - **A server that keeps running cannot write vectors of the old embedder into the new generation.** `ensure` checks the
    embedder of the live generation when a process starts; a server left running while `axmem re-embed` moved the alias to a
    generation of another model went on writing old-model vectors into it. `QdrantSchema.writable_generation` now checks the
    record of the live generation the first time this process sees it, and create, edit, targeted rebuild and import
    (which write by that generation's name) raise `EmbedderChangedError` (a `ConflictError`: 409, and the text is shown over MCP)
    and write nothing when it is not this process's embedder; a create (and each batch of an import) that straddles a switch to a generation of the same embedder
    is written again into the new one. A create, an edit or an import that the switch overtakes (the refusal comes after the write) did
    write: the memory is in the previous generation, and the catch-up copy of `re-embed` may or may not carry it into the new one.
    Its refusal says so (see the corrections below) instead of "Nothing was written". Reads are not affected. The procedure stays: stop every server before `re-embed`
    ([docs/embedding_migration.md](docs/embedding_migration.md), which also records that a run with writers across the
    switch lost an acknowledged edit in the cluster test, because the catch-up copy goes by version number and the two generations can
    hold different histories of a memory). Tests: `tests/integration/test_qdrant_embedder_switch.py`.
  - **Corrections to the cluster-scale review** (found by a second review of the commit above):
    - `qdrant-check` was listed among the scans that are now complete or end in a `ConflictError`. It never used a quorum scan
      (it reads each node on its own, which is its purpose), so it did not change; the changelog, the changes table and
      [docs/configuration.md](docs/configuration.md) no longer say it did.
    - A create or an edit that landed in the previous generation just before the alias moved to another embedder's generation
      was refused with "Nothing was written", although the memory was stored there and the catch-up copy of `re-embed` may
      carry it over: a client that repeated the request could store it twice. `writable_generation(written=Written.MEMORY)`,
      asked after the write, words the refusal as an open outcome (look for the memory before repeating). A refusal before
      the write keeps saying that nothing was written. Tests: `tests/integration/test_qdrant_embedder_switch.py` (the old
      generation holds the memory, the new one nothing).
    - An `import` that the alias overtook was refused with "Nothing was written" as well, although its earlier batches
      were stored in the previous generation, and a switch that came while its last batch was written was not noticed at
      all (the command ended with a summary). `import_memories` now passes what it has stored to
      `writable_generation(written=Written.IMPORT)` and looks again after each batch, like a create: the refusal says
      that the import was written, wholly or in part, that `re-embed` may or may not have carried it over, and that the
      import is repeated after the restart, which leaves a memory that exists (same id) as it is and so stores nothing
      twice; a batch that straddles a switch to a generation of the same embedder is written into the new one too. A
      refusal before the first batch is written keeps saying that nothing was written; one after it says that the import was written. `axmem import` is a command, so the refusal ends
      it with `Error: <text>` and exit code 1, not with a 409. Tests: `tests/integration/test_qdrant_embedder_switch.py`.
    - `export` counts the collections at its end before the file is put in place, not after. A transient Qdrant error
      there used to end the command with an error although the file was in place; it now ends it like any other failure
      of the export, with no file. Test: `tests/integration/test_qdrant_complete_scans.py`.
    - The statements that a snapshot taking longer than 3600 s stays unnamed are corrected: the node's snapshot is looked
      for for up to 5 minutes more and deleted; only one finished after that stays on the node, and the log lists the
      snapshots that existed before.
    - The `ConflictError` handler of the REST routes no longer says that every conflict is to be repeated:
      `EmbedderChangedError` needs a restart of the server.
  - **Clusters of other sizes and a restore into another cluster are tested** (`tests/cluster/test_cluster_backup_restore_other_clusters.py`,
    `start_cluster(nodes=...)` in `tests/qdrant_support.py`): see the results in
    [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md#facts-to-re-verify).

### Security

- `certifi` is locked at 2026.10.19 (was 2026.1.4, which bundles root CAs named in GHSA-6q48-6c7w-9gw2); it is a
  transitive dependency of the HTTP clients and the only entry of `uv.lock` that changed.
- Authentication fails closed (`3201a2c3`). A server with no `AUTH_MODE` refuses to start (exit code 1), `AUTH_MODE=none` is accepted only on stdio or on a loopback bind (`127.0.0.1`, `::1`; a hostname such as `localhost` does not count), and the credential modes are refused on stdio, which carries no bearer token. A request-level check refuses to serve when no provider is attached and the mode is not `none`. Fixes the finding that, without `FASTMCP_SERVER_AUTH`, every request was the same default user (`app/middleware/auth.py`), so any caller of a network-bound server read and changed every memory. Existing deployments that relied on that default must set `AUTH_MODE` (and bind to a loopback address if it is `none`) before the next start.
- Tenant isolation is tested on every access path: tenant A cannot read, update, delete, link, unlink, search or list tenant B's memories over the MCP tools, the REST routes and the CLI (local and remote), and another tenant's id answers exactly like an id that does not exist.
- Static tokens are stored as SHA-256 hashes only, compared in constant time, and never logged; the settings validator refuses a value that is not a 64-character hexadecimal digest without repeating it, so a token pasted by mistake is not echoed. A static token has no expiry: revoke it by removing its hash and restarting.
- PostgreSQL has no row-level security: there is no `CREATE POLICY` in `app/` or `alembic/`, isolation is a
  `user_id` predicate in each query, and a role that connects as the PostgreSQL bootstrap superuser (as the removed
  Docker deployment did) would bypass row-level security anyway. This goes away with the move to Qdrant, where isolation is a tenant
  filter applied in one place.
