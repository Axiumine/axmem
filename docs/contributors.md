# Contributing to Forgetful

This guide covers testing and deployment workflows for contributors.

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
the whole project, the architecture, integration and SQLite e2e suites with coverage, PostgreSQL e2e
(needs Docker), **100 % combined coverage**, memory budgets (memray),
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

When a change alters a public surface (CLI flags, settings defaults, routes, MCP tools, tables, migrations)
the syrupy snapshots fail on purpose: review the diff, then refresh them with
`uv run pytest -m snapshot --snapshot-update` and commit the snapshot files with the change. A new
vulture false positive goes into `vulture_whitelist.py` with a comment naming its consumer. Suppress a
bandit or semgrep finding inline with the reason on the same line (`# nosec B603 - argv list, no shell`,
`# nosemgrep: <rule-id> - reason`) and only for code that is safe by construction. The memory budgets
live in `tests/e2e_sqlite/test_memory_budget_sqlite.py` and are enforced under `--memray` (Linux/macOS).
All of this, including the gate list, scoping rules and the cost of the mutation gate, is in
[quality-gates.md](quality-gates.md).

## Running from source

Clone the repository and install the locked dependencies (Python 3.14 and [uv](https://docs.astral.sh/uv/)):

```bash
git clone https://github.com/ScottRBK/forgetful.git
cd forgetful
uv sync --locked --all-extras --dev
AUTH_MODE=none uv run main.py   # the server refuses to start without AUTH_MODE
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

The integration, SQLite e2e and PostgreSQL e2e suites run the real TensorRT reranker on the GPU. There is
no CPU path, no stub reranker, no fake `tensorrt` module and no `gpu` marker to deselect: a machine without
a usable GPU cannot run these suites. Requirements are in [GPU reranking](gpu-reranking.md) (driver R580 or
newer, Linux); `uv sync` installs CUDA 13 and TensorRT 11.

Each of the three suites begins with a hard preflight (`tests/gpu_preflight.py`, called from the suite's
`conftest.py` in `pytest_collection_modifyitems`). It ends the pytest run with exit code 2 and the cause, and
runs no test, when no NVIDIA GPU is visible, TensorRT or the CUDA bindings cannot be imported, or
`pytest -n N` asks for more workers than the GPU memory holds (pytest-xdist is not a project dependency; the
check only applies when you install it yourself). Each pytest process loads its
own engine and holds about 2.2 GiB of GPU memory (about 1.8 GiB more for each extra reranker worker), so
`-n` is limited to what fits in 90 % of the card. The suites share one reranker engine per process through
the session fixture `tensorrt_reranker` in `tests/conftest.py`, so the engine is loaded once per run (about
0.3 s from the cache, 5 to 7 s when it has to be built).

What stays a test double, and why: HTTP mock transports and loopback HTTP servers for `HttpRerankAdapter`
(the code under test is the HTTP client); stub embedding adapters (no embedding model is the subject);
recorders and spies that delegate to the real reranker (to observe the candidates a search sends it);
a barrier around `TensorRTEngine.infer` (to force two inferences to overlap); a module finder that hides
`tensorrt` or `cuda` in a subprocess (to prove the server refuses to start rather than falling back); and
one deliberately invalid `cudaMemcpy` (to prove a failed CUDA call is reported and cleared). Everything
else about the reranker is the real engine on the real GPU.

### Integration Tests

**Location**: `tests/integration/`

**Purpose**: Test business logic with stubbed I/O (no real database required; the reranker is real)

**Run locally**:
```bash
uv run pytest tests/integration/
```

These tests use in-memory stubs for storage and embeddings, run the real reranker and run fast (~seconds). They form the bulk of our test suite and catch 90% of issues.

### End-to-End Tests

#### SQLite E2E Tests

**Location**: `tests/e2e_sqlite/`

**Purpose**: Test complete stack with in-memory SQLite

**Requirements**: an NVIDIA GPU (see above); no Docker required

**Run locally**:
```bash
uv run pytest tests/e2e_sqlite/
```

**CI Status**: ✅ Runs automatically in CI workflow on every push/PR, on the GPU runner

These tests use an in-memory SQLite database for test isolation, with automatic cleanup. The suite is large (several thousand tests across the integration and SQLite e2e directories), so run a single file or `-k` selection while iterating and the whole directory before pushing. They provide full-stack coverage without Docker dependencies.

#### PostgreSQL E2E Tests

**Location**: `tests/e2e/`

**Purpose**: Test complete stack with real PostgreSQL

**Requirements**: PostgreSQL running in Docker

**Run locally**:
```bash
# The fixtures start (or reuse) the forgetful-db Docker container (pgvector image, see tests/e2e/README.md)
uv run pytest -m e2e
```

**CI Status**: Runs on every branch push, pull request, and manual workflow dispatch.

These tests are marked with `@pytest.mark.e2e` and validate the full application stack with
PostgreSQL; the default `pytest` options exclude them (`-m "not e2e"`). Use a disposable database: the
E2E fixtures clear its application tables. They also provide the PostgreSQL half of the 100 % coverage
gate, so the `coverage` gate cannot pass on a machine without Docker.

#### User creation tests

`test_user_creation_sqlite.py` and `test_user_creation_e2e.py` share cases that check real duplicate
inserts, preservation of the existing user, recovery when another request creates the user first,
and propagation of unrelated database errors; only the timing of the competing lookup is arranged.

#### Backup and recovery tests

These tests use a temporary SQLite file or the PostgreSQL E2E database. They check that a
backup restores memories, metadata, links, obsolete state, and searchable vectors. They also
run the real CLI, fail the embedding provider after a saved batch or during the final search
check, and verify that automatic recovery restores the original data.

The embedding provider returns fixed vectors, so these tests need no model downloads or API keys.
PostgreSQL requires `pg_dump` and `psql` compatible with the PostgreSQL 16 test server. CI installs
the client tools explicitly and fails if either is missing; local runs skip with an explanation.

```bash
uv run pytest tests/e2e_sqlite/test_backup_restore_sqlite.py -q --durations=5
uv run pytest tests/e2e/test_backup_recovery_e2e.py \
  tests/e2e/test_zz_backup_restore_e2e.py -m e2e -q --durations=5
```

To target a disposable database already running on port 55099, prefix the PostgreSQL command
with `PGPORT=55099 POSTGRES_DB=forgetful_backup_tests`. Set `POSTGRES_USER` and
`POSTGRES_PASSWORD` to that test database's credentials as needed.

Both suites run in the existing CI jobs. A push to a branch with an open PR starts both a push
run and a PR run; newer runs cancel older runs for the same workflow and ref.

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
2. `test` (self-hosted GPU runner, `runs-on: [self-hosted, linux, gpu]`): run integration tests (stubbed
   storage and embeddings, real reranker) and SQLite E2E tests (real SQLite, full stack).
3. `smoke-test` (hosted runner): build the wheel, check the Alembic files are included and
   `forgetful --help` works.

The GPU runner is a machine you provide and register with those labels; see
[quality-gates.md](quality-gates.md#pipeline) for what it needs. The Windows leg was removed because the
TensorRT reranker is Linux-only.

**Merge requirements**: Configure branch rules to require this workflow's `test` check.
The workflow alone does not make it mandatory for merging.

**Test Coverage**: Integration tests validate business logic with stubbed storage and embeddings and the real reranker. SQLite E2E tests validate the complete application stack including real database interactions, all without Docker dependencies.

### `e2e.yml` - PostgreSQL E2E Validation

**Triggers**: Every branch push, pull request, and manual workflow dispatch

**Purpose**: Validate with real PostgreSQL database

**Matrix**: Python 3.14, on the self-hosted GPU runner (`runs-on: [self-hosted, linux, gpu]`).

**Steps**:
1. Install PostgreSQL backup client tools.
2. Run PostgreSQL E2E tests (`tests/e2e/` with `-m e2e` marker), including backup recovery.

**Merge requirements**: Configure the repository's branch rules to require the `e2e` check.
The workflow alone does not make a check mandatory for merging.

**Test Coverage**: Validates the full application stack with PostgreSQL, including real
`pg_dump`/`psql` backup and restore.

### `quality.yml` - Quality Gates

**Triggers**: Every push and pull request, a weekly schedule (mutation testing) and manual dispatch

**Purpose**: Run the same gates as the local Git hooks (`scripts/quality`) so a bypassed hook is caught

**Jobs**: `static` (lint, import boundaries, security, dead code, complexity, types and pylint on a hosted
runner, on Python 3.14), then on the self-hosted GPU runner `snapshots`, `coverage` (SQLite and PostgreSQL
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

---

## Deployment

**Workflow**: `deploy.yml`

**Purpose**: Deploy to staging/production environments

**Method**: Self-hosted runners with sparse checkout

**Environment Selection**: Runners match environment by label (e.g., `staging` runner for staging deployment)
