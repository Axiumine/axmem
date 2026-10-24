# Contributing to Axmem

Thank you for helping. Axmem is a one-maintainer project, so replies can take a few days; a short, focused
change gets reviewed fastest.

## Hardware: NVIDIA only

Axmem runs its embedder and reranker with TensorRT on an NVIDIA GPU (driver R580 or newer, Linux). There is no CPU
path and no fallback, by design ([GPU models](docs/gpu-models.md)). That will not change on request, so please do not
open an issue, a discussion or a comment that asks for AMD, Intel, Apple-silicon or CPU-only support, for Windows, or for
"a way to run it without a GPU". Such requests will be closed.

**macOS is the one exception, and only as a pull request.** If you want Axmem on a Mac, send a pull request with code
that works natively on macOS:

* It runs directly on the Mac, not through a Linux virtual machine, a container or an emulation layer.
* The NVIDIA path on Linux stays as it is and keeps passing its gates.
* Like the NVIDIA path, it runs the real models: no stub, no test-only embedder, no skipped test
  ([testing philosophy](AGENTS.md#testing-philosophy)).
* The pull request says which Mac and which macOS version it was tested on, and shows the test run. Do not add a macOS
  or self-hosted runner to `.github/workflows`: hosted CI runs only checks that need neither a GPU nor Docker, and
  `tests/architecture/test_workflows.py` fails on anything else.

The maintainer decides whether to merge it. No other platform is planned or considered.

## Ways to contribute

* **Report a bug or ask for a feature** in the [issue tracker](https://github.com/Axiumine/axmem/issues). Say what you
  did, what you expected and what happened, plus the version or commit, your OS, the GPU model and the NVIDIA driver, the
  Qdrant version, and the log lines. Never paste an API key, a token, a Qdrant key or the content of your memories.
* **Report a security problem privately**, not in an issue: write to dev@giovannimanzoni.com.
* **Send a pull request** for a fix, a test, a doc improvement or a feature. For anything bigger than a small fix, open an
  issue first so we agree on the approach before you spend time on it.
* **Improve the docs**: [README.md](README.md) and [docs/](docs/) are part of the product.

Who decides, who has access and how review works is in [GOVERNANCE.md](GOVERNANCE.md); the maintainers are listed in
[MAINTAINERS.md](MAINTAINERS.md). Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

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
5. Open a pull request against `main` that says what changed and why, and links the issue.

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
  at most (`radon`), and the dead-code check (`vulture`) is clean.
* **Coverage.** Line and branch coverage of `app` and `main.py` stays at 100 %.
* **Gates.** Do not weaken a threshold, skip a gate or bypass a hook (`--no-verify`) to make a change pass: fix the code, or
  explain the exception in the pull request.
* **Dependencies.** Do not add or bump a dependency without agreeing it in an issue first. They are pinned in `uv.lock`
  and scanned by `trivy`.
* **Package contents.** The wheel and the sdist hold Axmem's source and nothing else. Model weights, TensorRT engines and
  the NVIDIA libraries have licenses of their own and are never added to the repository or to a package (the `package` gate
  refuses them).

## Commits

Commit messages follow `[🤖 ]<type>(<scope>): <subject>` (types `feat fix chore docs refactor test perf ci build`); subject
at most 150 characters, body at most 10 lines of at most 150 characters each. AI `Co-Authored-By` trailers and "Generated
with" lines are rejected by the `commit-msg` hook; mark AI-written commits with the `🤖` prefix instead.

## License

Axmem is released under the GNU Affero General Public License, version 3 only (`AGPL-3.0-only`, copyright Giovanni
Manzoni). By sending a contribution you agree that it is licensed under the same terms. Code derived from Forgetful keeps its
MIT notice in [LICENSE-FORGETFUL-MIT.md](LICENSE-FORGETFUL-MIT.md). The models and the NVIDIA libraries are not part of
this release: see [their licenses](docs/gpu-models.md#licenses-of-the-models-and-libraries).
