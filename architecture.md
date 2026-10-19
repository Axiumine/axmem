# Forgetful architecture

Reviewable, agent-authored overview, checked against source on 2026-10-08, with boundary changes checked on 2026-10-17
and the structure re-verified on 2026-10-19 against the `quality-gates` branch (`dd1a371`).
This is a selective interpretation, not an exhaustive dependency inventory.
The component groupings below are inferred for readability; the described execution paths
and wiring were verified against source. Line numbers are inspection starting points and may drift.

Forgetful provides persistent, semantically searchable memories and related knowledge through
MCP tools, HTTP endpoints, and a CLI. Its intended boundary is
**routes → services → protocols → repositories/adapters**, as described in [AGENTS.md](AGENTS.md).
Protocols are Python contracts, not an extra runtime process: services call injected implementations.
SQL, vector storage, provider clients, and database sessions normally stay behind those contracts.
Operational exceptions are called out below rather than hidden by the diagram.

## Component view

```mermaid
flowchart TD
    MCP["MCP: three meta-tools"] --> Registry["ToolRegistry"]
    Local["Local CLI executor"] --> Registry
    Remote["Remote CLI executor"] --> MCP
    Registry --> ToolAdapters["Tool adapters: one module per domain, identity, models, results"]
    ToolAdapters --> Services["Domain services"]
    HTTP["HTTP custom routes"] --> Services
    Server["ForgetfulMCP: typed holder of services, registry, scope ceiling"]
    HTTP -->|reads services from| Server
    MCP -->|reads registry and scopes from| Server
    Server -->|holds| Services
    Services --> GraphHelpers["Graph helpers: app/services/graph"]
    Services --> Contracts["Repository protocols"]
    SQLite["SQLite repositories: memory repository composed of mixins"] -. implements .-> Contracts
    Postgres["PostgreSQL repositories: memory repository composed of mixins"] -. implements .-> Contracts
    SQLite --> SQLiteDB["SQLite adapter and sqlite-vec"]
    Postgres --> PostgresDB["PostgreSQL adapter and pgvector"]
    SQLite --> Providers["Embedding and rerank adapters"]
    Postgres --> Providers
    SQLite --> VectorDDL["vector_ddl: validated vector DDL"]
    Postgres --> VectorDDL
    Services --> Events["Optional EventBus"]
    Events --> Activity["ActivityService and memory access handler"]
    Activity --> Contracts
    Lifespan["main.lifespan"] -->|calls| Bootstrap["bootstrap.build_runtime"]
    Lifespan -->|attaches runtime to| Server
    Bootstrap -->|constructs and injects| Services
    Bootstrap -->|registers implementations| Registry
    Bootstrap -->|selects backend| SQLite
    Bootstrap -->|selects backend| Postgres
```

Boxes combine multiple classes/files; unless a label names a module or package, they are not declared
packages or separate deployments.
Solid arrows show source-verified use or explicitly labelled startup wiring. Dotted arrows mean
structural implementation of a contract, not inheritance or a request passing through a protocol.
Only memory/skill repositories use the embedding/rerank branch. Health/auth routes and operational
commands have additional paths omitted here; the diagram does not assert universal layering.

## Entry points and lifecycle

| Inspection point | Responsibility |
| --- | --- |
| [main.py:47](main.py#L47) | Server lifespan configures logging, calls `build_runtime`, attaches services/registry/scopes to the typed `ForgetfulMCP` instance ([server_state.py](app/routes/server_state.py)), then disposes the runtime on normal shutdown. Logging is deferred to protect STDIO transport. |
| [main.py:114](main.py#L114), [main.py:468](main.py#L468) | Construct the typed FastMCP subclass with configured authentication; `_serve` runs it over STDIO or HTTP. Both HTTP branches run the app under Uvicorn directly so lifespan teardown (`dispose_runtime`) completes before a shutdown signal is re-raised; the CORS branch additionally wraps the app with Starlette middleware. |
| [main.py:608](main.py#L608) | Console entry point `cli`: a known subcommand goes to `parser.dispatch`; any other argv falls through to the permanent legacy launcher (`--transport`, `--re-embed`, ...), which rejects the re-embed-only flags when `--re-embed` is absent. |
| [bootstrap.py:272](app/bootstrap.py#L272) | Composition root: choose providers and database, initialize schema, construct repositories, services, optional events, tool registry, and instance scope ceiling. |
| [bootstrap.py:241](app/bootstrap.py#L241) | `Services` and `Runtime` collect the instances for front doors; optional services are `None` when disabled. This is explicit constructor injection, not a DI container. |
| [bootstrap.py:425](app/bootstrap.py#L425) | Drain pending event handlers before closing the database; especially relevant to one-shot CLI execution. |
| [parser.py:273](app/routes/cli/parser.py#L273) | Dispatch CLI serve, re-embed, tool discovery/calls, memory/project verbs, and authentication commands. |

The bootstrap factories select PostgreSQL or SQLite repositories at
[bootstrap.py:116](app/bootstrap.py#L116), passing embedding and rerank adapters to memory/skill repositories.
`FILES_ENABLED`, `SKILLS_ENABLED`, and `PLANNING_ENABLED` control matching repositories, services,
metadata registration, and HTTP route registration. `GraphService` receives memory/entity repositories
plus the other services, including optional ones ([bootstrap.py:359](app/bootstrap.py#L359)).
Settings live in [settings.py:100](app/config/settings.py#L100); [canonical_choice:67](app/config/settings.py#L67) resolves provider and
log-format choices case-insensitively and raises on a typo instead of silently selecting a default.

The [local executor:18](app/routes/cli/local_executor.py#L18) builds the same runtime, supplies a
CLI context, checks the instance's permitted tools, and executes the registry in process.
The [remote executor:124](app/routes/cli/remote_executor.py#L124) calls the server's three meta-tools
through an MCP client; it does not build a second local database stack.
Both conform to [ToolExecutor:10](app/protocols/executor.py#L10).

## MCP, HTTP, and identity

The active MCP surface is registered by [meta_tools.py:494](app/routes/mcp/meta_tools.py#L494):

1. `discover_forgetful_tools` lists permitted tool metadata, optionally by category.
2. `how_to_use_forgetful_tool` returns detailed metadata for a permitted tool.
3. `execute_forgetful_tool` rejects argument names that are not identifiers
   ([line 454](app/routes/mcp/meta_tools.py#L454)), checks the name/permissions
   ([line 472](app/routes/mcp/meta_tools.py#L472)), injects context, and dispatches it.

Failures leave the meta-tools through [tool_errors.py:42](app/routes/mcp/tool_errors.py#L42): domain and
argument errors keep their authored message, while database and other unexpected errors are logged
server-side and reach the client only as a short generic sentence. The `linking` category of
`discover_forgetful_tools` is a tag filter across the memory, entity, and skill link tools.
Caller-supplied names are logged with `%r` and quoted with `!r` inside `ToolError` text (FastMCP
logs every failed tool call with a traceback that ends in that text); argument names are screened
first because CPython copies an unknown keyword verbatim into the `TypeError` that the logs and
traceback would carry.

The full operation set is dynamic: feature flags and scopes affect what a caller sees.
[tool_metadata_registry.py:910](app/routes/mcp/tool_metadata_registry.py#L910) constructs adapter
callables and registers metadata/implementations together. Despite its filename, this is a module
of registration helpers, not a second `ToolMetadataRegistry` class.
[ToolRegistry.execute:183](app/routes/mcp/tool_registry.py#L183) looks up and awaits the callable.
The adapters live per domain in [app/routes/mcp/adapters](app/routes/mcp/adapters) (memory, project,
entity, document, code artifact, file, skill, plan, task, user), with argument coercion shared in
[arguments.py](app/routes/mcp/adapters/arguments.py);
[tool_adapters.py](app/routes/mcp/tool_adapters.py) only re-exports them so the metadata registry
and the tests keep one import path.
[MemoryToolAdapters.create_memory:56](app/routes/mcp/adapters/memory.py#L56) illustrates identity
resolution, argument conversion, model construction, service delegation, and response formatting.
`main.py` mounts only `meta_tools` ([line 151](main.py#L151)); there are no per-category MCP tool
modules (the seven unmounted `*_tools.py` modules were removed), so every operation is reachable
only through the registry and these three meta-tools.

HTTP endpoints are FastMCP custom routes accepting Starlette requests, rather than a separate
FastAPI application despite the opening docstring in `main.py`.
The services, registry and scope ceiling that the lifespan attaches are declared once on
[ForgetfulMCP:41](app/routes/server_state.py#L41), a `FastMCP` subclass; routes, middleware and
`register(mcp)` take that type, and [server_of:67](app/routes/server_state.py#L67) narrows an MCP
request context to it (the in-process CLI passes a duck-typed shim carrying only the attributes adapters read).
[main.py:131](main.py#L131) mounts health, auth info, domain CRUD, graph, and activity routes;
files, skills, plans, and tasks are conditional. The root service-info route starts at
[main.py:117](main.py#L117). Route modules share three small helpers so bad client input answers
4xx instead of 500: [request_utils.py](app/routes/api/request_utils.py) (`read_json_object`, `path_id`,
`handle_request_errors`), [query_params.py](app/routes/api/query_params.py) (strict integer and
ISO 8601 query parsing) and [validation.py](app/routes/api/validation.py) (`validation_error_response`
renders pydantic errors as a safe 400). Examples:

| Surface | Source and path |
| --- | --- |
| Health / auth discovery | [health.py:19](app/routes/api/health.py#L19) `/health`; [auth.py:33](app/routes/api/auth.py#L33) `/api/v1/auth/info`. |
| Domain operations | [memories.py:172](app/routes/api/memories.py#L172) validates a create request and calls `MemoryService` directly; other domain routes follow this broad shape. |
| Graph exploration | [graph.py:85](app/routes/api/graph.py#L85) assembles a broad graph using domain services; [graph.py:1088](app/routes/api/graph.py#L1088) delegates bounded subgraph traversal to `GraphService`. |
| Activity history / streaming | [activity.py:48](app/routes/api/activity.py#L48) queries persisted events; [activity.py:210](app/routes/api/activity.py#L210) streams per-user events via SSE. |

[build_auth_provider:117](app/config/auth.py#L117) selects configured GitHub/Google OAuth, JWT,
or introspection providers, or no authentication. MCP identity comes from validated tokens or a
default user ([auth.py:169](app/middleware/auth.py#L169)); HTTP identity has its own bearer-token
verification/cache path ([auth.py:254](app/middleware/auth.py#L254)) whose cache entries never outlive
the token's own expiry. Services receive an internal user ID.
[scope_resolver.py:168](app/routes/mcp/scope_resolver.py#L168) intersects token permissions with
the instance ceiling for MCP. Token scopes come from the `scope`/`scp` claim; unrecognised scopes are
ignored, and a claim with scopes but none recognised permits nothing. Its empty-ceiling fallback
permits all registered tools; do not interpret an empty ceiling as deny-all. HTTP routes do not automatically inherit registry scope checks:
inspect their own authorization path before assuming parity. This overview is not a security audit.

## Memory write, retrieval, and linking

**Create:** route/tool adapter → `MemoryService.create_memory` → repository → embedding provider
and database. [memory_service.py:219](app/services/memory_service.py#L219) applies provenance
defaults, persists the memory, then optionally finds similar memories and creates links in a batch.
The repository builds embedding text and persists associations as part of its create operation:
[PostgreSQL:67](app/repositories/postgres/memory_repository.py#L67),
[SQLite:40](app/repositories/sqlite/memory_writes.py#L40).
Memory creation and subsequent automatic linking are separate repository calls, not one service-wide transaction.

Automatic linking uses the configured neighbor limit and similarity threshold, excluding obsolete
memories and the source memory ([PostgreSQL:155](app/repositories/postgres/memory_search.py#L155),
[SQLite:244](app/repositories/sqlite/memory_search.py#L244)). Explicit linking is a separate
service operation ([memory_service.py:559](app/services/memory_service.py#L559)); repository link
methods maintain the bidirectional memory relation and only link memories owned by the calling user. Obsolescence is a soft state with reason and
optional replacement; create responses can additionally warn about similar obsolete memories
([memory_service.py:474](app/services/memory_service.py#L474)).

**Query:** [memory_service.py:81](app/services/memory_service.py#L81) asks the repository for
scored primary results, optionally expands one-hop links, deduplicates, and applies token/count
budgets. Project restrictions apply to primaries; linked memories use the same project filter only
when `strict_project_filter` is enabled. Budget allocation prioritizes primary results before links
([memory_service.py:701](app/services/memory_service.py#L701)). Within each tier, the budget helper
sorts by importance ([memory_service.py:776](app/services/memory_service.py#L776)); the final response
is not an untouched vector-ranking list. Scores are returned for retained primaries.
Primaries and already-linked memories are excluded before each primary's link cap is applied, a request's
`token_context_threshold` can only lower `MEMORY_TOKEN_BUDGET`, and `truncated` reports drops by either the
token budget or `MEMORY_MAX_MEMORIES`. Token counting is lazy and shared, loads off the event loop, and falls
back to a character-based estimate when tiktoken's encoding is unavailable.

The repositories embed the query and search active, user-owned memories with optional importance
and project filters. PostgreSQL uses pgvector cosine distance
([memory_search.py:100](app/repositories/postgres/memory_search.py#L100)); SQLite joins
`memories` to `vec_memories` and computes cosine distance
([memory_search.py:22](app/repositories/sqlite/memory_search.py#L22)).
When reranking is enabled, they retrieve at least `max(k, DENSE_SEARCH_CANDIDATES)` candidates and
rerank when that set exceeds `k`; `query_context` enriches the rerank query, not the initial
embedding query ([PostgreSQL:43](app/repositories/postgres/memory_search.py#L43),
[SQLite:138](app/repositories/sqlite/memory_search.py#L138)).

## Contracts, storage, and implementation variants

| Boundary | Implementations / inspection points |
| --- | --- |
| Domain persistence | [MemoryRepository:21](app/protocols/memory_protocol.py#L21) and sibling files in [app/protocols](app/protocols) define service-facing contracts. Pydantic domain/request/result models live in [app/models](app/models), separate from backend ORM tables. Update models derive from [PatchModel](app/models/patch_models.py) (an explicit `null` means unchanged), and the entity-relationship, skill, plan, task and criterion request models derive from [StorableInput](app/models/storable.py), which refuses NaN, Infinity and NUL on both backends so the REST routes answer 400. |
| Memory repositories | [PostgresMemoryRepository:56](app/repositories/postgres/memory_repository.py#L56) keeps create/update/obsolete itself and composes search, link, listing, usage, subgraph and re-embedding mixins ([memory_search.py](app/repositories/postgres/memory_search.py), [memory_links.py](app/repositories/postgres/memory_links.py), [memory_listing.py](app/repositories/postgres/memory_listing.py), [memory_usage.py](app/repositories/postgres/memory_usage.py), [memory_subgraph.py](app/repositories/postgres/memory_subgraph.py), [memory_reembedding.py](app/repositories/postgres/memory_reembedding.py)) over [MemoryRepositoryBase:55](app/repositories/postgres/memory_base.py#L55); association writes are in [memory_associations.py](app/repositories/postgres/memory_associations.py) and the traversal SQL in [memory_graph_edges.py](app/repositories/postgres/memory_graph_edges.py). [SqliteMemoryRepository:18](app/repositories/sqlite/memory_repository.py#L18) composes search, write, link, re-embedding and subgraph mixins over [SqliteMemoryBase:7](app/repositories/sqlite/memory_base.py#L7), with reads in [memory_reads.py](app/repositories/sqlite/memory_reads.py), shared helpers in [memory_queries.py](app/repositories/sqlite/memory_queries.py) and the traversal SQL in [memory_subgraph_sql.py](app/repositories/sqlite/memory_subgraph_sql.py). The mixin split is structural; both classes still satisfy the one `MemoryRepository` contract. |
| PostgreSQL | [PostgresDatabaseAdapter:36](app/repositories/postgres/postgres_adapter.py#L36) owns async sessions, commit/rollback, initialization, and disposal. User sessions set `app.current_user_id`, but the schema defines no row-level-security policy, so isolation rests on the explicit `user_id` predicates in the repositories. Backend table definitions start at [postgres_tables.py:26](app/repositories/postgres/postgres_tables.py#L26). |
| SQLite | [SqliteDatabaseAdapter:54](app/repositories/sqlite/sqlite_adapter.py#L54) owns file/in-memory connections and sessions, loads sqlite-vec, and creates vector virtual tables. It creates a new data directory `0700` and keeps the database file (and its WAL/SHM) `0600`. Repository queries enforce user ownership. Relational tables start at [sqlite_tables.py:57](app/repositories/sqlite/sqlite_tables.py#L57). |
| Schema lifecycle | Both database adapters run Alembic in-process during `init_db` ([SQLite:157](app/repositories/sqlite/sqlite_adapter.py#L157), [PostgreSQL:85](app/repositories/postgres/postgres_adapter.py#L85)); concurrent first starts are serialised by a SQLite write transaction and a PostgreSQL advisory lock. Backend selection and migration execution live in [alembic/env.py:1](alembic/env.py#L1), with revisions in [alembic/versions](alembic/versions). Vector-storage initialization also has adapter-specific work; the vector DDL that cannot use bound parameters (SQLite `vec0` tables, the pgvector column resize) is built by [vector_ddl.py](app/repositories/vector_ddl.py), which accepts a plain identifier and a positive integer and refuses everything else. |
| Embeddings | [EmbeddingsAdapter:6](app/protocols/embedding_protocol.py#L6) defines `generate_embedding`. Implementations: local FastEmbed, Azure OpenAI, Google, OpenAI, and Ollama. Selection is explicit in [bootstrap.py:50](app/bootstrap.py#L50) and matches `EMBEDDING_PROVIDER` case-insensitively. |
| Reranking | [RerankAdapter:20](app/repositories/embeddings/reranker_adapter.py#L20) has local FastEmbed cross-encoder and HTTP implementations. [bootstrap.py:72](app/bootstrap.py#L72) selects one or disables reranking. |

The two persistence backends implement the same broad contracts but use different SQL/vector
mechanisms. This source review does not establish complete behavioral parity or transaction equivalence.
Provider availability, model cache contents, and live database policy configuration were not exercised.

## Other domain and operational components

These are explanatory groupings; they do not imply separate subsystems in the source tree.

| Group | Responsibilities and source anchors |
| --- | --- |
| Organization and entities | [ProjectService:33](app/services/project_service.py#L33) manages project metadata; [EntityService:67](app/services/entity_service.py#L67) manages named entities, aliases, typed entity relationships, and links to memories/projects. Memory project membership is represented by `project_ids` associations. |
| Attached knowledge | [DocumentService:40](app/services/document_service.py#L40), [CodeArtifactService:40](app/services/code_artifact_service.py#L40), and optional [FileService:40](app/services/file_service.py#L40) manage long text, source snippets, and binary content. They use separate repository contracts and can be associated with memories. |
| Procedural knowledge | Optional [SkillService:220](app/services/skill_service.py#L220) supports CRUD, semantic search, Markdown/frontmatter import/export, and links to memories/files/documents/code artifacts. Skill names are unique per user, enforced by the service and backed by a unique index. Skill repositories receive the same provider adapters as memory repositories. |
| Planning | Optional [PlanService:37](app/services/plan_service.py#L37) and [TaskService:67](app/services/task_service.py#L67) manage plans, task claims, criteria, and dependencies. [transition_task:293](app/services/task_service.py#L293) validates versions, state transitions, dependencies, and completion criteria (the repository repeats those checks inside its atomic UPDATE); task completion can complete the parent plan, and reopening a finished task returns a completed plan to active. Dependency edges are cycle-checked inside the inserting transaction. |
| Graph views | [GraphService:55](app/services/graph_service.py#L55) combines repository traversal with domain-service lookups. [get_subgraph:155](app/services/graph_service.py#L155) bounds depth/node count and asks `MemoryRepository.get_subgraph_nodes` for traversal results across supported object types, then hands the found ids to the [app/services/graph](app/services/graph) package: node-id parsing ([node_ids.py](app/services/graph/node_ids.py)), per-type object lookup and center validation ([lookup.py](app/services/graph/lookup.py)), traversal context ([traversal.py](app/services/graph/traversal.py)), node loading ([nodes.py](app/services/graph/nodes.py)), edge producers ([edges.py](app/services/graph/edges.py)) and metadata counts ([meta.py](app/services/graph/meta.py)). Services are described there by structural protocols ([protocols.py](app/services/graph/protocols.py)) rather than imported, and lookups pass `track_read=False` so rendering a graph is not logged as the user opening each object. |
| Activity and usage | Optional [EventBus:31](app/events/event_bus.py#L31) dispatches background handlers and per-user streams. [ActivityService:24](app/services/activity_service.py#L24) persists and queries events; it remains queryable when event emission is disabled. Memory read/query events optionally update access counters via [memory_service.py:834](app/services/memory_service.py#L834). |
| Re-embedding | [ReEmbeddingService:49](app/services/re_embedding_service.py#L49) coordinates full storage reset/batch embedding/validation (memories and, when given a skill repository, skills) and targeted rebuilds, using repository contracts and an embedding adapter. [main.py:430](main.py#L430) is the operational CLI path: a read-only dry run, a backup restored on failure or SIGINT/SIGTERM, and a skill repository built regardless of `SKILLS_ENABLED`. |
| Backup | [BackupService:9](app/services/backup_service.py#L9) supplies the timestamp and delegates through `BackupAdapter`; [DatabaseBackupAdapter:59](app/repositories/backup_adapter.py#L59) owns SQLite online-backup snapshots and PostgreSQL `pg_dump`/`psql` (owner-only files, optional `BACKUP_DIR`). [create_backup_service:42](app/bootstrap.py#L42) connects them. |

Startup constructs `ReEmbeddingService` and supplies it to MCP handlers; handlers call its
validation and rebuild methods without accessing `MemoryService.memory_repo`.
Embedding interfaces live in `app/protocols`; shared text preparation and owner-only file helpers live in `app/utils`.
User repositories translate duplicate external IDs to `ConflictError`, which `UserService`
handles by fetching the existing user after the failed transaction has rolled back.
Activity handling is asynchronous and not part of the domain write transaction; it is not a durable queue.

## Enforced import boundaries

The six contracts in `pyproject.toml` enforce these rules, including indirect imports:

| Source | Must not import |
| --- | --- |
| Models, protocols, events, utilities, settings, logging support, exceptions, version | Services, repositories, routes, bootstrap, authentication middleware/configuration |
| Services | Repositories, routes, bootstrap, authentication middleware/configuration |
| Repositories | Services, routes, bootstrap, authentication middleware/configuration |
| Shared code above and services | `sqlalchemy`, `openai`, `google`, `fastembed`, `httpx`, `ollama`, `subprocess`, `shutil` |
| API/MCP routes, the shared server-state type and authentication middleware | Repositories or bootstrap |
| CLI | Repositories or bootstrap, except the two startup imports below |

All Python files under `app` must be visible to the checker; a regression test verifies this.
Package markers are required, including for SQLite repositories, middleware, utilities, and the
`app/services/graph` and `app/routes/mcp/adapters` packages.
Bootstrap constructs implementations. The CLI has its own checked rule: only
`app.routes.cli.local_executor` and `app.routes.cli.context` may import bootstrap directly.
Other CLI modules may reach bootstrap through those startup imports, but may not import
repositories or bootstrap themselves. These exceptions belong only to the CLI rule;
API/MCP handlers still cannot reach bootstrap through CLI code.
Logging configuration may use `app.middleware.logging_middleware` as shared support.
Package initializers and generated version metadata are scanned; they are not extra application layers.
New source areas need an explicit decision about which rules apply.

`main.py` is a standalone startup/operational module, reviewed manually rather than scanned by
Import Linter; tests, migrations, and the evaluation harness are outside the production rules.
Static imports in functions, re-exports, and `TYPE_CHECKING` blocks are checked.
Dynamic imports, runtime attribute access, and arbitrary file/network operations are not checked;
review those in code review and cover behaviour with integration/E2E tests.
The external-library list is explicit, not a complete ban on every possible integration.

Run `uv run lint-imports --no-cache` for the rules alone, or
`uv run pytest tests/architecture/` for the rules, source coverage, and deliberate-violation proofs.
The CI test job runs the latter; [validation notes](docs/dev/architecture-tests.md) describe the proofs.

## Tests and validation entry points

| Suite | What to inspect / run |
| --- | --- |
| Integration | [tests/integration](tests/integration) uses stubbed I/O for service workflows. Start at [test_memory_service.py:16](tests/integration/test_memory_service.py#L16), [test_bootstrap.py](tests/integration/test_bootstrap.py), and the scope, CLI, task, skill, graph, and event-bus tests. Run `uv run pytest tests/integration/`. |
| SQLite E2E | [tests/e2e_sqlite](tests/e2e_sqlite) exercises the stack with in-memory SQLite; [conftest.py:158](tests/e2e_sqlite/conftest.py#L158) builds the app and controls optional features. Run `uv run pytest tests/e2e_sqlite/`. |
| PostgreSQL E2E | [tests/e2e](tests/e2e) exercises the PostgreSQL stack; [conftest.py:280](tests/e2e/conftest.py#L280) manages the Docker database fixture. Run `uv run pytest -m e2e` with Docker available. |
| Snapshots | Syrupy snapshots (`-m snapshot`) pin public surfaces: [test_snapshots.py](tests/integration/test_snapshots.py) covers CLI help, settings defaults and the database schema; [test_snapshots_sqlite.py](tests/e2e_sqlite/test_snapshots_sqlite.py) covers the MCP meta-tool signatures, the discovered tool catalogue and the HTTP route table. A changed snapshot is a contract change; refresh it deliberately with `uv run pytest -m snapshot --snapshot-update`. |
| Memory budgets | [test_memory_budget_sqlite.py](tests/e2e_sqlite/test_memory_budget_sqlite.py) holds `limit_memory` budgets for bulk create and query; they are enforced only under `--memray` (Linux/macOS) and otherwise run as smoke tests. |
| Agent walkthroughs | [test_harness/walkthrough.py:227](test_harness/walkthrough.py#L227) orchestrates agent sessions; this is evaluation infrastructure, not a production request path. Generated run workspaces are excluded from the map. |

Backend-sensitive changes should exercise both relevant E2E suites; contract/business-rule changes
should first use the focused integration tests. Source links identify tests, not evidence that those
suites have passed for any particular revision. Repository lint command: `uv tool run ruff check .`;
the full set of gates (types, security, complexity, 100% combined coverage, mutation) is described in
[docs/quality-gates.md](docs/quality-gates.md).

## Using the detailed map

The [Mycelium map](docs/assets/mycelium_class_diagram.md) is supporting detail, not required
reading in full. Do not load the entire file into context by default.
It is a **historical snapshot of an older revision**: it was last exported on 2026-10-08 and has not been
regenerated since (Mycelium is not part of the toolchain and no gate reads the map). Use this overview
and current source first. The snapshot predates the October 17 boundary changes (embedding/backup types
and rebuild wiring moved), and it shows none of the later restructuring: `GraphService` split into
`app/services/graph/*`, both memory repositories split into mixin modules, `tool_adapters.py` split
into `app/routes/mcp/adapters/*`, the typed `ForgetfulMCP` in `app/routes/server_state.py`, and the helper
modules added since. It also still lists the seven per-category `app/routes/mcp/*_tools.py` modules,
which were removed because they were never mounted. Line numbers cited by the map refer to its own revision.

- Find the relevant declaration in the source index, reading that index in chunks if needed.
- Search its diagram identifier for connections and read the relevant line ranges.
- Expand to callers, contracts, alternative implementations, and tests as the task requires.
- Check relevant warnings and filtering notices. Missing edges do not establish independence.
- Verify important relationships against source; static call edges can be uncertain.

Useful IDs in this snapshot, each describing its older single-module shape: `c0016` bootstrap,
`c0276` main, `c0240` ToolRegistry, `c0256` MemoryService, `c0120` MemoryRepository, `c0143` PostgreSQL
memory repository, `c0171` SQLite memory repository, `c0251` GraphService, and `c0264` TaskService.
The source index begins at [map line 4921](docs/assets/mycelium_class_diagram.md#L4921);
relationships at [5233](docs/assets/mycelium_class_diagram.md#L5233), warnings at
[8246](docs/assets/mycelium_class_diagram.md#L8246). IDs/positions can change on regeneration.

When it was generated, the map was freshly analyzed and exported using Mycelium 1.0.0 with
`--max-classes 1000 --test-path tests --test-path test_harness/runs`.
Repeating the export from the same map produced identical bytes. The output is nonempty and
contains one class diagram; it was checked as Markdown, without a rendered preview.
It contains 8,353 lines, 296 indexed boxes, 2,579 listed relationships,
and 106 extraction warnings. Test filtering removed 4,561 calls and 119 type relationships.
The box count is below the requested cap; full detail is not proof of complete extraction.

Warnings include unresolved or out-of-scope bases (`Protocol`, `BaseModel`, enums) and `EventBus`'s
`asyncio.Task` binding. These mostly name third-party/builtin types, but the diagnostics do not reliably
classify the origin of every unresolved reference. A concrete heuristic mismatch: the map attributes `TaskService.claim_task`
calling `_emit_event` to `PlanService`; source calls its own handler at
[task_service.py:420](app/services/task_service.py#L420), defined at [line 84](app/services/task_service.py#L84).
Runtime registration, decorators, and dynamic attributes require source inspection even when edges exist.

This overview is selective and does not replace source inspection. This map excludes `tests` and
`test_harness/runs`, but includes harness implementation and some debug scripts. Inspect test sources
or request a test-inclusive export when needed. Neither the overview nor the map establishes gap-free coverage.

## Maintenance

After **major code or architecture changes**, review the diagram and explanations and update them
if needed. Mycelium is optional; a source-verified manual update is acceptable. Preserve reviewed
explanations that remain accurate. Keep this overview at most 500 lines, with no column-width limit.
If the generated backup is refreshed, use fresh analysis, keep its JSON outside the repository,
and record exporter version/scope and material warnings, then update the snapshot date and the list of
known-stale shapes in "Using the detailed map". Recheck links and Mermaid rendering.
