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
- **(planned)** `gateway` embeddings provider that calls an optional HTTP embeddings gateway with
  batches of up to 256 texts, retries, `Retry-After` handling and dimension validation.
- **(planned)** Direct TensorRT reranker (TensorRT 11.x, strict fp32), with a fingerprinted engine cache,
  a warm-up before the service is ready and a startup check that refuses to run on anything else.
- Tenants and static bearer tokens. `AUTH_MODE=static` maps a fixed bearer token to one tenant: `AUTH_STATIC_TOKEN_HASHES` holds only the SHA-256 hash per tenant (never the token), a presented token is hashed and compared with every hash in constant time, one token belongs to one tenant, and a token never reaches a log line. For `jwt` and `introspection` the tenant is the claim `tenant_id`, else `tenant`, else `sub`; a blank claim falls through, and a claim of another type or over 255 characters is refused. `DEFAULT_TENANT` names the tenant of `AUTH_MODE=none`. Tests cover each mode (valid, invalid, expired and missing token), the claim order, the hash comparison and tenant isolation over REST, MCP and the CLI.
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
- **(planned)** NVIDIA GPU is a hard requirement for development and runtime; there is no CPU path and no skip.
- **(planned)** Embeddings come only from the router (`gateway`); reranking runs locally on the GPU.
- Users become tenants and projects become an optional scope string created on first use. The stored `users` row is the tenant record (matched by name in `external_id`) and `projects` is the table behind the scope, because the storage layer is replaced later and not migrated; no revision touches either table. A scope belongs to one tenant, so two tenants that use the same label have two scopes. Memory responses carry `project` (a string or null) instead of `project_ids`, and `ObsoleteMatch` does the same. The `AUTH_MODE` setting replaces `FASTMCP_SERVER_AUTH`, `DEFAULT_TENANT` replaces `DEFAULT_USER_ID`, `DEFAULT_USER_NAME` and `DEFAULT_USER_EMAIL`; an existing deployment keeps its memories by setting `DEFAULT_TENANT` to the old `DEFAULT_USER_ID` value. `GET /api/v1/auth/info` reports `authMode` as `disabled`, `jwt`, `introspection` or `static`. The tool scopes `users` and `projects` no longer exist (`read:memories` and `write:memories` remain). `.env.example` now sets `AUTH_MODE=none` and `SERVER_HOST=127.0.0.1`. The stdio and the HTTP examples in the README and the connectivity guide pass `AUTH_MODE=none` or a bearer token. The tool count goes from 17 to 10. The test harness server runs with `AUTH_MODE=none` on its loopback bind.
- **(planned)** Memory ids become UUIDs.
- **(planned)** Tests use the real GPU models and a real Qdrant; hosted CI keeps only the static gates.
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
- Container deployment: `docker/` (the Dockerfile and the SQLite, PostgreSQL and development compose files),
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
- **(planned)** FastEmbed, ONNX Runtime and the local CPU embedder.

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

- Authentication fails closed. A server with no `AUTH_MODE` refuses to start (exit code 1), `AUTH_MODE=none` is accepted only on stdio or on a loopback bind (`127.0.0.1`, `::1`; a hostname such as `localhost` does not count), and the credential modes are refused on stdio, which carries no bearer token. A request-level check refuses to serve when no provider is attached and the mode is not `none`. Fixes the finding that, without `FASTMCP_SERVER_AUTH`, every request was the same default user (`app/middleware/auth.py`), so any caller of a network-bound server read and changed every memory. Existing deployments that relied on that default must set `AUTH_MODE` (and bind to a loopback address if it is `none`) before the next start.
- Tenant isolation is tested on every access path: tenant A cannot read, update, delete, link, unlink, search or list tenant B's memories over the MCP tools, the REST routes and the CLI (local and remote), and another tenant's id answers exactly like an id that does not exist.
- Static tokens are stored as SHA-256 hashes only, compared in constant time, and never logged; the settings validator refuses a value that is not a 64-character hexadecimal digest without repeating it, so a token pasted by mistake is not echoed. A static token has no expiry: revoke it by removing its hash and restarting.
- PostgreSQL has no row-level security: there is no `CREATE POLICY` in `app/` or `alembic/`, isolation is a
  `user_id` predicate in each query, and a role that connects as the PostgreSQL bootstrap superuser (as the removed
  Docker deployment did) would bypass row-level security anyway. This goes away with the move to Qdrant, where isolation is a tenant
  filter applied in one place.
