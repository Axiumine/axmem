# Quality gates

Every check below runs from one orchestrator, `scripts/quality` (`python -m scripts.quality`),
so Git hooks, CI and a developer shell execute identical commands. Tool versions are pinned in
`pyproject.toml` (`dev` dependency group, locked in `uv.lock`); semgrep, trivy and Qodana run
isolated because their dependency trees conflict with the application pins.

**Where they run.** Hosted CI has no GPU, and the project has no self-hosted runner, so CI runs only the gates that
need neither a GPU nor Docker (the static gates, trivy, the architecture tests, the wheel smoke test). Everything
that runs the real TensorRT embedder and reranker or a real Qdrant (tests, the Qdrant cluster suite, coverage, memory
budgets, Qodana, mutation) runs on the developer machine, in the `pre-push` Git hook. See
[Where the gates run](#where-the-gates-run).

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
| `pre-push` | stage `pre-push` on the whole project: the one place that runs the GPU, Docker and Qdrant gates. It gates the pushed commit only when that is the checked-out `HEAD` (the gates read the working tree), so a push of any other commit is refused and not passed unchecked; deleting a remote ref has nothing to check and goes through | yes |
| `commit-msg` | commit message format: `[🤖 ]<type>(<scope>): <subject>`, types feat/fix/chore/docs/refactor/test/perf/ci/build, subject <=150 chars, body <=10 lines, no AI attribution trailers; skip once with `SKIP=commit-msg` or `--no-verify` | yes |
| `post-commit` | refreshes the local GitNexus index in the background (needs `.gitnexus/run.cjs` and `node`) | never |
| `denylist-check` | called by `pre-commit` (staged file names and added lines), `commit-msg` (the message) and `pre-push` (every unpushed commit); refuses any match with `.git/info/denylist` | yes |

`denylist-check` keeps terms that must not appear in the repository out of it. The terms live in
`.git/info/denylist` (one extended regular expression per line, matched case-insensitively), a file that belongs
to the clone and is never tracked, so no tracked file names them. Without the file, or with a list that has no
term, nothing is checked. The guard never prints a term, and `SKIP` does not bypass it. Each line is its own
pattern (grep takes them one per line, so a line is never merged with its neighbour and keeps its own
back-reference groups). It fails closed, exiting 2 instead of letting everything through, when it cannot do its job:
a path at `.git/info/denylist` that grep cannot read (a directory, a dangling symlink, a file without read
permission), a line that is not a valid expression on its own (an unbalanced parenthesis, a term that ends in a
backslash; the message names the line numbers to fix, never their content, and a line that would only join with its
neighbour into a valid expression still counts as invalid), a `git` or `grep` call that fails, and a missing or
failing `awk`. Every external call is captured on its own, because the status of a pipeline is the status of its
last stage and would hide the failure of an earlier one. Added lines are matched without the diff column, so
`^term` anchors to the line, and a line that begins with `++` is scanned like any other (covered by
`tests/integration/test_denylist_check_hook.py`).

`commit-msg` is stage-less: it only validates the message text, so it is not run by `scripts.quality stage`
and is not part of the pipeline below. Comment lines and the `git commit -v` scissors section are stripped
before checking, and merge, revert and `fixup!`/`squash!`/`amend!` commits skip the format check. AI
`Co-Authored-By` trailers and "Generated with" lines are rejected; mark AI-written commits with the `🤖`
subject prefix instead (covered by `tests/integration/test_commit_msg_hook.py`).

## Pipeline

Gates in the same group run in parallel; groups run left to right and the first failing group
stops the stage (cheapest checks first). A gate whose tool or daemon is missing (trivy with neither a binary
nor Docker, semgrep with neither `semgrep`, `uvx` nor Docker, the cluster suite without Docker, Qodana without
its CLI) **fails** with the cause, so a push that these gates guard cannot pass because the machine cannot run one.
A gate reports `SKIP` only when it has nothing to look at: no staged file of its kind, GitNexus without a local
index or `node`, mutation without a scope. `SKIP=<gate>` leaves a gate out on purpose.

**GPU requirement.** Every gate that runs `tests/integration`, `tests/e2e` or `tests/cluster` (`snapshots`,
`tests-fast`, `tests`, `e2e-qdrant`, `memray`, `mutation`) needs an NVIDIA GPU with driver R580 or newer
and about 4.1 GiB of free GPU memory per pytest process (embedder and reranker, plus the same again for the
standalone test's server process). The embedder and reranker tests run the real TensorRT engines;
there is no CPU path, no stub and no skip. Each of these suites starts with a preflight that ends the pytest
run with exit code 2 and the cause when no GPU is visible, TensorRT cannot be imported, or `-n` (pytest-xdist, if
installed) asks for more pytest workers than the GPU memory holds.

**Qdrant requirement.** The integration and end-to-end suites run on a real Qdrant node and the cluster suite on three
nodes, all started through Docker from `qdrant/qdrant:v1.19.2` (`tests/qdrant_support.py` does the starting and removes
the containers). The preflight pulls the image when it is not local, so a clean or pruned runner needs no manual step
but needs the registry (a pull may take up to half an hour). A run that cannot start Qdrant, because Docker is missing or
unreachable or the pull fails, ends with exit code 2 and the cause, it never skips.
Setting `AXMEM_TEST_QDRANT_URL` (and `AXMEM_TEST_QDRANT_API_KEY`) points the integration and end-to-end suites at a server that
already runs, for example a Qdrant you started yourself; the tests then keep to collections of their own under a
unique prefix and delete them afterwards. The single node keeps its data in memory (`--tmpfs`), because payload-index creation fsyncs and a slow Docker storage driver turned
each test's setup into seconds; the cluster nodes use the container disk, since their tests stop and start nodes with their data.
The cluster suite always starts its own three nodes. These gates do not report `SKIP` without a GPU or without Docker:
they fail. See
[GPU models](gpu-models.md) for the host requirements. The static gates (ruff, imports, bandit, semgrep,
vulture, radon, mypy, pylint) and `tests/architecture` need no GPU.

| Stage | Pipeline |
|---|---|
| `pre-commit` (staged files) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi → mypy, pylint → snapshots, tests-fast → gitnexus |
| `pre-push` (whole project) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi, trivy → mypy, pylint → tests → e2e-qdrant → coverage → memray → qodana → mutation (opt-in) → gitnexus |
| `ci` (the gates hosted CI runs) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi, trivy → mypy, pylint |

`pre-push` puts the cheap gates first (trivy needs a few seconds and the network, the suites need minutes), so a
finding there refuses the push before the GPU suites start. `coverage` comes after `tests` and `e2e-qdrant`, whose
data it reads, and `gitnexus` last.

`pre-commit` scopes ruff, bandit, semgrep, radon and pylint to the staged files. mypy, vulture,
import-linter and the tests always run over the whole project (a changed signature breaks unchanged
callers) but are skipped when nothing relevant is staged. `QUALITY_FULL=1` makes `pre-commit` check the
whole project.

## Where the gates run

Hosted CI runs on GitHub's Ubuntu runners and has neither an NVIDIA GPU nor a self-hosted runner. It runs the gates by
name, split across jobs (`stage ci` runs the same set in one go on a developer machine):

| Workflow and job | What runs |
|---|---|
| `quality.yml` job `static` | ruff, imports, bandit, semgrep (with `p/python p/security-audit` added), vulture, radon-cc, radon-mi, mypy, pylint |
| `quality.yml` job `trivy` | `trivy`, with the pinned release binary downloaded and checked against its SHA-256 (no Docker image) |
| `ci.yml` job `lint` | `tests/architecture` (the import contracts, the rule proofs and the rules of these workflow files) and ruff |
| `ci.yml` job `smoke-test` | wheel build smoke test: the locked dependencies (`uv sync --locked --no-dev --no-install-project`), then the wheel on top (`uv pip install --no-deps`), then `axmem --help` from another directory; a second step resolves the wheel's own metadata, where the TensorRT packages have no index and PyPI only holds placeholders, with the `--extra-index-url https://pypi.nvidia.com` that the README documents (`uv pip compile`, nothing is downloaded) and checks that the three packages come from NVIDIA's index, the libraries as a wheel |
| `publish.yml` job `publish` | builds the package and publishes it to PyPI when a release is published |

Every `uv sync` of these workflows uses `--locked` and leaves out `tensorrt-cu13-libs` (`--no-install-package tensorrt-cu13-libs`), the
3.8 GB of TensorRT libraries that no hosted check loads; `tensorrt-cu13`, `tensorrt-cu13-bindings` and `cuda-python`
(small) are installed, and mypy and pylint pass with them. These jobs were run in a clean container with no GPU and no
Docker, from the workflow steps themselves.

`tests/architecture/test_workflows.py` fails when a workflow breaks the rules: every job on a hosted `ubuntu-*`
runner, no container or service, no schedule, no `pull_request_target`; in `ci.yml` and `quality.yml` no secret, a
read-only token and `persist-credentials: false` on every checkout, only the gates above by name (never `tests`, `e2e-qdrant`,
`coverage`, `memray`, `qodana`, `mutation`), the environment installed without the TensorRT libraries, a concurrency
group of `${{ github.workflow }}-${{ github.ref }}` that cancels the older run of the same ref, and the trivy version in
`quality.yml` equal to `TRIVY_VERSION` in `scripts/quality/security.py`. Because a job runs on an ephemeral hosted
runner with a read-only token and no secret, a pull request from a fork needs no special handling. Set the repository to
require approval for all outside contributors (Settings, Actions, General): that setting is not in the repository.

CI does not run the tests, so a green CI proves the static gates only. The tests are proven by the pre-push hook of
the machine that pushed, and a push made with `git push --no-verify` or with `SKIP=` skipped that proof.

### The pre-push hook is the GPU gate

`git push` runs `.githooks/pre-push`, which runs `uv run --frozen python -m scripts.quality stage pre-push`. The push
is refused (the hook exits non-zero, git sends nothing) when:

* any gate of the pipeline fails, and the gates after it do not run;
* the machine has no usable NVIDIA GPU, or no Docker daemon that can start Qdrant: `tests`, `e2e-qdrant` and
  `memray` run suites whose `conftest.py` ends the pytest run with exit code 2 and the cause, and `e2e-qdrant` checks
  the daemon itself; none of them skips;
* trivy, Qodana or semgrep cannot run because their tool is missing;
* `uv` is missing;
* the pushed commit is not the checked-out `HEAD`: the gates check the working tree, so they cannot vouch for another
  commit. Check the branch out and push it from there. Annotated tags are looked through to their commit.

The gates read the working tree, not the commit, so commit your changes first: an uncommitted edit to a tracked file
is tested as if it were pushed. `tests/integration/test_pre_push_hook.py` runs real `git push` commands against a
bare repository with a stand-in `uv` (a passing and a failing pipeline, no `uv`, another branch, a tag, a deletion);
`tests/integration/test_pre_push_pipeline.py` proves the order, that each gate failing refuses the push, and that a
missing tool fails its gate; `test_gpu_preflight.py` and `test_qdrant_preflight.py` run the real suites without a GPU
and without Docker and check that each ends the run.

## Gates

| Gate | What it enforces | Config |
|---|---|---|
| `ruff` | lint (existing rule set) | `ruff.toml` |
| `imports` | layer contracts routes → services → protocols → repositories | `[tool.importlinter]` |
| `bandit` | security lint on app/main/harness/scripts | `[tool.bandit]` |
| `semgrep` | repo-specific rules (no `print` on the MCP stdout path, deprecated asyncio, Qdrant writes outside `store.py` or without `wait`, …); `SEMGREP_PACKS="p/python p/security-audit"` adds registry packs. Uses `semgrep` on `PATH`, else `uvx`, else the pinned Docker image; fails when none can run | `.semgrep/axmem.yml`, `.semgrepignore` |
| `vulture` | dead code, min confidence 60 | `[tool.vulture]`, `vulture_whitelist.py` |
| `radon-cc` | no function/method rated C or worse (minimum rating B), tests included | `radon.cfg` |
| `radon-mi` | every file maintainability grade A (score above 19), tests included | `radon.cfg` |
| `mypy` | type check of `app`, `main.py`, `scripts` | `[tool.mypy]` |
| `pylint` | score at least 8/10 | `[tool.pylint]` |
| `snapshots` | syrupy snapshot tests (`-m snapshot`): CLI argument tree, settings defaults, Qdrant collection layout and payload types, MCP meta-tool signatures and tool catalogue, HTTP route table | `tests/integration/test_snapshots.py`, `tests/e2e/test_snapshots_e2e.py` |
| `tests-fast` | `tests/architecture` and `tests/integration`, stopping at the first failure | `pytest.ini` |
| `tests` | architecture + integration + end-to-end on one Qdrant node with branch coverage (erases previous coverage data first) | `[tool.coverage.*]` |
| `e2e-qdrant` | `tests/cluster` (`-m cluster`) against a real three-node Qdrant cluster (replication factor 3, write consistency 2, strong ordering, API key and internal authentication), appending to the coverage data (fails without a GPU or without Docker) | `tests/cluster/conftest.py`, `tests/qdrant_support.py` |
| `coverage` | combined **100 %** line and branch coverage of `app` and `main.py`, from the single-node and cluster runs together | `[tool.coverage.report]` |
| `memray` | memory budgets (`@pytest.mark.limit_memory`) via pytest-memray, run as `pytest tests/e2e -m memory --memray` (Linux/macOS only) | `tests/e2e/test_memory_budget_e2e.py` |
| `trivy` | HIGH/CRITICAL vulnerabilities (fixed ones), secrets and misconfigurations (`fs` scan); the binary named by `TRIVY_BIN` or on `PATH`, else the pinned Docker image; fails when neither is available | `trivy.yaml`, `.trivyignore` |
| `qodana` | Qodana Ultimate Plus (`jetbrains/qodana-python`), token from `QODANA_TOKEN`; analyses a `git archive` export of `HEAD` (uncommitted changes are not seen) with `--image`; fails when the `qodana` CLI or the token is missing | `qodana.yaml` |
| `mutation` | mutmut on changed `app/` modules; survivors above `MUTATION_MAX_SURVIVORS` fail. Opt-in (`MUTATION=1`), see below | `[tool.mutmut]` |
| `gitnexus` | `detect-changes` on the staged diff (pre-commit) or against `origin/main` (pre-push); a partial or truncated answer is retried once, then fails. Skipped without a local index or `node` (local tooling a push does not depend on) | `.gitnexus/` (local index) |

`radon cc . -a -nc` and `radon mi . -s` work from the repository root and report the same files the
gates check (`radon.cfg` excludes virtualenvs and generated paths).

### Coverage

`tests` collects branch coverage from `tests/architecture`, `tests/integration` and `tests/e2e`;
`e2e-qdrant` appends the cluster run; `coverage` then fails below 100 % for the two together.
Every Qdrant path the cluster suite alone reaches (failover between nodes, the per-node check) is meant to be covered
by single-node tests with injected faults too, so the cluster suite is a second proof of them; whatever only
the cluster run covers is left untested without it, so `coverage` fails too, because untested code cannot be called covered.
Without Docker `e2e-qdrant` itself fails.
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
| CLI argument tree, settings defaults (built from code defaults, ignoring the environment and dotenv files; credentials, the fields whose name ends in `KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `CLIENT_ID` or `HASHES`, and the build-derived `SERVICE_VERSION` are masked), the layout of the Qdrant collections (vectors, shards, replicas, payload indexes) and the types of the stored payload | `tests/integration/test_snapshots.py` | `tests/integration/__snapshots__/test_snapshots/` |
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
and leaves `app/_version.py` unmutated. The tests run in a sandbox (`mutants/`) that holds only `app/`, `tests/` and the
`also_copy` paths; a selected test that reads another path from the repository root fails there and takes mutmut's baseline
run with it, which is why `.githooks/`, `.github/` and `.env.example` are copied and why
`tests/architecture/test_mutation_sandbox.py` checks every root path the selected tests name. A test that lists the
members of a class under `app/` must skip the names containing `__mutmut_` (mutmut adds its originals and mutants to a
mutated class, see `protocol_methods` in `tests/integration/test_tenant_chokepoint.py`). No workflow runs the gate: the
tests it selects need the GPU and Docker, so it is an opt-in gate of the pre-push hook (`MUTATION=1`). On a checkout of
`main` there is no diff against `origin/main`, so give it a scope of its own, for example `MUTATION=1
MUTATION_TARGETS='app.utils.* app.config.*' MUTATION_MAX_SURVIVORS=50`, and lower the tolerated survivors as mutants
get killed.

## Secrets

`QODANA_TOKEN` goes into the git-ignored dotenv file in the repository root (`.env.example` lists the
key name, commented out, because an empty value counts as set) or the environment. The runner reads only the keys a gate declares, takes the process
environment first, and never prints them. Qodana runs only on the developer machine and no workflow reads a
secret. Do not put the token in `qodana.yaml`.

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
network), `TRIVY_BIN` (explicit trivy binary; otherwise `PATH`, then the Docker image `aquasec/trivy:<TRIVY_VERSION>`,
pinned in `scripts/quality/security.py` like semgrep and Qodana) and
`MUTATION_MAX_SURVIVORS`.

A gate left out with `SKIP` is a decision to record in the PR description, not a default, and `git push
--no-verify` skips the whole hook. A gate whose tool is missing (for example no Docker for `e2e-qdrant`) fails with the
reason; leaving it out with `SKIP=e2e-qdrant` makes the `coverage` gate fail if code only that gate reaches is left
untested.

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
* New semgrep rules live in `.semgrep/axmem.yml` (rule ids are prefixed `axmem-`).
* Fix a bandit or semgrep finding when the code can be made safe (Qdrant writes go through
  `QdrantStore` only). Suppress only code that is safe
  by construction, inline and with the reason on the same line: `# nosec B603 - argv list from
  constants, no shell` or `# nosemgrep: <rule-id> - reason`. Keep the matching `ruff.toml`
  per-file `S` ignore in step, and never add file-level skips to `[tool.bandit]`.
* The same convention applies to the other linters: `# noqa: <code> - reason` for ruff,
  `# type: ignore[<code>]` with a reason for mypy (`warn_unused_ignores` flags stale ones). The code base
  uses no inline pylint disables; a noisy check is disabled once, with a comment, in
  `[tool.pylint."messages control"]`. The members of the native modules `tensorrt` and `cuda.*` are not checked
  (`ignored-modules` in `[tool.pylint.typecheck]`, the counterpart of the mypy override): they are filled in at
  import, so with them installed pylint called every attribute missing and a commit of a few files that touched
  `tensorrt_runtime.py` fell below the 8/10 score on errors that were never real.
* Bump trivy in three places together: `TRIVY_VERSION` in `scripts/quality/security.py` (the Docker image tag follows
  it), and `TRIVY_VERSION` and `TRIVY_SHA256` in the `trivy` job of `quality.yml` (the hash of
  `trivy_<version>_Linux-64bit.tar.gz` is in `trivy_<version>_checksums.txt` of the release). A test fails when the
  version of the workflow and of the gate differ. The vulnerability database is fetched on every run, so a new advisory
  against a locked dependency fails `trivy` without a code change: bump the dependency (`uv lock --upgrade-package <name>`),
  do not ignore the advisory.
* Raise the Python version (`quality.yml`, `ci.yml`, `pyproject.toml`
  `requires-python`, classifiers and the mypy, pylint and ruff targets, `.python-version`, `flake.nix`,
  the test harness Dockerfile) together.
