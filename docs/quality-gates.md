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

Python 3.14 is the only supported version (`requires-python = ">=3.14"`, classifier 3.14,
`.python-version` 3.14). The gates and the CI workflows described below all run on 3.14, and the
mypy, pylint and ruff targets are 3.14.

## Hooks

`install` makes every file in `.githooks/` executable and sets `core.hooksPath` to `.githooks` in the
repository's Git config (shared by all worktrees of the clone). The hooks call
`uv run --frozen python -m scripts.quality stage <name>` (`pre-commit` and `pre-push`), so `uv` must be on `PATH` and the environment
synced as above.

| Hook | Runs | Blocks |
|---|---|---|
| `pre-commit` | stage `pre-commit` on the staged files | yes |
| `pre-push` | stage `pre-push` on the whole project; only when the pushed commit is the checked-out `HEAD` (the gates read the working tree), pushing any other ref is not gated | yes |
| `commit-msg` | commit message format: `[🤖 ]<type>(<scope>): <subject>`, types feat/fix/chore/docs/refactor/test/perf/ci/build, subject <=150 chars, body <=10 lines, no AI attribution trailers; skip once with `SKIP=commit-msg` or `--no-verify` | yes |
| `post-commit` | refreshes the local GitNexus index in the background (needs `.gitnexus/run.cjs` and `node`) | never |
| `denylist-check` | called by `pre-commit` (staged file names and added lines), `commit-msg` (the message) and `pre-push` (every unpushed commit); refuses any match with `.git/info/denylist` | yes |

`denylist-check` keeps terms that must not appear in the repository out of it. The terms live in
`.git/info/denylist` (one extended regular expression per line, matched case-insensitively), a file that belongs
to the clone and is never tracked, so no tracked file names them. Without the file nothing is checked. The guard
never prints a term, and `SKIP` does not bypass it (covered by `tests/integration/test_denylist_check_hook.py`).

`commit-msg` is stage-less: it only validates the message text, so it is not run by `scripts.quality stage`
and is not part of the pipeline below. Comment lines and the `git commit -v` scissors section are stripped
before checking, and merge, revert and `fixup!`/`squash!`/`amend!` commits skip the format check. AI
`Co-Authored-By` trailers and "Generated with" lines are rejected; mark AI-written commits with the `🤖`
subject prefix instead (covered by `tests/integration/test_commit_msg_hook.py`).

## Pipeline

Gates in the same group run in parallel; groups run left to right and the first failing group
stops the stage (cheapest checks first). A gate that cannot run (missing tool, no Docker, nothing
relevant staged) reports `SKIP` with the reason and does not fail the stage.

**GPU requirement.** Every gate that runs `tests/integration`, `tests/e2e` or `tests/cluster` (`snapshots`,
`tests-fast`, `tests`, `e2e-qdrant`, `memray`, `mutation`) needs an NVIDIA GPU with driver R580 or newer
and about 4.1 GiB of free GPU memory per pytest process (embedder and reranker, plus the same again for the
standalone test's server process). The embedder and reranker tests run the real TensorRT engines;
there is no CPU path, no stub and no skip. Each of these suites starts with a preflight that ends the pytest
run with exit code 2 and the cause when no GPU is visible, TensorRT cannot be imported, or `-n` (pytest-xdist, if
installed) asks for more pytest workers than the GPU memory holds.

**Qdrant requirement.** The integration and end-to-end suites run on a real Qdrant node and the cluster suite on three
nodes, all started through Docker from `qdrant/qdrant:v1.19.2` (pull the image first; `tests/qdrant_support.py` does the
starting and removes the containers). A run that cannot start Qdrant ends with exit code 2 and the cause, it never skips.
Setting `AXMEM_TEST_QDRANT_URL` (and `AXMEM_TEST_QDRANT_API_KEY`) points the integration and end-to-end suites at a server that
already runs, for example a service container of a CI job; the tests then keep to collections of their own under a
unique prefix and delete them afterwards. The cluster suite always starts its own three nodes. These gates do not report `SKIP` without a GPU: they fail. See
[GPU models](gpu-models.md) for the host requirements. The static gates (ruff, imports, bandit, semgrep,
vulture, radon, mypy, pylint) and `tests/architecture` need no GPU.

| Stage | Pipeline |
|---|---|
| `pre-commit` (staged files) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi → mypy, pylint → snapshots, tests-fast → gitnexus |
| `pre-push` (whole project) | static group → mypy, pylint → tests → e2e-qdrant → coverage → memray → trivy → qodana → mutation (opt-in) → gitnexus |
| `ci` | static group → mypy, pylint → tests → e2e-qdrant → coverage → memray → trivy |

`pre-commit` scopes ruff, bandit, semgrep, radon and pylint to the staged files. mypy, vulture,
import-linter and the tests always run over the whole project (a changed signature breaks unchanged
callers) but are skipped when nothing relevant is staged. `QUALITY_FULL=1` makes `pre-commit` check the
whole project.

CI does not call `stage ci` as one job; `.github/workflows/quality.yml` runs the same gates by name, split
across jobs:

| Workflow | What runs | Python |
|---|---|---|
| `quality.yml` job `static` (hosted runner) | ruff, imports, bandit, semgrep (with `p/python p/security-audit` added), vulture, radon-cc, radon-mi, mypy, pylint; installs without `tensorrt-cu13-libs` | 3.14 |
| `quality.yml` job `snapshots` (GPU runner) | snapshots | 3.14 |
| `quality.yml` job `coverage` (GPU runner) | `tests`, `e2e-qdrant` (uses the runner's Docker), `coverage` | 3.14 |
| `quality.yml` job `memory` (GPU runner) | `memray` | 3.14 |
| `quality.yml` job `trivy` (hosted runner) | `trivy` | 3.14 |
| `quality.yml` job `qodana` (hosted runner) | Qodana action with the `QODANA_TOKEN` repository secret; not run for pull requests from forks | n/a |
| `quality.yml` job `mutation` (GPU runner) | `mutation` with `MUTATION=1`, weekly (Mondays 03:17 UTC) and on manual dispatch | 3.14 |
| `ci.yml` job `lint` (hosted runner) | `ruff` and `tests/architecture` | 3.14 |
| `ci.yml` job `test` (GPU runner) | integration and end-to-end suites on a Qdrant node started through Docker | 3.14 |
| `ci.yml` job `smoke-test` (hosted runner) | wheel build smoke test | 3.14 |
| `e2e.yml` (GPU runner) | `tests/e2e` on a Qdrant node started through Docker | 3.14 |

The GPU runner is a self-hosted GitHub Actions runner carrying the labels `self-hosted`, `linux` and `gpu`
(`runs-on: [self-hosted, linux, gpu]`). Its host needs an NVIDIA GPU with driver R580 or newer and enough
free GPU memory for one pytest process (about 4.1 GiB, and the same again while the standalone test's server process runs), Docker for the Qdrant
nodes, and `uv`. The repository does not install or configure the runner; the driver is the only part of the
TensorRT stack that belongs to the host, the rest is installed by `uv sync`. Hosted runners cannot run
these jobs because they have no GPU, and the Windows test matrix was removed because the TensorRT embedder and reranker are
Linux-only (its engine cache locks with `fcntl`).

## Gates

| Gate | What it enforces | Config |
|---|---|---|
| `ruff` | lint (existing rule set) | `ruff.toml` |
| `imports` | layer contracts routes → services → protocols → repositories | `[tool.importlinter]` |
| `bandit` | security lint on app/main/harness/scripts | `[tool.bandit]` |
| `semgrep` | repo-specific rules (no `print` on the MCP stdout path, deprecated asyncio, Qdrant writes outside `store.py` or without `wait`, …); `SEMGREP_PACKS="p/python p/security-audit"` adds registry packs. Uses `semgrep` on `PATH`, else `uvx`, else the pinned Docker image | `.semgrep/forgetful.yml`, `.semgrepignore` |
| `vulture` | dead code, min confidence 60 | `[tool.vulture]`, `vulture_whitelist.py` |
| `radon-cc` | no function/method rated C or worse (minimum rating B), tests included | `radon.cfg` |
| `radon-mi` | every file maintainability grade A (score above 19), tests included | `radon.cfg` |
| `mypy` | type check of `app`, `main.py`, `scripts` | `[tool.mypy]` |
| `pylint` | score at least 8/10 | `[tool.pylint]` |
| `snapshots` | syrupy snapshot tests (`-m snapshot`): CLI argument tree, settings defaults, Qdrant collection layout and payload types, MCP meta-tool signatures and tool catalogue, HTTP route table | `tests/integration/test_snapshots.py`, `tests/e2e/test_snapshots_e2e.py` |
| `tests-fast` | `tests/architecture` and `tests/integration`, stopping at the first failure | `pytest.ini` |
| `tests` | architecture + integration + end-to-end on one Qdrant node with branch coverage (erases previous coverage data first) | `[tool.coverage.*]` |
| `e2e-qdrant` | `tests/cluster` (`-m cluster`) against a real three-node Qdrant cluster (replication factor 3, write consistency 2, strong ordering, API key and internal authentication), appending to the coverage data (needs Docker) | `tests/cluster/conftest.py`, `tests/qdrant_support.py` |
| `coverage` | combined **100 %** line and branch coverage of `app` and `main.py`, from the single-node and cluster runs together | `[tool.coverage.report]` |
| `memray` | memory budgets (`@pytest.mark.limit_memory`) via pytest-memray, run as `pytest tests/e2e -m memory --memray` (Linux/macOS only) | `tests/e2e/test_memory_budget_e2e.py` |
| `trivy` | HIGH/CRITICAL vulnerabilities (fixed ones), secrets and misconfigurations (`fs` scan) | `trivy.yaml`, `.trivyignore` |
| `qodana` | Qodana Ultimate Plus (`jetbrains/qodana-python`), token from `QODANA_TOKEN`; analyses a `git archive` export of `HEAD` (uncommitted changes are not seen) with `--image`; skipped when the `qodana` CLI is missing, failed when the token is missing | `qodana.yaml` |
| `mutation` | mutmut on changed `app/` modules; survivors above `MUTATION_MAX_SURVIVORS` fail. Opt-in (`MUTATION=1`), see below | `[tool.mutmut]` |
| `gitnexus` | `detect-changes` on the staged diff (pre-commit) or against `origin/main` (pre-push); a partial or truncated answer is retried once, then fails. Skipped without a local index or `node` | `.gitnexus/` (local index) |

`radon cc . -a -nc` and `radon mi . -s` work from the repository root and report the same files the
gates check (`radon.cfg` excludes virtualenvs and generated paths).

### Coverage

`tests` collects branch coverage from `tests/architecture`, `tests/integration` and `tests/e2e`;
`e2e-qdrant` appends the cluster run; `coverage` then fails below 100 % for the two together.
Every Qdrant path the cluster suite alone reaches (failover between nodes, the per-node check) is meant to be covered
by single-node tests with injected faults too, so a machine without Docker for three nodes loses little; but whatever only
the cluster run covers makes `e2e-qdrant` `SKIP` and `coverage` fail, because untested code cannot be called covered.
Branch coverage runs with `parallel = true` and `concurrency = ["thread"]` and also follows subprocesses.

In parallel mode every measured process writes its own `.coverage.<host>.pid<N>.<random>` fragment, and
`coverage combine` merges the fragments into one data file. `[tool.coverage.run] data_file` puts that data file
and its fragments in `.coverage-data/` (ignored by Git, semgrep, trivy, Qodana and radon), so nothing
named `.coverage*` is written to the repository root, not even by an interrupted run. `pytest --cov` combines
when it finishes; `tests` starts with `coverage erase`, which removes the data file and any leftover fragments.
To merge fragments by hand, run `coverage combine --append`; to start over, delete `.coverage-data/`.

### Snapshots

The snapshot tests pin the public surfaces that must not change by accident. A changed snapshot is a
contract change: review the diff in Git, then refresh deliberately.

| Surface | Test | Snapshot directory |
|---|---|---|
| CLI argument tree, settings defaults (built from code defaults, ignoring the environment and dotenv files; secrets and the build-derived `SERVICE_VERSION` are masked), the layout of the Qdrant collections (vectors, shards, replicas, payload indexes) and the types of the stored payload | `tests/integration/test_snapshots.py` | `tests/integration/__snapshots__/test_snapshots/` |
| MCP meta-tool signatures, discovered tool catalogue, HTTP route table | `tests/e2e/test_snapshots_e2e.py` | `tests/e2e/__snapshots__/test_snapshots_e2e/` |

```bash
uv run pytest -m snapshot                     # what the gate runs
uv run pytest -m snapshot --snapshot-update   # rewrite the snapshots, then review `git diff`
```

Adding a setting, CLI flag, route, tool, collection or payload field changes a snapshot, which is expected;
commit the refreshed files with the change that caused them.

### Memory budgets

`tests/e2e/test_memory_budget_e2e.py` (marker `memory`) seeds memories through the MCP tools and
holds `@pytest.mark.limit_memory` budgets (bulk create, query with reranking). The TensorRT embedder and
reranker engines are loaded before the measured section, and the budgets are generous multiples of the measured peak so they fail on a
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
targets above `MUTATION_MAX_SURVIVORS` (default 0) fail it. `[tool.mutmut]` selects `tests/architecture` and `tests/integration` (which need Docker for their Qdrant node, like the
other suites) as the tests that must kill a mutant
and leaves `app/_version.py` unmutated. The scheduled `quality.yml` job sets `MUTATION=1` only, so on a checkout
of `main` (no diff against `origin/main`) it reports `SKIP` unless `MUTATION_TARGETS` is provided.

## Secrets

`QODANA_TOKEN` goes into the git-ignored dotenv file in the repository root (`.env.example` lists the
key name, commented out, because an empty value counts as set) or the environment. The runner reads only the keys a gate declares, takes the process
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
missing (for example no Docker for `e2e-qdrant`) reports `SKIP` with the reason and the
`coverage` gate then fails if code only that gate reaches is left untested.

## Maintaining the thresholds

* Coverage must stay at 100 %. Use `# pragma: no cover` only for code that cannot execute in any
  supported configuration, with a one-line reason. `[tool.coverage.report]` already excludes
  `TYPE_CHECKING` blocks, `@overload`, `raise NotImplementedError`, bare `...` bodies and the
  `__main__` guard; `app` and `main.py` currently use no `pragma: no cover` at all.
* New vulture false positives go into `vulture_whitelist.py` with a comment naming the consumer
  (framework hook, pydantic field, route registration). Run `uv run python -m scripts.quality gate vulture`
  (or `uv run vulture`) to list them as `file:line: unused name (NN% confidence)`, delete genuinely dead
  code instead of listing it, and put names that apply to a whole class of code (validators, route
  decorators, framework hooks) into `[tool.vulture]` `ignore_decorators` / `ignore_names`
  rather than the whitelist. The whitelist is only parsed by vulture, never imported or executed, and
  vulture does not scan `tests/`.
* New semgrep rules live in `.semgrep/forgetful.yml` (rule ids are prefixed `forgetful-`).
* Fix a bandit or semgrep finding when the code can be made safe (Qdrant writes go through
  `QdrantStore` only). Suppress only code that is safe
  by construction, inline and with the reason on the same line: `# nosec B603 - argv list from
  constants, no shell` or `# nosemgrep: <rule-id> - reason`. Keep the matching `ruff.toml`
  per-file `S` ignore in step, and never add file-level skips to `[tool.bandit]`.
* The same convention applies to the other linters: `# noqa: <code> - reason` for ruff,
  `# type: ignore[<code>]` with a reason for mypy (`warn_unused_ignores` flags stale ones). The code base
  uses no inline pylint disables; a noisy check is disabled once, with a comment, in
  `[tool.pylint."messages control"]`.
* Raise the Python version (`quality.yml`, `ci.yml`, `e2e.yml`, `pyproject.toml`
  `requires-python`, classifiers and the mypy, pylint and ruff targets, `.python-version`, `flake.nix`,
  the test harness Dockerfile) together.
