# Contributing to Axmem

Thank you for helping. Axmem is a small project: two owners are listed in [MAINTAINERS.md](MAINTAINERS.md), and the lead
maintainer does the day-to-day work, so replies can take a few days; a short, focused change gets reviewed fastest.

## Hardware

Axmem runs its embedder and reranker with TensorRT on an NVIDIA GPU (driver R580 or newer, Linux). There is no CPU
path and no fallback, by design ([GPU models](docs/gpu-models.md)). That will not change on request, so please do not
open an issue, a discussion or a comment that asks for support of another device (AMD, Intel, Apple silicon, CPU-only),
for Windows, or for "a way to run it without a GPU". Such requests will be closed.

**Apple silicon is the one exception, only as a pull request, and only with [MLX](https://github.com/ml-explore/mlx).**
macOS has no NVIDIA GPU and no CUDA, so a Mac port is a second backend, not a build of the TensorRT one. If you want
Axmem on a Mac, send a pull request with an MLX backend:

* The embedder and the reranker run with MLX on the GPU of an Apple-silicon Mac, natively on macOS: not through a Linux
  virtual machine, a container or an emulation layer. A backend on Core ML, Metal, PyTorch MPS, ONNX Runtime or the CPU
  is not accepted, and neither is an Intel Mac.
* The NVIDIA path on Linux stays as it is and keeps passing its gates.
* Like the NVIDIA path, it runs the real models: no stub, no test-only embedder, no skipped test
  ([testing philosophy](AGENTS.md#testing-philosophy)).
* The pull request says which Mac (chip and memory) and which macOS version it was tested on, and shows the test run.
  Do not add a macOS or self-hosted runner to `.github/workflows`: hosted CI runs only checks that need neither a GPU
  nor Docker, and `tests/architecture/test_workflows.py` fails on anything else.

The maintainer decides whether to merge it. No other platform is planned or considered.

## Ways to contribute

* **Report a bug or ask for a feature** in the [issue tracker](https://github.com/Axiumine/axmem/issues). Say what you
  did, what you expected and what happened, plus the version or commit, your OS, the GPU model and the NVIDIA driver, the
  Qdrant version, and the log lines. Never paste an API key, a token, a Qdrant key or the content of your memories.
* **Report a security problem privately**, not in an issue: [SECURITY.md](SECURITY.md) says how.
* **Send a pull request** for a fix, a test, a doc improvement or a feature. For anything bigger than a small fix, open an
  issue first so we agree on the approach before you spend time on it.
* **Improve the docs**: [README.md](README.md) and [docs/](docs/) are part of the product.

Who decides and who has access is in [GOVERNANCE.md](GOVERNANCE.md); the maintainers are listed in
[MAINTAINERS.md](MAINTAINERS.md); how a change is reviewed is in [Code review](#code-review) below. Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up

You need Python 3.14, [uv](https://docs.astral.sh/uv/), Docker (the tests start a real Qdrant node) and an NVIDIA GPU
with driver R580 or newer: the integration and end-to-end suites run the real TensorRT models and end with an error
when the GPU is unusable.

```bash
git clone https://github.com/Axiumine/axmem.git
cd axmem
uv sync --locked --all-extras --dev
uv run python -m scripts.quality install    # enables the versioned git hooks in .githooks
```

Always run Python through `uv run ...`, never the system `python3`: the project needs Python 3.14. The architecture
overview is [architecture.md](architecture.md); read it before changing code. Testing, CI and the release process are in
the [contributors guide](docs/contributors.md).

## Making a change

1. Fork the repository and branch from `main`.
2. Make the change, with tests: integration and end-to-end tests over unit tests, covering the critical workflows
   ([docs/contributors.md](docs/contributors.md)).
3. Run `uv run python -m scripts.quality gate ruff`, `uv run pytest tests/architecture/` and the tests for the code you touched, for example
   `uv run pytest tests/integration/test_qdrant_archive.py`.
4. Update [CHANGELOG.md](CHANGELOG.md) and [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md) when the
   change is visible to a user, an MCP client or an operator (tools, REST routes, settings, schema, dependencies,
   supported platforms, quality gates), and the page in [docs/](docs/) or the skill in [skills/](skills/) that describes it.
5. Open a pull request against `main` that says what changed and why, and links the issue. A maintainer reviews it as
   described in [Code review](#code-review).

The maintainer runs the slow gates (the full suites on a real Qdrant node and a three-node cluster, 100 % coverage, memory
budgets, Qodana, mutation, which is stopped after 24 hours unless `MUTATION_TIMEOUT_S` says otherwise) from the `pre-push` hook on a machine with an NVIDIA GPU before merging, so a pull request does
not need to pass all of them before review, but it must pass `ruff`, the architecture tests and its own tests. Hosted CI
runs only the static checks on every pull request.

## Rules for code

These are enforced by tools, so a change that breaks them cannot be merged. The full text is in
[docs/quality-gates.md](docs/quality-gates.md).

* **Layering.** routes -> services -> protocols -> repositories/adapters. No integration or implementation detail in the
  service layer (`uv run pytest tests/architecture/` proves the import boundaries).
* **Typing and style.** `mypy`, `ruff` (Python 3.14 built-in generics), `pylint` at 8/10 or better. Complexity is rank B
  at most (`radon`), and the dead-code check (`vulture`) is clean. The style guide behind these tools is in
  [Coding standards](#coding-standards).
* **Tests.** New functionality comes with automated tests in `tests/`, and a bug fix with a test that fails without the fix.
  They are integration or end-to-end tests on the real stack, covering the critical workflows rather than every edge case
  ([testing philosophy](AGENTS.md#testing-philosophy)). A change to code without the tests that prove it is not merged.
* **Coverage.** Line and branch coverage of `app` and `main.py` stays at 100 %.
* **Gates.** Do not weaken a threshold, skip a gate or bypass a hook (`--no-verify`) to make a change pass: fix the code, or
  explain the exception in the pull request.
* **Dependencies.** Do not add or bump a dependency without agreeing it in an issue first. They are pinned in `uv.lock`
  and scanned by `trivy`. The exception is the pull requests that Dependabot opens (its configuration is
  [.github/dependabot.yml](.github/dependabot.yml)): they need no issue, and they are reviewed and must pass the same
  checks as any other change. How a vulnerability or a license of a dependency is handled is in
  [Dependency findings](docs/quality-gates.md#dependency-findings).
* **Package contents.** The wheel and the sdist hold Axmem's source and nothing else. Model weights, TensorRT engines and
  the NVIDIA libraries have licenses of their own and are never added to the repository or to a package (the `package` gate
  refuses them).

### Coding standards

Python code follows [PEP 8](https://peps.python.org/pep-0008/) (names, layout, imports), carries
[PEP 484](https://peps.python.org/pep-0484/) type hints on the signatures it adds or changes, and uses the Python 3.14
idioms that `ruff`'s `UP` rules check (built-in generics such as `list[str]`, not `typing.List`). The tools state what is
enforced, and the configuration of the tools wins where it differs from the guides:

* `ruff` ([ruff.toml](ruff.toml), target `py314`): 54 rule groups are on, among them pycodestyle (`E`, `W`: lines of at most
  140 characters), pep8-naming, bugbear and security (`S`). A few rules inside them (`TRY003`, `PT011`, `G004`, `PLC0415`, and
  `PLR2004` in `tests/`) and nine groups (`ANN`, `D`, `EM`, `FBT`, `TC`, `T20`, `INP`, and the preview rules `CPY` and `DOC`)
  are off, each with its reason and the count of findings in `ruff.toml`. A suppression is a `# noqa: <code> - reason` on the
  line;
* `mypy` (`[tool.mypy]` in [pyproject.toml](pyproject.toml)): Python 3.14, `strict` mode (every function and method is
  annotated, generics carry their arguments), unused `type: ignore` comments and unreachable code are errors;
* `pylint` (`[tool.pylint]` in the same file): at least 8/10, lines of at most 100 characters (ruff stops at 140, so a line of
  101 to 140 columns costs pylint score and fails no gate; `pre-commit` runs pylint on the staged files without the
  convention checks, so the 100 columns are counted only by the project-wide run in `pre-push` and in hosted CI);
* `pytest` ([pytest.ini](pytest.ini)): every warning is an error (`filterwarnings = error`), so a deprecated call or an
  unawaited coroutine fails the test that causes it; an `ignore:` line names one message, one category and one module;
* shell scripts pass `shellcheck`, and workflow files pass `actionlint`;
* every file carries a copyright notice and a license, in the file or in [REUSE.toml](REUSE.toml) (`reuse`).

Keep to the style of the code around yours. An exception to a rule (`# noqa`, `# type: ignore`, `# pylint: disable`,
`# pragma: no cover`) names the rule and gives the reason on the same line. The gates fail a change that breaks
these standards, so compliance is a condition of merging ([docs/quality-gates.md](docs/quality-gates.md)).

## Commits

Commit messages follow `[🤖 ]<type>(<scope>): <subject>` (types `feat fix chore docs refactor test perf ci build`); subject
at most 150 characters, body at most 10 lines of at most 150 characters each. AI `Co-Authored-By` trailers and "Generated
with" lines are rejected by the `commit-msg` hook; mark AI-written commits with the `🤖` prefix instead.

## Code review

Every change from a contributor arrives as a pull request against `main`, and only a maintainer merges it. The pull
request says what changed and why, and links the issue it closes.

**What a reviewer checks.**

* The change does what the pull request says and stays within it; anything bigger was agreed in an issue first.
* The layering holds (routes, services, protocols, repositories and adapters) and `uv run pytest tests/architecture/`
  passes.
* It has tests that prove it and keep coverage at 100 %, and it follows the [Coding standards](#coding-standards).
* The docs, the skills, [CHANGELOG.md](CHANGELOG.md) and [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md)
  are updated when the change is visible to a user, an MCP client or an operator.
* No quality gate is weakened, no hook bypassed, and no security boundary changed without a reason in the pull request
  (authentication and scopes, the request body limit, the loopback guard).
* No new dependency that was not agreed, and nothing in the wheel or the sdist but Axmem's source.
* The commit messages follow the format in [Commits](#commits).

**Merge conditions.** A maintainer merges a contributor's pull request only when all of these hold:

1. a maintainer who is not its author has approved it, and the open review comments are answered;
2. the checks of `ci.yml` (`lint`, `smoke-test`) and `quality.yml` (`static`, `trivy`) are green on the last commit;
3. the maintainer has run the slow gates of the `pre-push` hook on the result (see
   [Making a change](#making-a-change)), because CI does not run the tests.

The repository rules for `main` enforce the first two for a contributor: deletion and force pushes are blocked, commits must
be signed, a change needs a pull request with one approval of a code owner, resolved conversations and the checks `lint`,
`smoke-test`, `static`, `trivy` and the two CodeQL analyses green on the last commit. The third cannot be enforced, because
the hosted CI has no GPU. The Admin role bypasses the rules, because the release script pushes to `main`; both owners are
admins, so the rules do not hold the maintainers' own changes (see
[GOVERNANCE.md](GOVERNANCE.md#if-the-lead-is-unavailable)).

**The maintainers' own changes.** These are pushed to `main` directly after the git hooks have passed on the maintainer's
machine: the `pre-commit` and `pre-push` hooks run the whole pipeline, including the tests that CI does not run. No second
person reviews them today.

## License

Axmem is released under the GNU Affero General Public License, version 3 only (`AGPL-3.0-only`, copyright Giovanni
Manzoni). By sending a contribution you agree that it is licensed under the same terms. Code derived from Forgetful keeps its
MIT notice in [LICENSE-FORGETFUL-MIT.md](LICENSE-FORGETFUL-MIT.md). The models and the NVIDIA libraries are not part of
this release: see [their licenses](docs/gpu-models.md#licenses-of-the-models-and-libraries).
