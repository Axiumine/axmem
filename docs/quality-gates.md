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
```

## Pipeline

Gates in the same group run in parallel; groups run left to right and the first failing group
stops the stage (cheapest checks first).

| Stage | Pipeline |
|---|---|
| `pre-commit` (staged files) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi → mypy, pylint → snapshots, tests-fast → gitnexus |
| `pre-push` (whole project) | static group → mypy, pylint → tests → e2e-postgres → coverage → compat, memray → trivy → qodana → mutation (opt-in) → gitnexus |
| `ci` | static group → mypy, pylint → tests → e2e-postgres → coverage → memray → trivy |
| `post-commit` | refreshes the local GitNexus index in the background (never blocks) |

CI (`.github/workflows/quality.yml`, `ci.yml`, `e2e.yml`) additionally runs the suites on
Python **3.12 and 3.14**; locally the `compat` gate runs the SQLite suites on 3.14.

## Gates

| Gate | What it enforces | Config |
|---|---|---|
| `ruff` | lint (existing rule set) | `ruff.toml` |
| `imports` | layer contracts routes → services → protocols → repositories | `[tool.importlinter]` |
| `bandit` | security lint on app/main/alembic/harness/scripts | `[tool.bandit]` |
| `semgrep` | repo-specific rules (no `print` on the MCP stdout path, deprecated asyncio, SQL built from strings, …); `SEMGREP_PACKS="p/python p/security-audit"` adds registry packs | `.semgrep/`, `.semgrepignore` |
| `vulture` | dead code, min confidence 60 | `[tool.vulture]`, `vulture_whitelist.py` |
| `radon-cc` | no function/method rated C or worse (minimum rating B), tests included | `radon.cfg` |
| `radon-mi` | every file maintainability grade A (score above 19), tests included | `radon.cfg` |
| `mypy` | type check of `app`, `main.py`, `scripts` | `[tool.mypy]` |
| `pylint` | score at least 8/10 | `[tool.pylint]` |
| `snapshots` | syrupy snapshot tests (`-m snapshot`): MCP tool surface, HTTP route table, CLI help, settings defaults, DB schema | `tests/snapshots/` |
| `tests-fast` | architecture + integration suites | `pytest.ini` |
| `tests` | architecture + integration + SQLite e2e with branch coverage | `[tool.coverage.*]` |
| `e2e-postgres` | `tests/e2e` against a real PostgreSQL container (needs Docker) | `tests/e2e/conftest.py` |
| `coverage` | combined **100 %** line and branch coverage of `app` and `main.py` | `[tool.coverage.report]` |
| `compat` | the SQLite suites on Python 3.14 (`.venv314`) | |
| `memray` | memory budgets (`@pytest.mark.limit_memory`) via pytest-memray (Linux/macOS) | `tests/e2e_sqlite/test_memory_budget_sqlite.py` |
| `trivy` | HIGH/CRITICAL vulnerabilities, secrets and misconfigurations (`fs` scan) | `trivy.yaml`, `.trivyignore` |
| `qodana` | Qodana Ultimate Plus (`jetbrains/qodana-python`), token from `QODANA_TOKEN` | `qodana.yaml` |
| `mutation` | mutmut on changed `app/` modules; survivors above `MUTATION_MAX_SURVIVORS` fail. Opt-in: `MUTATION=1`; weekly in CI | `[tool.mutmut]` |
| `gitnexus` | `detect-changes` on the staged diff / branch; partial or truncated answers fail | `.gitnexus/` (local index) |

`radon cc . -a -nc` and `radon mi . -s` work from the repository root and report the same files the
gates check (`radon.cfg` excludes virtualenvs and generated paths).

## Secrets

`QODANA_TOKEN` goes into the git-ignored `.env` file (see `.env.example`) or the environment. The
runner reads only the keys a gate declares and never prints them; CI uses the `QODANA_TOKEN`
repository secret.

## Bypassing, deliberately

```bash
SKIP=qodana,trivy git push            # skip named gates once
ONLY=mypy uv run python -m scripts.quality stage pre-commit
QUALITY_FULL=1 git commit             # pre-commit over the whole project, not just staged files
MUTATION=1 git push                   # include mutation testing
```

A skipped gate is a decision to record in the PR description, not a default. A gate whose tool is
missing (for example no Docker for `e2e-postgres`) reports `SKIP` with the reason and the
`coverage` gate then fails, because untested Postgres code cannot be called covered.

## Maintaining the thresholds

* Coverage must stay at 100 %. Use `# pragma: no cover` only for code that cannot execute in any
  supported configuration, with a one-line reason.
* New vulture false positives go into `vulture_whitelist.py` with a comment naming the consumer
  (framework hook, pydantic field, route registration).
* New semgrep rules live in `.semgrep/forgetful.yml`; add a case to the rule's test when it is
  precise enough to need one.
* Raise the Python matrix (`quality.yml`, `ci.yml`, `e2e.yml`, `pyproject.toml` classifiers) together.
