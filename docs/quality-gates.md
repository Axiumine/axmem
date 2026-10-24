# Quality gates

Every check below runs from one orchestrator, `scripts/quality` (`python -m scripts.quality`),
so Git hooks, CI and a developer shell execute identical commands. Tool versions are pinned in
`pyproject.toml` (`dev` dependency group, locked in `uv.lock`); semgrep, trivy and Qodana run
isolated because their dependency trees conflict with the application pins.

**Every check is a gate, and every gate runs from a hook.** A check that guards the project is a named gate in
`scripts/quality/registry.py`, and the `pre-commit` and `pre-push` hooks reach it through `stage <name>`. None
exists only as a command to remember, as a step of a workflow or as a line of a script that no hook runs. A new check
is added as a gate, with its tool pinned in the `dev` group, and to a stage in the same change. The flake defines no
check of its own (see [Nix](nix.md)): `.githooks/` is the only mechanism.

Two things are not run by every hook, on purpose, and each says so when it is left out: `gitnexus` reports `SKIP` on a
clone without a local GitNexus index or `node`, because that index is local tooling that no clone has until it builds
one; and the checks of a release (tag shape, digests, cosign identity,
the release guard) need a release and its signed files, so `scripts/release-pypi.sh` and the release workflows run them,
with their code covered by the release tests that `tests` runs.

**Where they run.** Hosted CI has no GPU, and the project has no self-hosted runner, so CI runs only the gates that
need neither a GPU nor Docker (the static gates, trivy, the architecture tests, the package and the wheel), and it
runs them by their gate names: it adds nothing the hooks do not run. Everything that runs the real TensorRT embedder
and reranker or a real Qdrant (tests, the Qdrant cluster suite, coverage, memory budgets, Qodana, mutation) runs on the
developer machine, in the `pre-push` Git hook. See [Where the gates run](#where-the-gates-run).

```bash
uv sync --locked --all-extras --dev          # install the toolchain
uv run python -m scripts.quality install     # enable .githooks/ (sets core.hooksPath)
uv run python -m scripts.quality list        # gates and stage pipelines
uv run python -m scripts.quality gate mypy   # run one gate on the whole project
uv run python -m scripts.quality gate ruff --staged
uv run python -m scripts.quality gate ruff --files app/bootstrap.py main.py
uv run python -m scripts.quality stage pre-commit   # what the pre-commit hook runs
uv run python -m scripts.quality wheel-check dist   # the checks of the wheel gate, on a dist/ built elsewhere
uv run python -m scripts.quality uninstall   # unset core.hooksPath
```

The `dev` group also holds the command-line tools of the `shellcheck`, `actionlint` and `wheel` gates (`shellcheck-py`,
`actionlint-py`, `twine`, `check-wheel-contents`), so the sync above installs them and the gates take them from the
environment, never from a global install or from `uvx`: a hand run and the hook use the same version. Sync the project
environment with `--all-extras --dev` and without `--no-install-package`: the hosted workflows leave out
`tensorrt-cu13-libs` (3.8 GB that no hosted check loads), but a sync of `.venv` that does would remove the GPU libraries.
The hooks run `uv run --frozen`, which syncs a newly pinned tool into `.venv` the first time and keeps the rest.

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
| `pre-commit` | stage `pre-commit` on the staged files, and `denylist-check` | yes |
| `pre-push` | stage `pre-push` on the whole project, and `denylist-check`: the one place that runs the GPU, Docker and Qdrant gates. It gates the pushed commit only when that is the checked-out `HEAD` (the gates read the working tree), so a push of any other commit is refused and not passed unchecked; deleting a remote ref has nothing to check and goes through | yes |
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

`commit-msg` and `denylist-check` are stage-less: they read the message or the names of the commits, and the guard
must not be bypassable with `SKIP` or `ONLY`, so neither is run by `scripts.quality stage` and neither is part of the
pipeline below. The `shellcheck` gate lints both, and every other hook, with the shell scripts. Comment lines and the `git commit -v` scissors section are stripped
before checking, and merge, revert and `fixup!`/`squash!`/`amend!` commits skip the format check. AI
`Co-Authored-By` trailers and "Generated with" lines are rejected; mark AI-written commits with the `🤖`
subject prefix instead (covered by `tests/integration/test_commit_msg_hook.py`).

## Pipeline

Gates in the same group run in parallel; groups run left to right and the first failing group
stops the stage (cheapest checks first). A gate whose tool or daemon is missing (trivy with neither a binary
nor Docker, semgrep with neither `semgrep`, `uvx` nor Docker, shellcheck, actionlint, twine or check-wheel-contents
absent from the environment, no `uv` for the wheel gate, the cluster suite without Docker, Qodana without
its CLI) **fails** with the cause, so a push that these gates guard cannot pass because the machine cannot run one.
A gate reports `SKIP` only when it has nothing to look at: no staged file of its kind (no staged shell script for
`shellcheck`, no staged workflow for `actionlint`, none that the architecture rules read for `architecture`), GitNexus without a local
index or `node`, mutation when the push changes no `app/` module and `MUTATION_TARGETS` is not set. A gate that raises instead of returning fails, with the traceback, and the other gates of its
group still report. `SKIP=<gate>` leaves a gate out on purpose: the stage prints a `SKIP` line for every gate that `SKIP` or `ONLY`
left out and ends `OK (N gates left out)`, so a pipeline that was emptied does not look like a passed one.
A name in `SKIP` or `ONLY` that is no gate, and an `ONLY` that selects no gate of the stage, end the run with exit code 2.

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
each test's setup into seconds; the nodes of the shared cluster use the container disk, since their tests stop and start nodes with their data (the four-node and the second cluster of the restore tests keep theirs in memory, since no test there stops a node).
The cluster suite always starts its own nodes: three for the shared cluster, and a four-node cluster and a second three-node cluster for the restore tests of `tests/cluster/test_cluster_backup_restore_other_clusters.py`. These gates do not report `SKIP` without a GPU or without Docker: The `e2e-qdrant` gate gets 3 hours instead of the 30 every other gate gets (`E2E_QDRANT_TIMEOUT_SECONDS` in `scripts/quality/testing.py`), because the suite starts three clusters and waits for their replicas.
they fail. See
[GPU models](gpu-models.md) for the host requirements. The static gates (ruff, imports, bandit, semgrep,
vulture, radon, shellcheck, actionlint, mypy, pylint), `tests/architecture`, `package` and `wheel` need no GPU.

| Stage | Pipeline |
|---|---|
| `pre-commit` (staged files) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi, shellcheck, actionlint → mypy, pylint, architecture, package → snapshots, tests-fast → gitnexus |
| `pre-push` (whole project) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi, shellcheck, actionlint, trivy → mypy, pylint, architecture, package, wheel → tests → e2e-qdrant → coverage → memray → qodana → mutation → gitnexus |
| `ci` (the gates hosted CI runs) | ruff, imports, bandit, semgrep, vulture, radon-cc, radon-mi, shellcheck, actionlint, trivy → mypy, pylint, architecture, package, wheel |

`pre-push` puts the cheap gates first (trivy needs a few seconds and the network, the suites need minutes), so a
finding there refuses the push before the GPU suites start. `coverage` comes after `tests` and `e2e-qdrant`, whose
data it reads, and `gitnexus` last.

`wheel` is not in `pre-commit`: it installs the locked dependencies, which needs PyPI and NVIDIA's index, so a commit
made offline must not fail on it. `package`, which is in `pre-commit`, runs `uv build`, which fetches the pinned build
requirements (`hatchling`, `hatch-vcs`) unless the uv cache holds them: a first commit with an empty cache needs PyPI. It runs in `pre-push` and in `ci`, where the network is a given, and fails (never
skips) without it. A commit that stages only a workflow, a hook, `pyproject.toml` or `uv.lock` still meets the
`architecture` gate, which runs when a staged file is one that the architecture tests read (`app/`, `scripts/`,
`tests/architecture/`, `.github/`, `.githooks/`, `test_harness/docker/`, `main.py`, `pyproject.toml`, `uv.lock`,
`architecture.md`, `.python-version`, `flake.nix`, `tests/integration/`: `test_mutation_sandbox.py` reads the integration tests).
`tests-fast` also runs when a staged file is one that the integration tests read besides code (`docs/`, `README.md`,
`CHANGELOG.md`, `.env.example`, `release-superseded.json`, `.github/`), so a docs-only commit meets the reference-document
tests at the commit and not first at the push. A commit that only deletes files is not an empty one: the removed paths count
for the gates over the whole project (mypy, vulture, import-linter, the tests, shellcheck, actionlint), and nothing is linted
in a file that is gone.

`pre-commit` scopes ruff, bandit, semgrep, radon and pylint to the staged files, `shellcheck` and `actionlint` to the
shell scripts and the workflows (they lint every one of them when one is staged: `-x` follows `source` into the others,
and it takes under a second). mypy, vulture,
import-linter and the tests always run over the whole project (a changed signature breaks unchanged
callers) but are skipped when nothing relevant is staged. `QUALITY_FULL=1` makes `pre-commit` check the
whole project. So does staging the configuration of a gate (`pyproject.toml`, `uv.lock`, `ruff.toml`, `radon.cfg`,
`pytest.ini`, `vulture_whitelist.py`, `trivy.yaml`, `.trivyignore`, `.semgrepignore`, `.shellcheckrc`, anything under
`.semgrep/`): a changed rule or pin can fail a file that is not staged, and a scoped gate would report `SKIP`.
Semgrep keeps to its local rules in a scoped `pre-commit` run (the registry packs need the network) and runs the
packs in every other run.

## Where the gates run

Hosted CI runs on GitHub's Ubuntu runners and has neither an NVIDIA GPU nor a self-hosted runner. It runs the gates by
name, split across jobs (`stage ci` runs the same set in one go on a developer machine):

| Workflow and job | What runs |
|---|---|
| `quality.yml` job `static` | ruff, imports, bandit, semgrep (with `p/python p/security-audit`, the default of the gate), shellcheck, actionlint, vulture, radon-cc, radon-mi, mypy, pylint |
| `quality.yml` job `trivy` | `trivy`, with the pinned release binary downloaded and checked against its SHA-256 (no Docker image) |
| `ci.yml` job `lint` | the `architecture` gate (`tests/architecture`: the import contracts, the rule proofs and the rules of these workflow files) and the `ruff` gate |
| `ci.yml` job `smoke-test` | the `package` gate and the `wheel` gate, see [The wheel gate](#the-wheel-gate) |
| `publish.yml` job `verify` | on a dispatch on a `v<version>` tag: checks the signed files of the GitHub release again (digests, cosign identity of `release.yml`, the release guard) and runs `dist-check` on them (a package that holds model weights, engines, native libraries or other files than source is refused), before the job `publish` uploads them to PyPI; see [release.md](release.md) |
| `release.yml` jobs `sign-artefacts` and `provenance-artefacts` | on a `v<version>` tag: rebuilds the package from the committed `release-digests.json`, refuses to sign unless the rebuild is byte-identical, runs `dist-check`, signs each file with cosign (keyless) and attests them (SLSA-3); started by `scripts/release-pypi.sh`, see [release.md](release.md) |

A workflow step calls a gate by its name (`uv run --frozen python -m scripts.quality gate <name>`) and holds no check
of its own: a command that guards the project is a gate first. `tests/architecture/test_workflows.py` fails when a gate
of the `ci` stage is missing from the workflows. The only other commands in `ci.yml` and `quality.yml` install a tool
(`setup-uv`, `uv sync`, the pinned trivy binary); the checks of `release.yml` and `publish.yml` (tag shape, digests,
cosign identity, the release guard) need a release and its signed files, so they are not hook gates, and `actionlint`
and `shellcheck` lint those workflows too.

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
| `semgrep` | repo-specific rules (no `print` on the MCP stdout path, deprecated asyncio, Qdrant writes outside `store.py` or without `wait`, …) and the registry packs `p/python p/security-audit` (`SEMGREP_PACKS` in `scripts/quality/static.py`; they need the network, so a scoped `pre-commit` run keeps to the local rules; `SEMGREP_PACKS` in the environment replaces them, empty leaves them out). Uses `semgrep` on `PATH`, else `uvx`, else the pinned Docker image; fails when none can run | `.semgrep/axmem.yml`, `.semgrepignore` |
| `shellcheck` | `shellcheck -x` with `SHELLCHECK_OPTS` emptied (`shellcheck-py`, pinned in the `dev` group) over every shell script the project holds: the files named `*.sh` and the files whose first line names `sh`, `bash`, `dash` or `ksh`, tracked or untracked and not ignored (the files of `.githooks/`, `scripts/*.sh`, `scripts/lib/*.sh`, the fake tools of `tests/release_support/fake_bin/`); fails when the binary is missing from the environment, or when the interpreter is not in a virtual environment (a system `bin/` would hold unpinned tools) | `.shellcheckrc` (it holds no option; its presence stops the search for a user-level rc file) |
| `actionlint` | `actionlint` (`actionlint-py`, pinned in the `dev` group) over `.github/workflows/*.yml`: expressions, contexts, the job and step schema, and the `run:` blocks through the same pinned shellcheck (the gate puts the `bin/` of the environment first on `PATH` and passes `-shellcheck`, because actionlint lints `run:` blocks only with a shellcheck it finds); fails when a binary is missing. `actionlint-py` is an sdist in `uv.lock`: the first `uv sync` builds it and it downloads the pinned `actionlint` binary from github.com (checked against the SHA-256 in the sdist), so a sync needs GitHub as well as PyPI | `.github/actionlint.yaml` (optional) |
| `vulture` | dead code, min confidence 60 | `[tool.vulture]`, `vulture_whitelist.py` |
| `radon-cc` | no function/method rated C or worse (minimum rating B), tests included | `radon.cfg` |
| `radon-mi` | every file maintainability grade A (score above 19), tests included | `radon.cfg` |
| `mypy` | type check of `app`, `main.py`, `scripts` | `[tool.mypy]` |
| `pylint` | score at least 8/10 | `[tool.pylint]` |
| `architecture` | `tests/architecture`: the import contracts and the proofs of their rules, the rules of the workflow files, the shape of the release workflows; no GPU and no Docker | `[tool.importlinter]`, `tests/architecture/` |
| `package` | the wheel and the sdist built from this checkout hold source only, see [Package contents](#package-contents) | `scripts/quality/package.py`, `[tool.hatch.build.targets.*]` |
| `wheel` | the built wheel installs on the locked dependencies, starts and resolves, see [The wheel gate](#the-wheel-gate) | `scripts/quality/wheel.py` |
| `snapshots` | syrupy snapshot tests (`-m snapshot`): CLI argument tree, settings defaults, Qdrant collection layout and payload types, MCP meta-tool signatures and tool catalogue, HTTP route table | `tests/integration/test_snapshots.py`, `tests/e2e/test_snapshots_e2e.py` |
| `tests-fast` | `tests/integration`, stopping at the first failure (the architecture tests are the `architecture` gate) | `pytest.ini` |
| `tests` | architecture + integration + end-to-end on one Qdrant node with branch coverage (erases previous coverage data first) | `[tool.coverage.*]` |
| `e2e-qdrant` | `tests/cluster` (`-m cluster`) against a real three-node Qdrant cluster (replication factor 3, write consistency 2, strong ordering, API key and internal authentication; the restore tests also start a four-node and a second three-node cluster), appending to the coverage data (fails without a GPU or without Docker) | `tests/cluster/conftest.py`, `tests/qdrant_support.py` |
| `coverage` | combined **100 %** line and branch coverage of `app` and `main.py`, from the single-node and cluster runs together | `[tool.coverage.report]` |
| `memray` | memory budgets (`@pytest.mark.limit_memory`) via pytest-memray, run as `pytest tests/e2e -m memory --memray` (Linux/macOS only) | `tests/e2e/test_memory_budget_e2e.py` |
| `trivy` | HIGH/CRITICAL vulnerabilities (fixed ones), secrets and misconfigurations (`fs` scan); the binary named by `TRIVY_BIN` or on `PATH`, else the pinned Docker image; fails when neither is available | `trivy.yaml`, `.trivyignore` |
| `qodana` | Qodana Ultimate Plus (`jetbrains/qodana-python`), token from `QODANA_TOKEN`; analyses a `git archive` export of `HEAD` (uncommitted changes are not seen) with `--image`; fails when the `qodana` CLI or the token is missing | `qodana.yaml` |
| `mutation` | mutmut on changed `app/` modules; survivors above `MUTATION_MAX_SURVIVORS` fail; one run may take 24 h (`MUTATION_TIMEOUT_S`). Runs in every `pre-push`, see below | `[tool.mutmut]` |
| `gitnexus` | `detect-changes` on the staged diff (pre-commit) or against the upstream of the branch (pre-push; `origin/main`, then `HEAD~1`, without one); a partial or truncated answer is retried once, then fails. Skipped without a local index or `node` (local tooling a push does not depend on) | `.gitnexus/` (local index) |

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

### Package contents

The AGPL release is Axmem's Python source. The model weights, the TensorRT engines built from them and the NVIDIA
libraries have licenses of their own ([GPU models](gpu-models.md#licenses-of-the-models-and-libraries)): the user fetches
them, and none of them may be in a package. The `package` gate builds the wheel and the sdist with `uv build` in a copy of the
files a build picks up (`git ls-files --cached --others --exclude-standard`: the tracked files and the untracked ones git does
not ignore, so a model file left in `app/` is in the copy and the repository stays free of the version file the build
writes; `SETUPTOOLS_SCM_PRETEND_VERSION` stands in for the git history that the copy lacks), then holds both to an allow-list:

- under `app/` and `main.py` only `.py` files, each at most 256 KiB, none with a NUL byte (a weights file renamed to `.py`);
  anything else is named for what it is when its name says so (model weights: `.onnx`, `.safetensors`, `.pt`, `.bin`, `.gguf`
  and others; a TensorRT engine: `.engine`, `.plan`, `.trt`; a native library: `.so`, `.so.N`, `.dll`, `.dylib`, `.pyd`;
  a tokenizer or model file such as `tokenizer.json`, `vocab.txt`, `manifest.json`) and otherwise as "not Python source";
- in the wheel, nothing outside `app/`, `main.py` and the `.dist-info` directory (`METADATA`, `WHEEL`, `RECORD`,
  `entry_points.txt`, `licenses/*`), and `Root-Is-Purelib: true` with `-none-any` tags, so no native library can be inside;
- in the sdist, only the top-level names that `[tool.hatch.build.targets.sdist] include` lists, plus `PKG-INFO`,
  `pyproject.toml` and `.gitignore` that hatchling writes;
- each artefact at most 2 MiB; the largest file of the source tree is 35 KB and the wheel is about 250 KB;
- `License-Expression` and `License-File` in `METADATA` or `PKG-INFO` equal `license` and `license-files` of
  `pyproject.toml`, and both license files are in the artefact.

`MAX_MEMBER_BYTES` and `MAX_ARTEFACT_BYTES` in `scripts/quality/package.py` are the caps: raise one on purpose when the source
grows, never to let a model through. A new kind of file in the package, `py.typed` for example, is refused until the
allow-list in the same module names it: say why in the commit.

`pre-commit` runs the gate when a staged file is `app/`, `main.py`, `pyproject.toml`, `README.md`, a license file or the gate
itself (otherwise it reports `SKIP`); `pre-push` and the `ci` stage always run it. The `smoke-test` job of `ci.yml` runs it
after the locked environment is installed, and `publish.yml` runs `python -m scripts.quality dist-check dist` on the
signed files of the release before the upload to PyPI, which cannot be taken back. `dist-check <dir>` checks any directory
that holds one wheel and one sdist, for example a package you built by hand before you upload it.

### The wheel gate

`package` reads what the wheel holds; `wheel` uses it, the way a user does. Everything happens in a temporary directory,
never in `dist/` (which the release flow checks and signs) and never in `.venv`: the gate refuses an environment inside
the repository. It builds the package as `package` does, then stops at the first step that fails:

1. `uv lock --check`: `uv.lock` is current with `pyproject.toml` (the hooks run `uv run --frozen`, which never checks it);
2. `twine check` on the wheel and the sdist (metadata and the README as it renders), `twine` pinned in the `dev` group;
3. `check-wheel-contents` on the wheel, `check-wheel-contents` pinned in the `dev` group, with `W005` and `W009` ignored
   (`WHEEL_IGNORE` in `scripts/quality/wheel.py`, with the reason: the top-level package is called `app`, and the wheel holds
   the package and the `main.py` module that the console script points at);
4. a clean environment: the locked dependencies without the dev group, the project and `tensorrt-cu13-libs`
   (`uv sync --locked`, so the TensorRT packages come from NVIDIA's index as `uv.lock` records), the wheel on top
   (`uv pip install --no-deps`), then `axmem --help` and `import main` from another directory, in isolated mode, so the
   installed wheel is what runs and not `app/` of the checkout;
5. the resolution of the wheel's own metadata with the extra index that the README documents (`uv pip compile`,
   nothing is downloaded): the wheel names the TensorRT packages without an index and PyPI holds placeholders for two of
   them, so the three packages must come from NVIDIA's index and the libraries as a wheel. The index URL and the package
   names are read from `[tool.uv.index]` and `[tool.uv.sources]`.

The gate needs the network (PyPI and `pypi.nvidia.com`) and `uv`, and fails without them. It runs in `pre-push`, in the
`ci` stage and in the `smoke-test` job of `ci.yml`. `python -m scripts.quality wheel-check <dir>` runs steps 1 to 5 on a
directory that holds one wheel and one sdist built elsewhere; `scripts/check-package.sh` calls it on the `dist/` of a
release, so the release flow and the gate run the same code.

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

`mutation` runs `mutmut` over the modules a change touched, in every `pre-push`. A run over the whole of `app/`
produces roughly 33,000 mutants and is not practical, so the gate always needs a scope:

* the `app/` files that the push adds to the upstream of the branch (the default for `pre-push` and a plain
  `gate mutation`; without an upstream the base is `main` of the remote being pushed to (the hook names it in
  `QUALITY_PUSH_REMOTE`, so the first `git push -u <remote> <branch>` is compared with that remote), then `origin/main`,
  and without that, `HEAD~1`; the `gitnexus` gate of `pre-push` has the same base), or the `app/` files named with `--staged` or `--files`, or
* an explicit `MUTATION_TARGETS`, a space-separated list of mutmut name patterns such as
  `MUTATION_TARGETS='app.utils.*'`, which wins over both.

With no `app/` module in the diff and no `MUTATION_TARGETS` it reports `SKIP`, and the stage says so; a diff that git
cannot make (a root commit has no `HEAD~1`) fails the gate with git's message instead of reading as no change. The gate allows one `mutmut run` 24 hours (`MUTATION_TIMEOUT_S`, see below); surviving mutants in the
targets above `MUTATION_MAX_SURVIVORS` (default 0) fail it. `[tool.mutmut]` selects `tests/architecture` and `tests/integration` (which need Docker for their Qdrant node, like the
other suites) as the tests that must kill a mutant
and leaves `app/_version.py` unmutated. The tests run in a sandbox (`mutants/`) that holds only `app/`, `tests/` and the
`also_copy` paths; a selected test that reads another path from the repository root fails there and takes mutmut's baseline
run with it, which is why `.githooks/`, `.github/`, `.env.example` and `CHANGELOG.md` are copied and why
`tests/architecture/test_mutation_sandbox.py` checks every root path the selected tests name (after `ROOT /`, or as a string that names a file in a test module that finds the root). A test that lists the
members of a class under `app/` must skip the names containing `__mutmut_` (mutmut adds its originals and mutants to a
mutated class, see `protocol_methods` in `tests/integration/test_tenant_chokepoint.py`). A test that starts a Python child outside the sandbox is left out in `[tool.mutmut]` (`--ignore`, `--deselect`): the instrumented `app/` cannot find mutmut's config there. The workers fork from a fork server that has not started CUDA (`process_isolation`) and are at most three (`MUTATION_WORKERS` in `scripts/quality/testing.py`), since each holds the TensorRT engines on the GPU. No workflow runs the gate: the
tests it selects need the GPU and Docker, so it is a gate of the pre-push hook only. On a checkout of
`main` that is level with its upstream the diff is empty, so give it a scope of its own, for example
`MUTATION_TARGETS='app.utils.* app.config.*' MUTATION_MAX_SURVIVORS=50 git push`, and lower the tolerated survivors as
mutants get killed. A push of a long branch can take hours, so the cap of one `mutmut run` is 24 hours by default
(`MUTATION_TIMEOUT_S`, in seconds, changes it: `MUTATION_TIMEOUT_S=172800 git push` allows two days). It must be a positive whole
number up to 2147483 (the longest wait that `subprocess` accepts, about 24 days); any other value fails the gate and names
the variable, and the gate never falls back to the default. A run that reaches the cap fails the push: the gate stops `mutmut`
and the test workers it started and reports `mutation exceeded <cap> s`, with the ways out: a higher `MUTATION_TIMEOUT_S`,
a scope of its own (`MUTATION_TARGETS`), or `SKIP=mutation git push` once, said in the PR description. The first push of a
branch that changes most of `app/` (dozens of modules, tens of thousands of mutants) is the one that can reach it, and a
rerun resumes: the mutants already tested stay in `mutants/` (git-ignored), and a rerun continues from them while the code
and the files the tests read are unchanged; `rm -rf mutants` starts over.

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
MUTATION_TARGETS='app.utils.*' git push   # mutation testing of these modules instead of the push's diff
MUTATION_TARGETS='app.utils.*' uv run python -m scripts.quality gate mutation   # one scoped gate
```

`SKIP` and `ONLY` take comma-separated gate names and apply to stages. `ONLY` runs just those gates of the
stage. `gate mutation` runs the mutation gate on its own. Other knobs: `SEMGREP_PACKS` (the registry packs of semgrep, default `p/python p/security-audit`, needs
network; empty leaves them out), `TRIVY_BIN` (explicit trivy binary; otherwise `PATH`, then the Docker image `aquasec/trivy:<TRIVY_VERSION>`,
pinned in `scripts/quality/security.py` like semgrep and Qodana),
`MUTATION_TARGETS`, `MUTATION_MAX_SURVIVORS` and `MUTATION_TIMEOUT_S` (see Mutation testing), and `QUALITY_PUSH_REMOTE` (the remote of the push,
which the `pre-push` hook sets; the base of a branch without an upstream).

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
* A shellcheck finding is fixed in the script. Where a directive is right, `# shellcheck disable=SC<code>` goes on the
  line above the command with the reason after it (a sourced settings file, `scripts/lib/project.sh`, carries a
  file-level `shell=` and `disable=SC2034` because its variables are read by the files that source it); an
  actionlint finding is fixed in the workflow. Both tools are pinned in the `dev` group: bump `shellcheck-py` or
  `actionlint-py` there (`uv lock --upgrade-package <name>`) and fix what the new version reports in the same change.
* A new check of the project is a new gate: register it in `scripts/quality/registry.py`, put it in the stages that
  reach it (`pre-commit` when it is cheap and works offline, always `pre-push`, and `ci` when it needs neither a GPU nor
  Docker), pin its tool in the `dev` group and add a row to the tables above. A check that only a workflow, a script or a
  person runs is not accepted.
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
