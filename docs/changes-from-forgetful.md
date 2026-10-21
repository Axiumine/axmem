# Changes from Forgetful, and why

Axmem is maintained by Axiumine. It is derived from [Forgetful](https://github.com/ScottRBK/forgetful) by
Scott Raisbeck (MIT), forked at upstream commit `6d0199ed` (2026-10-18), and keeps its licence notice in
[LICENCE.md](../LICENCE.md). This page records every deliberate difference and the reason for it.
[CHANGELOG.md](../CHANGELOG.md) lists the same changes by section. Both are updated in the commit that changes
the behaviour (see the rule in [AGENTS.md](../AGENTS.md)).

Status values: **Done** (committed, with the commit), **Planned** (decided, not landed). A commit cannot name its
own hash, so the hash of a change is added by the next commit that updates this page.

## Why Axmem exists

Axmem is a standalone memory server: short free-form notes ("memories") with similarity links, obsolescence,
provenance and a token-budgeted query. It needs Qdrant, an NVIDIA GPU with the model files and an `AUTH_MODE`, and no
other service: remote embeddings, a remote rerank service and token providers are optional integrations that nothing
contacts unless they are selected. Forgetful features outside memory (plans and tasks, skills, files, documents,
entities, a graph view, an activity stream, user and project registries) were removed, because each is a job for a
tool built for it and a second copy inside a memory server would drift from the first.

An upstream-tracking fork cannot make these cuts, which is why Axmem is a separate project. Open question:
whether to keep merging upstream or to cherry-pick selected upstream features only. A trial merge of upstream
commits #97 to #100 (up to `f9d16368`) into this fork conflicted in 10 files and 19 hunks when measured on
2026-10-19, and the gap widens with every cut. Upstream has moved on since: its latest commit that day was
`dc7c13e1` (#101).

## Kept on purpose

Memories (create, query, update, recent), `mark_memory_obsolete` / `unobsolete_memory`, automatic links to the
three most similar memories (similarity 0.7 or more, both defaults), one-hop linked memories inside a token budget
(default 8,000 tokens), provenance on every object, the warning when a new memory resembles an obsolete one
(similarity 0.89 or more, at most three; it matches obsolete memories only, not active duplicates), and
cross-encoder reranking.

## Changes

| Area | Change | Why | Status |
|---|---|---|---|
| Identity | Renamed to Axmem under Axiumine; README rewritten, Forgetful credited, MIT notice kept | A separate product with its own direction | Done (`b121d350`) |
| Rename | Package, console script, CLI, environment variables (`AXMEM_SERVER`, `AXMEM_TOKEN`, `AXMEM_SCOPES`), config and data directories, MCP server name, the three meta-tools (`discover_axmem_tools`, `how_to_use_axmem_tool`, `execute_axmem_tool`), skills, semgrep rule ids, harness names, docs and snapshots all say `axmem`; the old names are not read; the upstream-authored `org_knowledge_proposal.md` and the upstream plugin section of the connectivity guide are removed; the architecture image of the upstream project is replaced by a Mermaid diagram | A product that shares a name with its upstream cannot be installed beside it, and a client config, an environment or a log line would not say which of the two it meant | Done (the commit titled "rename forgetful to axmem") |
| Python | 3.14 only; `compat` gate removed; ruff target py314 | One supported version removes the matrix and a redundant gate | Done (`1eea81a5`, `c78d3f98`) |
| Quality | Gate orchestrator, Git hooks, 100 % branch coverage threshold (proposed upstream as #163) | Every check runs the same way locally, in hooks and in CI | Done |
| Coverage data | `data_file` set to `.coverage-data/.coverage`, so the data file and its per-process fragments stay out of the repository root | `parallel = true` writes one `.coverage.*` file per measured process, and interrupted runs left them in the root | Done |
| Commits | `commit-msg` hook with `test`, `perf` and `build` types; AI attribution rejected only as trailers and "Generated with" lines | Naming Claude Code in docs is legitimate for an MCP project; the checks avoid `grep -P` so they run on any platform | Done (`95c1e5f1`) |
| Leak guard | `denylist-check` refuses commits, messages and pushes that match terms in the clone-local `.git/info/denylist` | Some names must never enter the repository, and a tracked list would name them | Done |
| Process | CHANGELOG.md, this page and an AGENTS.md rule to update both | A fork made of removals needs a record of what went and why | Done |
| Positioning | The README, this page, the changelog and the skills catalogue describe Axmem as a standalone memory server: what it needs, which integrations are optional, and what it does not do; the `axmem-remember` skill no longer routes content to a plan or task store | The reason given for each cut pointed at a system outside this repository, which described one deployment and not what the server needs; Axmem runs with Qdrant, a GPU and its model files alone | Done |
| Graph | Graph REST view, `GraphService` and subgraph SQL removed; memory links and auto-linking stay | A second open-ended graph next to the memory links would drift from them; the links are the one graph a memory server owns | Done (`6ce740e7`) |
| Planning | Plans and tasks removed; revision `20261019_drop_planning` | Planning belongs to a task tracker, not to a memory server | Done (`59f3f479`) |
| Skills | Skills removed, with import/export and `skill_ids` on memories; revision `20261019_drop_skills` | Skills are files that live with the agent that uses them | Done (`e77b50e0`) |
| Files | Files removed, with `file_ids` on memories; revision `20261019_drop_files` | Artefacts live in their own repository, which tracks their state; a memory holds a short note, not a file | Done (`c2cbf05a`) |
| Documents, code artifacts | Documents and code artifacts removed, with `document_ids` and `code_artifact_ids` on memories; revision `20261019_drop_docs_artifacts` | Documents and source code are ingested and indexed by tools built for them; a memory is one atomic note, not a document store | Done (`611e76d5`) |
| Entities | Entities and relationships removed; revision `20261019_drop_entities` | A second free-form graph next to the memory links would drift from them | Done (`5f7f1bd1`) |
| Activity | Event bus, activity log, SSE stream and the memory access counters removed; revision `20261020_drop_activity` | Metrics and tracing belong to the operator's observability stack, and nothing in Axmem reads the log or the counters | Done (`373e7eb5`) |
| Deployment | Container deployment removed: `docker/`, `.dockerignore`, the image build workflow and offline image test, the Nix OCI image and the self-hosting guide; the environment reference moves to the root `.env.example`; Docker stays for tests | Axmem is deployed natively on hosts with an NVIDIA GPU; the compose files still pulled the upstream image and no Axmem image was published | Done (`36a25494`) |
| Hardware | NVIDIA GPU required, no CPU path, no skip | One hardware target; the reranker and the embedder need the GPU to meet the latency budget | Done |
| Embeddings gateway | Optional `gateway` provider (`EMBEDDING_PROVIDER=gateway`) for an HTTP embeddings gateway: batches of up to 256, bounded retries with `Retry-After`, vector count and width checked against `EMBEDDING_DIMENSIONS`; `generate_embeddings` added to every provider and used by re-embedding. The Azure, Google, OpenAI and Ollama providers are unchanged, and no `GATEWAY_*` setting is read unless the provider is selected | A host that already runs an embeddings gateway can serve Axmem's vectors without a second model on the machine; checking the reply keeps a wrong-width vector out of the store | Done |
| Embeddings | In-process TensorRT by default (`BAAI/bge-small-en-v1.5`, strict fp32, 384 dimensions, width checked at start; the embedder and the reranker share one CUDA context); remote embeddings stay optional; FastEmbed, ONNX Runtime and the local CPU embedder removed (`EMBEDDING_PROVIDER=FastEmbed` is rejected at startup, `FASTEMBED_*` and `EMBEDDING_ONNX_PROVIDERS` are gone, `docs/OFFLINE_SETUP.md` moved into `docs/gpu-models.md`) | One GPU path with no network dependency: an Axmem host works with only the GPU, the model files, the tiktoken data and Qdrant, and the embeddings need no model service on the network; the CPU embedder was a second runtime next to the GPU one, and it pulled ONNX Runtime and seven other packages into the environment | Done |
| Reranking | Local TensorRT 11.x runner, strict fp32 (TF32 off, `RERANKING_TF32` opt-in), no fp16, no CPU path; fingerprinted engine cache, startup warm-up, startup refused with the cause when the GPU or libraries are unusable; FastEmbed cross-encoder, `RERANKING_THREADS`, `RERANKING_ONNX_PROVIDERS` and DirectML removed; `HTTP` provider unchanged | See "Decisions" below. On the reference GPU the engine matches the CPU fp32 scores within 1e-5, scores 20 documents in about 38 ms and loads from the cache in under a second | Done |
| Store | Qdrant only, with an API key and an optional three-node cluster profile; SQLite, PostgreSQL, pgvector, SQLAlchemy and Alembic removed; every request goes through one store module that adds the tenant condition | After the cuts only memories, links, a scope and a tenant key remain, so one store is enough; one chokepoint is what makes an instance-wide API key safe | Done |
| Ids | Memory ids become UUIDs (UUIDv7) in REST, MCP and the CLI; a number is refused | Qdrant has no autoincrement, and a client-chosen id must not collide across tenants | Done |
| Tenants | A tenant has no stored record: its id is the UUIDv5 of its name | A tenant store would be a second place to keep in step with the identity provider's, and the name in the credential already identifies it | Done |
| Concurrency | Edits and obsolete toggles are writes conditional on the version read, with a claim token read back; a lost race is retried up to 5 times, then 409 | Qdrant has no transactions; last-write-wins would silently drop a concurrent edit, and the tests prove that none is dropped, including on a three-node cluster | Done |
| Re-embedding | `--re-embed` fills a new collection and moves the live alias after validation; a failure or an interrupt discards the new collection; no backup or restore step | Qdrant cannot resize a vector field in place, and a copy leaves the live data untouched whatever happens | Done |
| Auth | JWT, introspection and static bearer tokens (hashes only, constant-time compare); GitHub and Google OAuth and the CLI browser login removed; fails closed: no `AUTH_MODE` means no start, and `AUTH_MODE=none` only on stdio or a loopback bind | Service deployments use tokens or OIDC; the default-user mode exposed every memory to every caller of a network-bound server | Done (`3201a2c3`) |
| Users, projects | Users become tenants (claim `tenant_id`, else `tenant`, else `sub`); projects become a scope string created on first use; user and project tools, routes and CLI verbs removed | Isolation is by tenant, taken from the credential; a project is a label on a memory, so there is no registry to keep | Done (`3201a2c3`) |
| Backup | `backup`, `restore`, `export`, `import` and `qdrant-check` on the CLI: snapshots of every node plus a JSONL export without vectors, refused when replicas disagree | The export can be re-embedded with any provider, which a snapshot cannot; a snapshot is the fast restore of the same cluster | Done |
| Tests | Real GPU models through the production adapters (no test-only embedder); a standalone end-to-end test with a network guard; real Qdrant in a Docker test fixture; contract tests of the gateway adapter against an in-process fake gateway | No model stubs, so similarity thresholds are tested for real; no storage stub, so Qdrant's filter, ordering and conditional-write semantics are tested rather than copied | Done for the reranker and the embedder (real engines, hard GPU preflight, no skip, no embedding stub on a happy path: similarity-dependent tests use clearly similar and clearly unrelated texts), the standalone test, and Qdrant (a real node in Docker for the integration and end-to-end suites, a three-node cluster suite behind the `e2e-qdrant` gate; the in-memory repositories are deleted), and the gateway adapter (an in-process fake that serves the request and response contract) |
| Review fixes | Start-up tolerates a silent Qdrant node and warns about ignored settings of earlier versions (the removed ONNX, store and feature settings by exact name, any case), and refuses to start on an old `FORGETFUL_SCOPES` that `AXMEM_SCOPES` does not replace; `AUTH_MODE=jwt` requires an issuer and an audience and a token needs an `exp`, introspection can check an audience and has one cache, and a mode with incomplete settings refuses only `serve` (the other commands run); the REST routes enforce `AXMEM_SCOPES` and the token scopes like the MCP tools, a body must be JSON (`415`), and a credential-free server refuses a foreign `Host` (`421`) or `Origin` (`403`); `import` creates missing collections and `export` fails clearly; quorum reads after writes; an offset past the end is an empty page; `GATEWAY_METADATA` parsed by the adapter; `EmbeddingUnavailableError` stops per-memory retries; fp32 enforced on the ONNX graph; NVML, manifest and cache failures are start-up errors; stderr logging during the warm-up; the Nix flake is Linux only; the README picture is a Mermaid diagram; the Qdrant image is pulled when it is missing; fork pull requests never reach the GPU runner; a `denylist-check` that fails closed for every line of the list, for an unreadable list and for a failing `git`, `grep` or `awk`; no test-only or unused package among the runtime dependencies; a setting that does not validate, a start-up refusal (Qdrant down, a refused API key, a differing layout, a bad scope ceiling, a provider without its key or package) and a failure of an operator command end in `Error: <cause>` with exit code 1 instead of a traceback; search texts, `source_files`, link calls, the obsolete reason, tool names and the request body are bounded (`413`); `/health` answers `503` once a TensorRT engine is unusable; a failed automatic linking no longer fails a stored create; `restore` validates its manifest and `export` and `import` resist a planted link and hostile lines; the log filter covers `extra` fields and no longer takes a minute on a hostile text; a `DELETE` whose body was cut off by the size limit marks nothing, a setting that is not a choice no longer prints its value, a manifest nested too deeply ends in one `Error:` line, a URL password with `/`, `?` or `#` is masked, and an empty `?` or `#` in `GATEWAY_URL` is refused; a cluster `restore` puts every node's snapshot back on its own node (one node's snapshot holds only its shards when the replication factor is below the node count), `re-embed` moves the memories and the links to a changed shard or replica layout, a start refuses vectors made by another embedder of the same width, an edit that was overtaken is recognised from a claim history of 16 versions (beyond it the outcome is reported unknown) and an edit across a re-embedding switch is made again on the new generation, the reads that decide a write or copy data away use quorum (a memory the replicas disagree about is awaited, then answered with a conflict (409 on every REST route), never reported missing, and so is the read-back of a write), a `re-embed` that stopped while it moved the links keeps the server, `import`, `backup` and `export` from running on links that are not there and the next `re-embed` finishes the move first, a snapshot is deleted from its node as soon as it is downloaded (one that cannot be deleted is named in the log), and `qdrant-check` tells unreadable nodes from a disagreement; a `tokenizer.json` that cannot be parsed and a failed GPU probe at any step are start-up refusals with a cause, a cached TensorRT engine that TensorRT refuses is replaced only after a new plan loads (a full GPU no longer deletes it, and a plan or fingerprint file that cannot be read is never taken for damage), a closed or failed engine raises `TensorRTUnavailableError` (an `EmbeddingUnavailableError`, so a rebuild stops retrying memory by memory, from the call that meets the CUDA error on), a failed reranker or embedder start releases its engine, the Azure, Google, OpenAI and Ollama adapters refuse a vector of another width than `EMBEDDING_DIMENSIONS` or with a number that is not finite, and `BACKUP_DIR` is documented as where `backup` writes (an `export` writes the file it is given) | A review of the cuts found places where a removed feature still left a silent failure, a stale instruction or an unchecked assumption | Done |
| CI | Static gates on hosted runners; tests on a self-hosted GPU runner, never for pull requests from forks | Hosted runners have no GPU, and fork code must not run next to the host's Docker daemon | Done (workflows; the runner itself is provisioned outside the repository) |

## Decisions and the evidence behind them

The measurements below were taken on 2026-10-19 on one machine (NVIDIA RTX PRO 4000 Blackwell). The scripts and raw
results are not in this repository.

**Reranking stays, and runs locally.** The test used the current defaults (the `bge-small-en-v1.5` embedder and the
`ms-marco-MiniLM-L-12-v2` cross-encoder) on 1,200 units cut from three sets of
technical documentation, with 130 ordinary queries (set A) and 44 hard ones (set B). Dense retrieval plus the cross-encoder
improved recall@5 and MRR@10 by at least 5 points on every slice, and the 95 % paired-bootstrap interval is above
zero on all of them (set A recall@5 .323 to .392, MRR .185 to .275; set B recall@5 .545 to .727, MRR .372 to .559).
Labels are weak (the target is the unit a query was written from), so trust the gap, not the absolute numbers.
Reranking runs in-process on the GPU, so no remote rerank service is needed.

**GPU and TensorRT.** The reranker takes about 1.7 s for a 20-document query on one CPU thread (0.44 s on eight) and
about 34 ms on ONNX Runtime's CUDA provider. TensorRT fp32 has the same latency as CUDA and uses about 0.9 to 1.2 GiB
of GPU memory. A direct TensorRT runner (87 lines in the spike) matches the ONNX Runtime TensorRT provider on speed
and accuracy, fails explicitly where ONNX Runtime silently falls back to CUDA when an input leaves the engine's
profile, and builds its engine in 8.4 s on TensorRT 11.4 against about 23 s through ONNX Runtime. TensorRT 10.x has no
Python 3.14 wheels (11.1 is the first with them), so the direct runner uses 11.x.

**Strict fp32, no fp16.** TF32 is on by default in TensorRT "fp32". Turning it off costs about 6 ms (about 40 ms per
query) and gives exact fp32 against the CPU reference (Kendall tau 1.0, maximum score difference 1e-5, against tau
0.998 to 0.999 and a difference of 0.004 to 0.005 with TF32 on). fp16 is not supported: TensorRT 11 builds strongly
typed networks, so fp16 would need a separately converted ONNX model.

**Qdrant only, with an API key.** A Qdrant API key is instance-wide (full or read-only), so tenant isolation is
enforced by Axmem: every query and write goes through one tenant filter. A lost Qdrant is a restore from snapshot or
from the JSONL export, the same class of risk as any single database without backups.

**Compare-and-set works on Qdrant.** On a single node of Qdrant 1.19.2, `set_payload` with a filter selector (an id
condition plus the expected `version` and `is_obsolete`) and `upsert` with `update_filter` and `update_mode=update_only`
are atomic: 50 concurrent writers, 640 rounds, exactly one winner each time. This decides how the obsolete toggle is
written. The conditions: pin the server to 1.19.0 or later and check it at startup; always pass `wait=True`; Qdrant
reports no applied/not-applied signal (a failed condition also returns `completed`), so write a unique token and read
it back; every writer of a guarded field, including the re-embedding job, must itself be conditional, because a plain
upsert silently overwrites a flipped value; filters match exact types, so a wrongly typed field is a silent no-op.
There is no multi-point or cross-collection atomicity, so memory creation and its links will be built from idempotent,
retryable steps (a link is its own point with a deterministic UUIDv5 id, written with `update_mode=insert_only`).
Qdrant does not publish an atomicity contract for conditional updates; the guarantee comes from its source and from
the test, so it is pinned to the tested version.

**Tests.** Integration and end-to-end suites use the production TensorRT embedder and reranker on the GPU (real
vectors and scores), shared through session fixtures; there is no test-only embedder. The gateway adapter's contract
tests use an in-process fake gateway that serves the request and response contract the adapter speaks.

## Facts to re-verify

- Compare-and-set was proved on one Qdrant node (the spike above, 50 writers, 640 rounds) and, in `tests/cluster`, on
  a three-node cluster (replication factor 3, write consistency factor 2, strong ordering, an API key and internal
  authentication) with writers spread over the nodes. A crash during a conditional write followed by WAL replay,
  `update_vectors` with `update_filter`, conditional writes during bulk ingestion (each takes an exclusive lock and
  waits for the update queue), more than three nodes and a replica that stays divergent after a node returns (the
  check reports it, nothing repairs it) are untested. Only Qdrant 1.19.2 was tested.
- A cluster restore was tested on three nodes for replication factors 3, 2 and 1, each over existing collections and over an emptied Qdrant (every node's snapshot uploaded to its
  own node with `priority=snapshot`): the data comes back whole and every replica becomes `Active`, but for a few
  seconds after the last upload some are `Dead` or `Recovering`, and how long the settling takes on a loaded host was not
  measured; at replication factor 2 uploading the next file before the replicas had settled left every replica of a shard `Dead` in two of four repeated restores, which is why `restore` now waits after each file (and uploads a file once more when a replica is still not `Active` after 90 seconds, a `ManualRecovery` replica that Qdrant leaves after a restore into an emptied cluster). A restore of one node's snapshot alone is whole only when every node holds every shard. Collection metadata
  (`axmem_embedder`) is checked to survive a snapshot upload and restore into an emptied Qdrant, on one node and on the three-node cluster, on 1.19.2 only. More than three nodes, and a
  restore into a cluster whose node set or order changed, are untested.
- Statements about upstream Forgetful were checked against this fork at `6d0199ed` and `f9d16368`; upstream has
  moved on since.
- The TensorRT embedder and reranker were measured on one GPU (RTX PRO 4000 Blackwell, driver 615.71, CUDA 13.4,
  compute capability 12.0) with TensorRT 11.4.0.106. Other GPUs, driver branches and TensorRT releases are untested; a
  bump is checked by the golden tests on a GPU host. The embedder's vectors were compared with the CPU fp32 model of
  the same files (about 2e-7 per component), not with the FastEmbed provider's output, because its model files were
  not available offline; a database filled by FastEmbed is migrated with `--re-embed`.
- The hosted CI jobs (static gates without `tensorrt-cu13-libs`, the wheel smoke test that installs the locked
  dependencies without those libraries and the wheel on top) were checked in a local environment, not on a GitHub-hosted runner, and the
  self-hosted GPU runner has not been provisioned or run against these workflows.
- The Nix flake (`flake.nix`, `docs/nix.md`) was not built or run with the TensorRT and CUDA wheels; Nix was not
  available when the reranker landed. The rename of its package, app and wrapper to `axmem`
  was not built either.
