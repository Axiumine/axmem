# Contributing to Axmem

This guide covers testing and CI workflows for contributors.

## Architecture

Before changing code, read the [architecture overview](../architecture.md).
The [detailed class map](assets/mycelium_class_diagram.md) is supporting material and a historical
snapshot of an older revision (last exported 2026-10-08, before the graph, repository and tool-adapter
restructuring); use the overview's advisories to read relevant sections rather than the whole file,
and trust the overview and the source where they differ.

Run `uv run pytest tests/architecture/` before submitting code changes; CI runs the same check.
It checks imports, verifies that every `app` module is scanned, and proves each rule rejects
forbidden dependencies; see [the rules and validation notes](dev/architecture-tests.md).

After major code changes, check whether the architecture diagram or explanations need updating.
Update them when the architecture has changed; small changes do not require regeneration.
Preserve reviewed explanations that remain accurate. Keep the overview at most 500 lines,
with no column-width limit, including Mermaid statements.

You do not need Mycelium to contribute or update the overview. If useful, the optional
[Mycelium architecture skill][mycelium-architecture] and [map-generation skill][mycelium-mermaid]
are published in [ScottRBK/mycelium][mycelium]. When refreshing the generated backup, run fresh
analysis, keep the JSON outside the repository, and export full detail to
`docs/assets/mycelium_class_diagram.md` with `--max-classes 1000`, `--test-path tests`, and
`--test-path test_harness/runs`. Verify nonempty diagrams and repeat-export consistency.

[mycelium]: https://github.com/ScottRBK/mycelium
[mycelium-architecture]:
  https://github.com/ScottRBK/mycelium/blob/master/skills/mycelium-architecture/SKILL.md
[mycelium-mermaid]:
  https://github.com/ScottRBK/mycelium/blob/master/skills/mycelium-mermaid/SKILL.md

## Quality gates and Git hooks

Install the toolchain and hooks once:

```bash
uv sync --locked --all-extras --dev
uv run python -m scripts.quality install     # sets core.hooksPath to .githooks
```

The `commit-msg` hook enforces the commit message format (`[🤖 ]<type>(<scope>): <subject>`); see the
Hooks table in [docs/quality-gates.md](quality-gates.md#hooks).

`pre-commit` checks staged files (lint, import boundaries, security, dead code, complexity, types,
snapshots, fast tests, GitNexus impact); `pre-push` runs the full pipeline: the same static gates over
the whole project, the architecture, integration and end-to-end suites with coverage (they start a real Qdrant
node in Docker), the three-node Qdrant cluster e2e (Docker), **100 % combined coverage**, memory budgets (memray),
trivy, Qodana and, only when asked for, mutation testing. CI runs the same gates on Python 3.14.
The Qodana token (`QODANA_TOKEN`) lives in your git-ignored dotenv file (see `.env.example` for the key
name), never in a tracked file. Python 3.14 is the only supported version (`.python-version`).

Everyday commands:

```bash
uv run python -m scripts.quality stage pre-commit            # what the commit hook runs
uv run python -m scripts.quality gate mypy                   # one gate, whole project
SKIP=qodana,trivy git push                                   # skip named gates once; say so in the PR
ONLY=mypy uv run python -m scripts.quality stage pre-commit  # only these gates
QUALITY_FULL=1 git commit                                    # pre-commit over the whole project
MUTATION=1 git push                                          # add mutation testing (changed app/ modules)
```

When a change alters a public surface (CLI flags, settings defaults, routes, MCP tools, collections, payload fields)
the syrupy snapshots fail on purpose: review the diff, then refresh them with
`uv run pytest -m snapshot --snapshot-update` and commit the snapshot files with the change. A new
vulture false positive goes into `vulture_whitelist.py` with a comment naming its consumer. Suppress a
bandit or semgrep finding inline with the reason on the same line (`# nosec B603 - argv list, no shell`,
`# nosemgrep: <rule-id> - reason`) and only for code that is safe by construction. The memory budgets
live in `tests/e2e/test_memory_budget_e2e.py` and are enforced under `--memray` (Linux/macOS).
All of this, including the gate list, scoping rules and the cost of the mutation gate, is in
[quality-gates.md](quality-gates.md).

## Running from source

Clone the repository and install the locked dependencies (Python 3.14 and [uv](https://docs.astral.sh/uv/)):

```bash
git clone https://github.com/Axiumine/axmem.git
cd axmem
uv sync --locked --all-extras --dev
AUTH_MODE=none uv run main.py   # the server refuses to start without AUTH_MODE, and needs a Qdrant node (QDRANT_URL)
```

**Optional**: Customize configuration by copying the example environment file:

```bash
cp .env.example .env
# Edit .env with your custom values; the example sets AUTH_MODE=none and SERVER_HOST=127.0.0.1
uv run main.py --transport http
```


## Testing Philosophy

We focus on **integration and E2E tests** over unit tests. Tests should cover critical workflows without exhaustive edge case coverage.

### Tests need an NVIDIA GPU

The integration, end-to-end and cluster suites run the real TensorRT embedder and reranker on the
GPU. There is no CPU path, no test-only embedder or reranker, no fake `tensorrt` module and no `gpu` marker
to deselect: a machine without a usable GPU cannot run these suites. Requirements are in [GPU models](gpu-models.md) (driver R580 or
newer, Linux); `uv sync` installs CUDA 13 and TensorRT 11.

Each suite begins with a hard preflight (`tests/gpu_preflight.py`, called from the suite's
`conftest.py` in `pytest_collection_modifyitems`). It ends the pytest run with exit code 2 and the cause, and
runs no test, when no NVIDIA GPU is visible, TensorRT or the CUDA bindings cannot be imported, or
`pytest -n N` asks for more workers than the GPU memory holds (pytest-xdist is not a project dependency; the
check only applies when you install it yourself). Each pytest process loads its
own engines and holds about 4.1 GiB of GPU memory (the embedder about 2.2 GiB and the reranker about 1.9 GiB
more, in one CUDA context; about 1.8 GiB more for each extra reranker worker), and the standalone end-to-end
test starts one more server process that loads the same pair, so `-n` is limited to what fits in 90 % of the
card. The suites share one engine of each model per process through the session fixtures `tensorrt_embedder`
and `tensorrt_reranker` in `tests/conftest.py`, so the engines are loaded once per run (under a second each
from the cache, 6 to 9 s when one has to be built). The fixtures return the production adapters with
production defaults (`get_embedding_adapter()`, `get_reranker_adapter()`), so a test that needs real vectors
depends on `tensorrt_embedder` and sets `EMBEDDING_PROVIDER = "TensorRT"`.

What stays a test double, and why:

- HTTP mock transports, SDK clients pointed at in-process fake endpoints and the in-process fake gateway,
  for the remote embeddings adapters (Azure, Google, OpenAI, Ollama, gateway) and `HttpRerankAdapter`: the
  code under test is the client, and a test cannot reach those services.
- Fake adapters in `tests/integration/test_startup_config_wiring.py`: they record which provider
  `get_embedding_adapter` builds for a setting; no vector is involved.
- Fault injection around the real embedder, in `tests/embedding_doubles.py`: `spy_on` wraps it in call
  recorders and a test assigns `side_effect` to make one call fail; `failing_on` fails the n-th call;
  `DelegatingEmbedder` subclasses add latency, a gate, a hook that changes the memory during the call or a
  vector of the wrong width, and still return the real vector; `MigratingEmbedder` changes the vector on
  purpose and fails or stalls at a chosen call inside a child process. The real model never fails or stalls
  on cue, so these cannot be real.
- `NarrowedEmbedder`: the model has one width (384), so tests that change `EMBEDDING_DIMENSIONS` cut the real
  vector to the width under test and scale it back to length 1.
- One malformed batch, a provider that returns one vector too few (the service must fall back to single calls).
- Nothing stands in for storage: the repository under test is the production Qdrant one, on a real node (see
  "Qdrant for the tests" below), because an in-memory stand-in would repeat Qdrant's filter, ordering and
  conditional-write semantics and could pass where the real thing fails.
- Fault injection around the real Qdrant client, in `tests/integration/test_qdrant_cas.py` and `test_qdrant_pool.py`: a client that
  raises what the library raises on a reset connection or a 5xx answer, before or after the write was applied. A real
  node does not fail on cue.
- Recorders and spies that delegate to the real reranker (to observe the candidates a search sends it);
  a barrier around `TensorRTEngine.infer` (to force two inferences to overlap); a module finder that hides
  `tensorrt` or `cuda` in a subprocess (to prove the server refuses to start rather than falling back); and
  one deliberately invalid `cudaMemcpy` (to prove a failed CUDA call is reported and cleared).

Everything else about the embedder and the reranker is the real engine on the real GPU. A test that needs
vectors uses the `tensorrt_embedder` fixture; a test that mocks the repository takes `embedding_spy`.

Real vectors are not equal for equal meaning, so tests that depend on similarity use texts that are clearly
similar or clearly unrelated instead of identical vectors. The texts are in `tests/embedding_texts.py`; the
cosine values below were measured with the production embedder, and
`tests/integration/test_embedding_texts.py` pins them (tolerance 0.03), so a model or numerics change fails
there first.

| Pair | Cosine | Relied on by |
|---|---|---|
| Sourdough feeding notes, other number in the title | 0.99 | duplicates above the 0.89 obsolete-warning threshold |
| Sourdough feeding and sourdough baking | 0.83 | links above the 0.7 auto-link cutoff, no obsolete warning |
| Sourdough feeding and Postgres pool sizing | 0.46 | unrelated, below the cutoff |
| Sourdough baking and Postgres pool sizing | 0.37 | unrelated, below the cutoff |
| Feeding query and the feeding notes, the baking note | 0.88, 0.78 | the query finds the feeding notes first |
| Backup seeds "Original memory/content 0" and "1", "2" | 0.946, 0.932 | the obsolete warning fires in the backup-restore checks |
| Backup seed and the same text with the "migrated: " prefix | 0.933 | the changed-provider embedder differs from the original |
| "Alice private secret" and "Bob own note" memories | 0.77 | cross-user reference checks link above the cutoff, asserted before the test |

The embedder tests (`tests/integration/test_tensorrt_embedder.py`) compare vectors with goldens computed
once from the CPU fp32 model, check unit length, batching and chunking at 63, 64, 65 and 133 texts,
truncation at 512 tokens, concurrency and cancellation, and that the embedder and the reranker report the same
CUDA context. `tests/e2e/test_standalone_e2e.py` starts the real server in a subprocess with a
static token, no `GATEWAY_*` variable, `HF_HUB_OFFLINE=1` and a guard that refuses and records every
connection that is not loopback (the test's own Qdrant node is on loopback), then runs create, auto-link, a reranked query, link and mark-obsolete; it
fails if the guard recorded anything.

### Qdrant for the tests

Memories live in Qdrant, so the suites need a real server (version 1.19.0 or newer; the tests pin `qdrant/qdrant:v1.19.2`).
`tests/qdrant_support.py` starts it through Docker on a random loopback port with an API key, once per pytest process, and
removes the container at the end. The preflight pulls the image (`qdrant/qdrant:v1.19.2`) when it is not local; a run that
cannot start it (no Docker, an unreachable registry) ends with exit code 2 and the cause, it never skips. `AXMEM_TEST_QDRANT_URL` (with `AXMEM_TEST_QDRANT_API_KEY`) points the suites at
a server that already runs instead; the tests then work in collections under a prefix of their own and delete them afterwards.

Tests share the node's collections and stay apart by tenant: the `qdrant_env` fixture gives every test a tenant namespace of
its own, so the same tenant name ("alice", `DEFAULT_TENANT`) is a different tenant in every test. A test that needs collections
of its own (re-embedding, snapshots, the layout, anything that counts all memories or switches the live generation) uses
`isolated_qdrant` or `isolated_stack`. Creating the collections costs about 5 seconds (eight payload indexes), which is why the
shared ones exist; keep the isolated tests few.

### Integration Tests

**Location**: `tests/integration/`

**Purpose**: Test business logic and the Qdrant repository on a real node (the embedder and the reranker are real; only HTTP I/O
is stubbed)

**Run locally**:
```bash
uv run pytest tests/integration/
```

These tests run the production repository on real Qdrant and the real embedder and reranker, and form the bulk of our test
suite. Four modules protect the storage design and are worth reading first:

- `test_tenant_chokepoint.py`: every `MemoryRepository` method is run behind a recording Qdrant client, and every read must carry
  the acting tenant's condition, every query must be by vector, and every write must stamp the tenant or select by it. A new
  protocol method fails the test until it has a scenario. The detector is tested against a repository that forgets the tenant.
- `test_qdrant_cas.py`: overlapping writers never lose one another's change, a toggle several writers race for has one winner, and a
  write whose answer is lost takes effect once, a write that was overtaken is still counted as applied (and one overtaken beyond
  the claim history reports an unknown outcome), and an edit that straddles a re-embedding switch is made again on the new
  generation. The detector is tested against a check-then-write updater that must be reported as losing changes.
- `test_qdrant_read_consistency.py`: the reads that decide a write or copy data away ask a majority of the replicas, and replicas that disagree about a memory are awaited, then answered with a conflict (never "missing"); the same goes for the read-back of a write, which is neither replayed nor reported lost because the replicas disagree about the point it wrote.
- `test_qdrant_store.py`: the scopes, create-if-absent, the guarded writes and the read-back of claim tokens.
- `test_qdrant_schema.py` and `test_qdrant_pool.py`: the collection layout and embedder checks that stop a start, the generations
  and the live alias, the failover between nodes and the retry of ambiguous failures. `test_qdrant_links_rebuild.py` stops the move
  of the links collection to a new layout at each of its steps (`tests/relayout_support.py` leaves the node at the two that matter), finishes it, and
  checks that nothing starts or copies the links away while they wait in the staging collection.

### End-to-End Tests

#### Single-node E2E Tests

**Location**: `tests/e2e/`

**Purpose**: Test the complete stack (REST, MCP, CLI, authentication, the real server in a subprocess) on a real Qdrant node

**Requirements**: an NVIDIA GPU (see above) and Docker for the Qdrant node

**Run locally**:
```bash
uv run pytest tests/e2e/
```

**CI Status**: Runs automatically in the CI and E2E workflows on every push/PR, on the GPU runner

The suite is large (several thousand tests across the integration and e2e directories), so run a single file or `-k` selection
while iterating and the whole directory before pushing.

#### Cluster E2E Tests

**Location**: `tests/cluster/` (marker `cluster`; the default `pytest` options exclude them with `-m "not cluster"`)

**Purpose**: Test the concurrency design on a real three-node Qdrant cluster: replication factor 3, write consistency factor 2,
strong write ordering, an API key, internal authentication between the nodes. The backup and restore round trip also runs
with replication factors 2 and 1, where a node's snapshot holds only some of the shards

**Run locally**:
```bash
uv run pytest tests/cluster -m cluster
```

The fixtures start the three containers and a network, wait for the cluster to form, and remove everything at the end. The
`e2e-qdrant` gate runs this suite and appends its coverage, so the `coverage` gate cannot pass on a machine without Docker if
code only the cluster run reaches is left untested.

#### Backup and recovery tests

These tests use the real Qdrant node. They check that a snapshot backup restores memories, metadata, links, obsolete state and
searchable vectors, that an export imports into an empty instance and can be imported twice, and that `axmem re-embed` leaves the
live generation untouched when the embedding provider fails after a saved batch or during the final search check.

The embedding provider is the real TensorRT embedder; a wrapper around it makes the provider fail on cue in the child process.

```bash
uv run pytest tests/e2e/test_backup_restore_e2e.py -q --durations=5
```

A push to a branch with an open PR starts both a push run and a PR run; newer runs cancel older runs for the same workflow and ref.

---
## Linting
linting with ruff and uv (the `ruff` gate runs the same check)
```bash
uv tool run ruff check .
```

## CI/CD Workflows

### `ci.yml` - Continuous Integration

**Triggers**: Every push and pull request

**Purpose**: Fast feedback for contributors

**Matrix**: Python 3.14.

**Jobs**:
1. `lint` (hosted Ubuntu runner, no GPU): check architecture boundaries and rule proofs, then run linting. The
   TensorRT libraries are left out of its environment (`--no-install-package tensorrt-cu13-libs`).
2. `test` (self-hosted GPU runner, `runs-on: [self-hosted, linux, gpu]`): run integration tests (real Qdrant,
   real embedder and reranker) and E2E tests (full stack on real Qdrant).
3. `smoke-test` (hosted runner): build the wheel, install the locked dependencies without the TensorRT libraries and the
   wheel on top, and check that `axmem --help` works.

The GPU runner is a machine you provide and register with those labels; see
[quality-gates.md](quality-gates.md#pipeline) for what it needs. The Windows leg was removed because the
TensorRT reranker is Linux-only.

**Pull requests from forks**: `test` is skipped for them, so that fork code never runs on the self-hosted runner
(see [quality-gates.md](quality-gates.md#pipeline)); the lint and smoke-test jobs still run. A maintainer who has reviewed
the change pushes its branch to this repository to run `test`.

**Merge requirements**: Configure branch rules to require this workflow's `test` check.
The workflow alone does not make it mandatory for merging.

**Test Coverage**: Integration tests validate business logic and the Qdrant repository with the real embedder and reranker. E2E tests validate the complete application stack. Both need Docker for their Qdrant node.

### `e2e.yml` - End-to-End Validation

**Triggers**: Every branch push, pull request, and manual workflow dispatch

**Purpose**: Validate the full stack with a real Qdrant node

**Matrix**: Python 3.14, on the self-hosted GPU runner (`runs-on: [self-hosted, linux, gpu]`).

**Steps**:
1. Check the Docker daemon (the suites start the Qdrant node).
2. Run the E2E tests (`tests/e2e/`), including backup recovery.

**Merge requirements**: Configure the repository's branch rules to require the `e2e` check.
The workflow alone does not make a check mandatory for merging.

**Test Coverage**: Validates the full application stack on Qdrant, including snapshot backup and restore.

### `quality.yml` - Quality Gates

**Triggers**: Every push and pull request, a weekly schedule (mutation testing) and manual dispatch

**Purpose**: Run the same gates as the local Git hooks (`scripts/quality`) so a bypassed hook is caught

**Jobs**: `static` (lint, import boundaries, security, dead code, complexity, types and pylint on a hosted
runner, on Python 3.14), then on the self-hosted GPU runner `snapshots`, `coverage` (single-node and cluster
suites combined, 100 % required), `memory` (memray budgets) and `mutation` (weekly and manual only); `trivy` and `qodana` (uses the
`QODANA_TOKEN` repository secret; skipped for pull requests from forks) run on hosted runners. See
[quality-gates.md](quality-gates.md).

### `publish.yml` - PyPI Publication

**Triggers**: A published GitHub release

**Purpose**: Build the package and publish it to PyPI

---

## Creating a Release

1. Commit your changes to `main` branch
2. Create and push a version tag:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
3. Publish a GitHub release for the tag; `publish.yml` then builds the package and publishes it to PyPI
