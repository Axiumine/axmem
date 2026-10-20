# Forgetful Development Guide

## Architecture

Before changing code or reasoning about this repository's architecture, read the
[architecture overview](architecture.md).

Consult the [detailed class map](docs/assets/mycelium_class_diagram.md) only as needed,
using the overview's reading advisories. Do not load the entire map into context by default.

After major code changes, check whether the architecture diagram or explanations are still
accurate and update them if needed. Small changes do not require diagram regeneration.
Preserve reviewed explanations that remain accurate. Keep the overview at most 500 lines;
it has no column-width limit, including Mermaid statements.

Mycelium is optional tooling, not a prerequisite for contributing or updating the overview.
Its [architecture skill][mycelium-architecture] and [map-generation skill][mycelium-mermaid]
are available in [ScottRBK/mycelium][mycelium] if useful. If refreshing the generated backup,
use fresh analysis, keep the JSON outside the repository, and export full detail with
`--max-classes 1000`, `--test-path tests`, and `--test-path test_harness/runs`.

[mycelium]: https://github.com/ScottRBK/mycelium
[mycelium-architecture]:
  https://github.com/ScottRBK/mycelium/blob/master/skills/mycelium-architecture/SKILL.md
[mycelium-mermaid]:
  https://github.com/ScottRBK/mycelium/blob/master/skills/mycelium-mermaid/SKILL.md

Layered architecture:
 routes -> services -> protocols -> repositories/adapters 
No pollution of service layer with integration/implementation details

Run `uv run pytest tests/architecture/` to check and prove the agreed import boundaries.

## Testing Philosophy
We focus on **integration and E2E tests** over unit tests. Tests should cover critical workflows without exhaustive edge case coverage.

Aim for each test to finish in under 15 seconds; this is advisory, with no enforced timing checks.

The integration, SQLite E2E and PostgreSQL E2E suites run the real TensorRT embedder and reranker, so they need an
NVIDIA GPU (driver R580 or newer, see [docs/gpu-models.md](docs/gpu-models.md)). There is no CPU path, test-only
embedder, stub reranker or skip: each suite ends the pytest run with exit code 2 and the cause when the GPU is
unusable. The session fixtures `tensorrt_embedder` and `tensorrt_reranker` (`tests/conftest.py`) return the
production adapters.

### Integration Tests
**Location**: `tests/integration/`
**Purpose**: Test business logic with stubbed I/O (no real database required)
**Run locally**:
```bash
uv run pytest tests/integration/
```
These tests use in-memory stubs and run fast (~seconds). They form the bulk of our test suite and catch 90% of issues.

### End-to-End Tests

#### SQLite E2E Tests
**Location**: `tests/e2e_sqlite/`
**Purpose**: Test complete stack with in-memory SQLite
**Requirements**: None (no Docker required)
**Run locally**:
```bash
uv run pytest tests/e2e_sqlite/
```
These tests use an in-memory SQLite database for test isolation. Fast execution with automatic cleanup. 

#### PostgreSQL E2E Tests
**Location**: `tests/e2e/`
**Purpose**: Test complete stack with real PostgreSQL
**Requirements**: Docker; the fixtures start (or reuse) a `forgetful-db` PostgreSQL container
**Run locally**:
```bash
docker rm -f forgetful-db && docker volume rm forgetful_db_data  # optional: start from an empty database
uv run pytest -m e2e
```
Docker is only a test dependency. The project has no container deployment: it runs natively (`uv run main.py`).


## Quality gates
Commits and pushes are gated by `.githooks/` (enable with `uv run python -m scripts.quality install`).
The pipeline (ruff, import-linter, bandit, semgrep, vulture, radon cc/mi, mypy, pylint, snapshot
tests, coverage at 100%, PostgreSQL e2e, memray, trivy, Qodana, mutmut,
GitNexus) and how to run or bypass single gates is documented in
[docs/quality-gates.md](docs/quality-gates.md). Run `uv run python -m scripts.quality stage pre-commit`
before committing; do not weaken a threshold to make a gate pass, fix the code or justify the exception.

## Changelog
Every commit that changes behaviour updates [CHANGELOG.md](CHANGELOG.md) and
[docs/changes-from-forgetful.md](docs/changes-from-forgetful.md) in the same commit. Behaviour means anything a
user, an MCP client or an operator can see: tools, REST routes, settings, schema and migrations, dependencies,
supported platforms and the quality gates. Move the matching planned entry to its section and mark it Done, or
add a new entry with the reason for the change. A commit cannot name its own hash, so the next commit that
touches these files adds it. Refactors, test-only and typo commits need no entry.

## Private references
Some names must never appear in this repository: not in code, comments, docs, tests, fixtures, settings,
commit messages or pull request text. The list is local to each clone (`.git/info/denylist`, untracked) and the
hooks enforce it. If a hook refuses a commit or push, remove the term; never bypass the guard. Do not copy
names from files outside this repository into anything you write here: describe the behaviour instead.

## Linting
Ensure that you run ruff following any changes and address any issues raised
```bash
uv tool run ruff check .
```

**Note**: Ruff UP006 rule enforces Python 3.14 built-in generics (`list` instead of `typing.List`, `dict` instead of `typing.Dict`, etc.). This catches legacy type hint syntax automatically.

## Skills 
The solution has a [catalogue of skills](./skills/) that is used to help drive agent behaviour, 
whenever adding or modifying behaviour around tools or the system, you should look to review these 
and consider whether these need updating as part of the changes you have made to the solution as well.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **forgetful**.

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/forgetful/context` | Codebase overview, check index freshness |
| `gitnexus://repo/forgetful/clusters` | All functional areas |
| `gitnexus://repo/forgetful/processes` | All execution flows |
| `gitnexus://repo/forgetful/process/{name}` | Step-by-step execution trace |

<!-- gitnexus:end -->
