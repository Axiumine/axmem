# Axmem Development Guide

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

The integration and end-to-end suites run the real TensorRT embedder and reranker, so they need an
NVIDIA GPU (driver R580 or newer, see [docs/gpu-models.md](docs/gpu-models.md)). There is no CPU path, test-only
embedder, stub reranker or skip: each suite ends the pytest run with exit code 2 and the cause when the GPU is
unusable. The session fixtures `tensorrt_embedder` and `tensorrt_reranker` (`tests/conftest.py`) return the
production adapters.

### Integration Tests
**Location**: `tests/integration/`
**Purpose**: Test business logic on the production Qdrant repository (a real Qdrant node in Docker, the real TensorRT
models); only HTTP I/O to remote providers and servers is stubbed. There is no in-memory repository or embedding stub.
**Run locally**:
```bash
uv run pytest tests/integration/
```
These tests share one Qdrant container per test process and keep apart by a tenant of their own. They form the bulk of
our test suite and catch 90% of issues.

### End-to-End Tests

#### Qdrant E2E Tests
**Location**: `tests/e2e/`
**Purpose**: Test complete stack with a real Qdrant node
**Requirements**: Docker; `tests/qdrant_support.py` starts a Qdrant container on a random loopback port with an API
key and removes it at the end of the run, and ends the run at once when it cannot (it never skips)
**Run locally**:
```bash
uv run pytest tests/e2e/
```
#### Cluster E2E Tests
**Location**: `tests/cluster/` (marker `cluster`, never selected by default)
**Purpose**: Test a three-node Qdrant cluster (replication, failover, concurrent writers, per-node equality)
**Run locally**: `uv run python -m scripts.quality gate e2e-qdrant` (or `uv run pytest -m cluster`)

Docker is only a test dependency. The project has no container deployment: it runs natively (`uv run main.py`).


## Quality gates
Commits and pushes are gated by `.githooks/` (enable with `uv run python -m scripts.quality install`).
The pipeline (ruff, import-linter, bandit, semgrep, shellcheck, actionlint, reuse, vulture, radon cc/mi, mypy, pylint,
the architecture tests, the package and wheel checks, snapshot
tests, coverage at 100%, Qdrant e2e, memray, trivy, Qodana, mutmut,
GitNexus) and how to run or bypass single gates is documented in
[docs/quality-gates.md](docs/quality-gates.md). Run `uv run python -m scripts.quality stage pre-commit`
before committing; do not weaken a threshold to make a gate pass, fix the code or justify the exception.

Every check that guards this project is a named gate in `scripts/quality/registry.py` that a hook stage runs
(`pre-commit` and `pre-push` call `python -m scripts.quality stage <name>`). A new check is added as a gate and to a
stage, with its tool pinned in the `dev` group; it is never only a manual command, a step of a workflow or a line of a
script that no hook reaches. Two analysis services exist only on GitHub and have no local equivalent: `scorecard.yml`
(OpenSSF Scorecard) and `codeql.yml` (CodeQL). They publish a report, are not quality gates, and are the narrow exception
to "a check is never only a step of a workflow"; `tests/architecture/test_workflows.py` fixes their names.

The `pre-push` hook is the GPU gate. Hosted CI has no GPU and there is no self-hosted runner, so GitHub Actions runs only
the static gates (ruff, import-linter, bandit, semgrep, shellcheck, actionlint, reuse, vulture, radon, mypy, pylint, trivy), the
architecture tests and the package and wheel gates, each called by its gate name, the same name the hooks run. Everything that needs the NVIDIA GPU, Docker or a real Qdrant (tests, e2e, the cluster suite, coverage,
memray, Qodana, mutation) runs only in `pre-push` on the developer machine, where a missing GPU or Docker daemon fails the
push instead of skipping a gate. Never add a self-hosted runner, a schedule or a GPU or Docker job to `.github/workflows`
(`tests/architecture/test_workflows.py` fails on it), and commit before pushing: the gates test the working tree.

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
uv run python -m scripts.quality gate ruff
```

**Note**: Ruff UP006 rule enforces Python 3.14 built-in generics (`list` instead of `typing.List`, `dict` instead of `typing.Dict`, etc.). This catches legacy type hint syntax automatically.

## Skills 
The solution has a [catalogue of skills](./skills/) that is used to help drive agent behaviour, 
whenever adding or modifying behaviour around tools or the system, you should look to review these 
and consider whether these need updating as part of the changes you have made to the solution as well.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus under the name of its checkout directory (`list_repos` shows it; `{repo}` below).

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
| `gitnexus://repo/{repo}/context` | Codebase overview, check index freshness |
| `gitnexus://repo/{repo}/clusters` | All functional areas |
| `gitnexus://repo/{repo}/processes` | All execution flows |
| `gitnexus://repo/{repo}/process/{name}` | Step-by-step execution trace |

<!-- gitnexus:end -->
