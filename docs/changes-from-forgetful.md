# Changes from Forgetful, and why

Axmem is maintained by Axiumine. It is derived from [Forgetful](https://github.com/ScottRBK/forgetful) by
Scott Raisbeck (MIT), forked at upstream commit `6d0199ed` (2026-10-18), and keeps its licence notice in
[LICENCE.md](../LICENCE.md). This page records every deliberate difference and the reason for it.
[CHANGELOG.md](../CHANGELOG.md) lists the same changes by section. Both are updated in the commit that changes
the behaviour (see the rule in [AGENTS.md](../AGENTS.md)).

Status values: **Done** (committed, with the commit), **Planned** (decided, not landed). A commit cannot name its
own hash, so the hash of a change is added by the next commit that updates this page.

## Why Axmem exists

Axmem is a focused memory server: short free-form notes ("memories") with similarity links, obsolescence,
provenance and a token-budgeted query. Forgetful features outside that scope were removed, because a host system
that needs them already provides them and a second copy would drift from the first.

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
| Identity | Renamed to Axmem under Axiumine; README rewritten, Forgetful credited, MIT notice kept | A separate product with its own direction | Done (`b121d350`); rename of the package, CLI and environment variables Planned |
| Python | 3.14 only; `compat` gate removed; ruff target py314 | One supported version removes the matrix and a redundant gate | Done (`1eea81a5`, `c78d3f98`) |
| Quality | Gate orchestrator, Git hooks, 100 % branch coverage threshold (proposed upstream as #163) | Every check runs the same way locally, in hooks and in CI | Done |
| Coverage data | `data_file` set to `.coverage-data/.coverage`, so the data file and its per-process fragments stay out of the repository root | `parallel = true` writes one `.coverage.*` file per measured process, and interrupted runs left them in the root | Done |
| Commits | `commit-msg` hook with `test`, `perf` and `build` types; AI attribution rejected only as trailers and "Generated with" lines | Naming Claude Code in docs is legitimate for an MCP project; the checks avoid `grep -P` so they run on any platform | Done (`95c1e5f1`) |
| Leak guard | `denylist-check` refuses commits, messages and pushes that match terms in the clone-local `.git/info/denylist` | Some names must never enter the repository, and a tracked list would name them | Done |
| Process | CHANGELOG.md, this page and an AGENTS.md rule to update both | A fork made of removals needs a record of what went and why | Done |
| Graph | Graph REST view, `GraphService` and subgraph SQL removed; memory links and auto-linking stay | A second open-ended graph next to the memory links drifts from any graph a host system keeps | Done (`6ce740e7`) |
| Planning | Plans and tasks removed; revision `20261019_drop_planning` | Planning belongs to the host system's tracker, not to a memory server | Done (`59f3f479`) |
| Skills | Skills removed, with import/export and `skill_ids` on memories; revision `20261019_drop_skills` | Skills are files a host system already keeps | Done (`e77b50e0`) |
| Files | Files removed, with `file_ids` on memories; revision `20261019_drop_files` | A host system keeps its artefacts in its own repository and tracks their state itself | Done (`c2cbf05a`) |
| Documents, code artifacts | Documents and code artifacts removed, with `document_ids` and `code_artifact_ids` on memories; revision `20261019_drop_docs_artifacts` | The knowledge layer ingests ADRs, epics and stories with chunking; GitNexus indexes code | Done (`611e76d5`) |
| Entities | Entities and relationships removed; revision `20261019_drop_entities` | A second free-form graph would drift from the Neo4j one | Done (`5f7f1bd1`) |
| Activity | Event bus, activity log, SSE stream and the memory access counters removed; revision `20261020_drop_activity` | Metrics and tracing belong to the host's observability stack, and nothing in Axmem reads the log or the counters | Done (`373e7eb5`) |
| Deployment | Container deployment removed: `docker/`, `.dockerignore`, the image build workflow and offline image test, the Nix OCI image and the self-hosting guide; the environment reference moves to the root `.env.example`; Docker stays for tests | Axmem is deployed natively on hosts with an NVIDIA GPU; the compose files still pulled the upstream image and no Axmem image was published | Done (`36a25494`) |
| Hardware | NVIDIA GPU required, no CPU path, no skip | One hardware target; the reranker and the embedder need the GPU to meet the latency budget | Done for the reranker, the default embedder and the tests; the FastEmbed CPU provider stays selectable until its removal (Planned) |
| Embeddings gateway | Optional `gateway` provider (`EMBEDDING_PROVIDER=gateway`) for an HTTP embeddings gateway: batches of up to 256, bounded retries with `Retry-After`, vector count and width checked against `EMBEDDING_DIMENSIONS`; `generate_embeddings` added to every provider and used by re-embedding. The Azure, Google, OpenAI and Ollama providers are unchanged, and no `GATEWAY_*` setting is read unless the provider is selected | A host that already runs an embeddings gateway can serve Axmem's vectors without a second model on the machine; checking the reply keeps a wrong-width vector out of the store | Done |
| Embeddings | In-process TensorRT by default (`BAAI/bge-small-en-v1.5`, strict fp32, 384 dimensions, width checked at start; the embedder and the reranker share one CUDA context); remote embeddings stay optional | One GPU path with no network dependency: an Axmem host works with only the GPU, the model files and a database, and the embeddings no longer need the router | Done for the TensorRT provider; removal of FastEmbed, ONNX Runtime and the local CPU embedder Planned |
| Reranking | Local TensorRT 11.x runner, strict fp32 (TF32 off, `RERANKING_TF32` opt-in), no fp16, no CPU path; fingerprinted engine cache, startup warm-up, startup refused with the cause when the GPU or libraries are unusable; FastEmbed cross-encoder, `RERANKING_THREADS`, `RERANKING_ONNX_PROVIDERS` and DirectML removed; `HTTP` provider unchanged | See "Decisions" below. On the reference GPU the engine matches the CPU fp32 scores within 1e-5, scores 20 documents in about 38 ms and loads from the cache in under a second | Done |
| Store | Qdrant only; SQLite, PostgreSQL, pgvector and Alembic removed | After the cuts only memories, links, a scope and a tenant key remain, so one store is enough | Planned |
| Ids | Memory ids become UUIDs | Qdrant has no autoincrement | Planned |
| Auth | JWT, introspection and static bearer tokens (hashes only, constant-time compare); GitHub and Google OAuth and the CLI browser login removed; fails closed: no `AUTH_MODE` means no start, and `AUTH_MODE=none` only on stdio or a loopback bind | Service deployments use tokens or OIDC; the default-user mode exposed every memory to every caller of a network-bound server | Done (`3201a2c3`) |
| Users, projects | Users become tenants (claim `tenant_id`, else `tenant`, else `sub`); projects become a scope string created on first use; user and project tools, routes and CLI verbs removed | A host system isolates by tenant and owns its project registry | Done (`3201a2c3`) |
| Backup | Qdrant snapshots plus a JSONL export without vectors | The export can be re-embedded through the router | Planned |
| Tests | Real GPU models through the production adapters (no test-only embedder); a standalone end-to-end test with a network guard; real Qdrant in a Docker test fixture; contract tests against a fake router | No model stubs, so similarity thresholds are tested for real | Done for the reranker and the embedder (real engines, hard GPU preflight, no skip) and the standalone test (on SQLite); Planned for Qdrant and the router contract tests |
| CI | Static gates on hosted runners; tests on a self-hosted GPU runner | Hosted runners have no GPU | Done (workflows; the runner itself is provisioned outside the repository) |

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
tests use an in-process fake router built from the router schema.

## Facts to re-verify

- Compare-and-set was tested on one Qdrant node only. Clusters and replicas, a crash during a conditional write
  followed by WAL replay, `update_vectors` with `update_filter`, conditional writes during bulk ingestion (each takes
  an exclusive lock and waits for the update queue) and runs with an API key are untested.
- Statements about upstream Forgetful were checked against this fork at `6d0199ed` and `f9d16368`; upstream has
  moved on since.
- The TensorRT embedder and reranker were measured on one GPU (RTX PRO 4000 Blackwell, driver 615.71, CUDA 13.4,
  compute capability 12.0) with TensorRT 11.4.0.106. Other GPUs, driver branches and TensorRT releases are untested; a
  bump is checked by the golden tests on a GPU host. The embedder's vectors were compared with the CPU fp32 model of
  the same files (about 2e-7 per component), not with the FastEmbed provider's output, because its model files were
  not available offline; a database filled by FastEmbed is migrated with `--re-embed`.
- The hosted CI jobs (static gates without `tensorrt-cu13-libs`, the wheel smoke test that downloads the TensorRT
  libraries through the PyPI stub package) were checked in a local environment, not on a GitHub-hosted runner, and the
  self-hosted GPU runner has not been provisioned or run against these workflows.
- The Nix flake (`flake.nix`, `docs/nix.md`) was not built or run with the TensorRT and CUDA wheels; Nix was not
  available when the reranker landed.
