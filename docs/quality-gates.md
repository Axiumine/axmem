# Quality gates

Every check below runs from one orchestrator, `scripts/quality` (`python -m scripts.quality`),
so Git hooks, CI and a developer shell execute identical commands. Tool versions are pinned in
`pyproject.toml` (`dev` dependency group, locked in `uv.lock`); semgrep, trivy and Qodana run
isolated because their dependency trees conflict with the application pins.

```bash
uv sync --locked --all-extras --dev          # install the toolchain
uv run python -m scripts.quality install     # enable .githooks/ (sets core.hooksPath)
uv run python -m scripts.quality list        # gates and stage pipelines
uv run python -m scripts.quality gate mypy   # run one gate on the whole project
uv run python -m scripts.quality gate ruff --staged
uv run python -m scripts.quality gate ruff --files app/bootstrap.py main.py
uv run python -m scripts.quality stage pre-commit   # what the pre-commit hook runs
uv run python -m scripts.quality uninstall   # unset core.hooksPath
```

Python 3.12 to 3.14 is the supported range (`requires-python = ">=3.12"`, classifiers 3.12, 3.13 and 3.14,
`.python-version` 3.12). The gates test the two ends of it, **3.12 and 3.14**: locally the `compat` gate runs
the SQLite suites on 3.14, and CI runs the matrices described below; 3.13 is only exercised by the wheel smoke
test. The mypy and pylint targets are 3.12.

## Hooks

`install` makes every file in `.githooks/` executable and sets `core.hooksPath` to `.githooks` in the
repository's Git config (shared by all worktrees of the clone). The hooks call
`uv run --frozen python -m scripts.quality stage <name>` (`pre-commit` and `pre-push`), so `uv` must be on `PATH` and the environment
synced as above.

| Hook | Runs | Blocks |
|---|---|---|
| `pre-commit` | stage `pre-commit` on the staged files | yes |
| `pre-push` | stage `pre-push` on the whole project | yes |
| `post-commit` | refreshes the local GitNexus index in the background (needs `.gitnexus/run.cjs` and `node`) | never |

## Pipeline

Gates in the same group run in parallel; groups run left to right and the first failing group
stops the stage (cheapest checks first). A gate that cannot run (missing tool, no Docker, nothing
relevant staged) reports `SKIP` with the reason and does not fail the stage.

| Stage | Pipeline |
|---|---|
| `pre-commit` (staged files) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi → mypy, pylint → snapshots, tests-fast → gitnexus |
| `pre-push` (whole project) | static group → mypy, pylint → tests → e2e-postgres → coverage → compat, memray → trivy → qodana → mutation (opt-in) → gitnexus |
| `ci` | static group → mypy, pylint → tests → e2e-postgres → coverage → memray → trivy |

`pre-commit` scopes ruff, bandit, semgrep, radon and pylint to the staged files. mypy, vulture,
import-linter and the tests always run over the whole project (a changed signature breaks unchanged
callers) but are skipped when nothing relevant is staged. `QUALITY_FULL=1` makes `pre-commit` check the
whole project.

CI does not call `stage ci` as one job; `.github/workflows/quality.yml` runs the same gates by name, split
across jobs:

| Workflow | What runs | Python |
|---|---|---|
| `quality.yml` job `static` | ruff, imports, bandit, semgrep (with `p/python p/security-audit` added), vulture, radon-cc, radon-mi, mypy, pylint, snapshots | 3.12 and 3.14 |
| `quality.yml` job `coverage` | `tests`, `e2e-postgres` (uses the runner's Docker), `coverage` | 3.12 |
| `quality.yml` jobs `memory`, `trivy` | `memray`, `trivy` | 3.12 |
| `quality.yml` job `qodana` | Qodana action with the `QODANA_TOKEN` repository secret; not run for pull requests from forks | n/a |
| `quality.yml` job `mutation` | `mutation` with `MUTATION=1`, weekly (Mondays 03:17 UTC) and on manual dispatch | 3.12 |
| `ci.yml` | architecture, integration and SQLite e2e suites plus `ruff` on Ubuntu and Windows; wheel build smoke test; offline Docker image test | 3.12 and 3.14 (Ubuntu and Windows), 3.13 for the wheel smoke test |
| `e2e.yml` | `tests/e2e` against PostgreSQL | 3.12 and 3.14 |

## Gates

| Gate | What it enforces | Config |
|---|---|---|
| `ruff` | lint (existing rule set) | `ruff.toml` |
| `imports` | layer contracts routes → services → protocols → repositories | `[tool.importlinter]` |
| `bandit` | security lint on app/main/alembic/harness/scripts | `[tool.bandit]` |
| `semgrep` | repo-specific rules (no `print` on the MCP stdout path, deprecated asyncio, SQL built from strings, …); `SEMGREP_PACKS="p/python p/security-audit"` adds registry packs. Uses `semgrep` on `PATH`, else `uvx`, else the pinned Docker image | `.semgrep/forgetful.yml`, `.semgrepignore` |
| `vulture` | dead code, min confidence 60 | `[tool.vulture]`, `vulture_whitelist.py` |
| `radon-cc` | no function/method rated C or worse (minimum rating B), tests included | `radon.cfg` |
| `radon-mi` | every file maintainability grade A (score above 19), tests included | `radon.cfg` |
| `mypy` | type check of `app`, `main.py`, `scripts` | `[tool.mypy]` |
| `pylint` | score at least 8/10 | `[tool.pylint]` |
| `snapshots` | syrupy snapshot tests (`-m snapshot`): CLI argument tree, settings defaults, SQLite and PostgreSQL schema, migration chain, MCP meta-tool signatures and tool catalogue, HTTP route table | `tests/integration/test_snapshots.py`, `tests/e2e_sqlite/test_snapshots_sqlite.py` |
| `tests-fast` | `tests/architecture` and `tests/integration`, stopping at the first failure | `pytest.ini` |
| `tests` | architecture + integration + SQLite e2e with branch coverage (erases previous coverage data first) | `[tool.coverage.*]` |
| `e2e-postgres` | `tests/e2e` (`-m e2e`) against a real PostgreSQL container, appending to the coverage data (needs Docker) | `tests/e2e/conftest.py` |
| `coverage` | combined **100 %** line and branch coverage of `app` and `main.py`, from the SQLite and PostgreSQL runs together | `[tool.coverage.report]` |
| `compat` | the SQLite suites on Python 3.14 (`uv run --python 3.14` into `.venv314`; skipped without `uv`) | |
| `memray` | memory budgets (`@pytest.mark.limit_memory`) via pytest-memray, run as `pytest tests/e2e_sqlite -m memory --memray` (Linux/macOS only) | `tests/e2e_sqlite/test_memory_budget_sqlite.py` |
| `trivy` | HIGH/CRITICAL vulnerabilities (fixed ones), secrets and misconfigurations (`fs` scan) | `trivy.yaml`, `.trivyignore` |
| `qodana` | Qodana Ultimate Plus (`jetbrains/qodana-python`), token from `QODANA_TOKEN`; analyses a `git archive` export of `HEAD` (uncommitted changes are not seen) with `--image`; skipped when the `qodana` CLI is missing, failed when the token is missing | `qodana.yaml` |
| `mutation` | mutmut on changed `app/` modules; survivors above `MUTATION_MAX_SURVIVORS` fail. Opt-in (`MUTATION=1`), see below | `[tool.mutmut]` |
| `gitnexus` | `detect-changes` on the staged diff (pre-commit) or against `origin/main` (pre-push); a partial or truncated answer is retried once, then fails. Skipped without a local index or `node` | `.gitnexus/` (local index) |

`radon cc . -a -nc` and `radon mi . -s` work from the repository root and report the same files the
gates check (`radon.cfg` excludes virtualenvs and generated paths).

### Coverage

`tests` collects branch coverage from `tests/architecture`, `tests/integration` and `tests/e2e_sqlite`;
`e2e-postgres` appends the PostgreSQL run; `coverage` then fails below 100 % for the two together.
The PostgreSQL repositories are only reachable from `tests/e2e`, so a machine without Docker cannot
satisfy the gate: `e2e-postgres` reports `SKIP` and `coverage` fails, because untested PostgreSQL code
cannot be called covered. Branch coverage runs with `parallel = true` and `concurrency =
["greenlet", "thread"]` (SQLAlchemy's async layer and aiosqlite) and also follows subprocesses.

### Snapshots

The snapshot tests pin the public surfaces that must not change by accident. A changed snapshot is a
contract change: review the diff in Git, then refresh deliberately.

| Surface | Test | Snapshot directory |
|---|---|---|
| CLI argument tree, settings defaults (built from code defaults, ignoring the environment and dotenv files; secrets and the build-derived `SERVICE_VERSION` are masked), SQLite and PostgreSQL DDL, Alembic revision chain | `tests/integration/test_snapshots.py` | `tests/integration/__snapshots__/test_snapshots/` |
| MCP meta-tool signatures, discovered tool catalogue, HTTP route table | `tests/e2e_sqlite/test_snapshots_sqlite.py` | `tests/e2e_sqlite/__snapshots__/test_snapshots_sqlite/` |

```bash
uv run pytest -m snapshot                     # what the gate runs
uv run pytest -m snapshot --snapshot-update   # rewrite the snapshots, then review `git diff`
```

Adding a setting, CLI flag, route, tool, table or migration changes a snapshot, which is expected;
commit the refreshed files with the change that caused them.

### Memory budgets

`tests/e2e_sqlite/test_memory_budget_sqlite.py` (marker `memory`) seeds memories through the MCP tools and
holds `@pytest.mark.limit_memory` budgets (bulk create, query with reranking). The ONNX models are loaded
before the measured section, and the budgets are generous multiples of the measured peak so they fail on a
leak or an accidental full-table load, not on noise. Without `--memray` the tests run as ordinary smoke
tests; on Windows the gate is skipped because memray is not available there. Change a budget only with the
new measurement in a comment next to it.

### Mutation testing

`mutation` runs `mutmut` over the modules a change touched. A run over the whole of `app/` produces roughly
33,000 mutants and is not practical, so the gate always needs a scope:

* the `app/` files that differ from `origin/main` (the default for `pre-push` and a plain
  `gate mutation`), or the `app/` files named with `--staged` or `--files`, or
* an explicit `MUTATION_TARGETS`, a space-separated list of mutmut name patterns such as
  `MUTATION_TARGETS='app.utils.*'`, which wins over both.

With no scope it reports `SKIP`. The gate allows one `mutmut run` two hours; surviving mutants in the
targets above `MUTATION_MAX_SURVIVORS` (default 0) fail it. `[tool.mutmut]` selects the SQLite-capable
suites (`tests/architecture`, `tests/integration`, `tests/e2e_sqlite`) as the tests that must kill a mutant
and leaves `app/repositories/postgres/*` and `app/_version.py` unmutated, since the PostgreSQL code is only
tested by the Docker-based suite. The scheduled `quality.yml` job sets `MUTATION=1` only, so on a checkout
of `main` (no diff against `origin/main`) it reports `SKIP` unless `MUTATION_TARGETS` is provided.

## Secrets

`QODANA_TOKEN` goes into the git-ignored dotenv file in the repository root (see `.env.example` for the
key name) or the environment. The runner reads only the keys a gate declares, takes the process
environment first, and never prints them; CI uses the `QODANA_TOKEN` repository secret. Do not put the
token in `qodana.yaml`.

## Bypassing, deliberately

```bash
SKIP=qodana,trivy git push            # skip named gates once
ONLY=mypy uv run python -m scripts.quality stage pre-commit
QUALITY_FULL=1 git commit             # pre-commit over the whole project, not just staged files
MUTATION=1 git push                   # include mutation testing
MUTATION_TARGETS='app.utils.*' uv run python -m scripts.quality gate mutation   # one scoped gate
```

`SKIP` and `ONLY` take comma-separated gate names and apply to stages. `ONLY` runs just those gates of the
stage; naming the opt-in `mutation` gate in `ONLY` also selects it. `MUTATION=1` only opts the gate into a
stage: `gate mutation` runs it directly without that variable. Other knobs: `SEMGREP_PACKS` (extra registry packs, needs
network), `TRIVY_BIN` (explicit trivy binary; otherwise `PATH`, then the Docker image) and
`MUTATION_MAX_SURVIVORS`.

A skipped gate is a decision to record in the PR description, not a default. A gate whose tool is
missing (for example no Docker for `e2e-postgres`) reports `SKIP` with the reason and the
`coverage` gate then fails, because untested Postgres code cannot be called covered.

## Maintaining the thresholds

* Coverage must stay at 100 %. Use `# pragma: no cover` only for code that cannot execute in any
  supported configuration, with a one-line reason. `[tool.coverage.report]` already excludes
  `TYPE_CHECKING` blocks, `@overload`, `raise NotImplementedError`, bare `...` bodies and the
  `__main__` guard; `app` and `main.py` currently use no `pragma: no cover` at all.
* New vulture false positives go into `vulture_whitelist.py` with a comment naming the consumer
  (framework hook, pydantic field, route registration). Run `uv run python -m scripts.quality gate vulture`
  (or `uv run vulture`) to list them as `file:line: unused name (NN% confidence)`, delete genuinely dead
  code instead of listing it, and put names that apply to a whole class of code (validators, route
  decorators, alembic or SQLAlchemy hooks) into `[tool.vulture]` `ignore_decorators` / `ignore_names`
  rather than the whitelist. The whitelist is only parsed by vulture, never imported or executed, and
  vulture does not scan `tests/`.
* New semgrep rules live in `.semgrep/forgetful.yml` (rule ids are prefixed `forgetful-`).
* Fix a bandit or semgrep finding when the code can be made safe (SQL fragments that cannot be
  bound parameters go through `app/repositories/vector_ddl.py`). Suppress only code that is safe
  by construction, inline and with the reason on the same line: `# nosec B603 - argv list from
  constants, no shell` or `# nosemgrep: <rule-id> - reason`. Keep the matching `ruff.toml`
  per-file `S` ignore in step, and never add file-level skips to `[tool.bandit]`.
* The same convention applies to the other linters: `# noqa: <code> - reason` for ruff,
  `# type: ignore[<code>]` with a reason for mypy (`warn_unused_ignores` flags stale ones). The code base
  uses no inline pylint disables; a noisy check is disabled once, with a comment, in
  `[tool.pylint."messages control"]`.
* Raise the Python matrix (`quality.yml`, `ci.yml`, `e2e.yml`, `pyproject.toml` classifiers, the
  `compat` gate's `COMPAT_PYTHON`) together.
