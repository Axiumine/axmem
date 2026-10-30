# Changelog

All notable changes to Axmem are recorded here. Axmem is derived from
[Forgetful](https://github.com/ScottRBK/forgetful) by Scott Raisbeck (MIT), forked at upstream commit
`6d0199ed` (2026-10-18), plus the fork work on top of it. Upstream's later commits are not merged. The reason
behind each change is in [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md).

The format follows [Keep a Changelog](https://keepachangelog.com/). The first release is [v0.6.0rc1] (commit
`16e8ec8`), a release candidate that is on PyPI; changes after it go under [Unreleased] until the next release stamps them.
Entries marked **(planned)** are decided but have not landed; each is moved to its section
without the mark, with the commit that landed it, in the commit that lands it.

## [Unreleased]

### Added

- The GitHub release of a version carries the log of its changes as its notes. `release.yml` cuts the section `## [v<version>]`
  of `CHANGELOG.md` at the tag (the one that `scripts/release-pypi.sh` stamps from `## [Unreleased]`) with the new
  `scripts/release/notes.py` and gives the file to `softprops/action-gh-release` as `body_path`, so the notes are what the
  changelog says about that version and nothing else. The step runs before anything is built, signed or attached and stops the
  run when the changelog at the tag has no section of the tag or more than one, when the section holds no entry under its
  headings, or when it is longer than the 125,000 characters that GitHub accepts as the body of a release (the section of
  0.6.0rc1 is 111,302). A heading inside fenced code does not count as one. The action, its pin, the permissions of the jobs,
  the tag of the SLSA generator and the dependencies are unchanged, and the wheel and the sdist are the same bytes. The release
  of 0.6.0rc1 was made before this change. `tests/integration/test_release_notes.py`
  runs the module on changelogs of several shapes (a missing, repeated and empty section, the limit to the character, a
  section that is the last of the file, Windows line ends), on every released section of the repository's `CHANGELOG.md` and on
  the heading that `stamp_changelog` writes; `tests/architecture/test_release_workflow.py` holds the step and `body_path` in
  place. See [docs/release.md](docs/release.md).
- `scripts/smoke-release.sh [--keep] [--index testpypi|pypi] <version>`, the install check of a release: it installs the files
  that an index serves for a version and uses the server they start. It reads the digests of `release-digests.json` at the
  tag `v<version>` (the tag is signed, the index is not trusted), requires the JSON of the index to list exactly the manifest's
  files, each on the files host of that index (`test-files.pythonhosted.org` or `files.pythonhosted.org`), over https only, and
  compares the sha256 of the downloaded bytes. It installs the wheel by its path into a new Python 3.14 environment with
  `https://pypi.nvidia.com` as the only extra index (TestPyPI is never an index of the install, and the shell's index variables
  are cleared) and checks `uv pip check`, the version and `axmem --help`. It then starts a throwaway Qdrant container
  (`qdrant/qdrant:v1.19.2`, a loopback port, a random key, memory-backed storage, the label `axmem-wheelsmoke=1`) and
  `axmem serve --transport http` from the new environment, with a clean environment and static bearer-token authentication,
  waits for `/health`, and drives the installed CLI as a client: lists the tools, is refused with a wrong token, saves four
  memories, finds the first by a paraphrase with a rerank score, reads it back and is told that an unknown id is an error. The
  server (SIGTERM) and the container are stopped and removed on every exit, an error or an interrupt included. It is advisory,
  not a gate, uploads nothing, needs no credential and needs the NVIDIA GPU, Docker and the models of the machine, so it runs
  by hand after the rehearsal (`--index testpypi`) and after the release (`--index pypi`); the `shellcheck` gate lints it, and
  `tests/integration/test_release_smoke_script*.py` run the real script against fake `curl`, `uv`, `docker`, `nvidia-smi` and
  `axmem` (the hosts, the image and the extra index are held to `scripts/release/facts.py`, `scripts/lib/project.sh`,
  `tests/qdrant_support.py` and `pyproject.toml`, and each cleanup path is run). See
  [docs/release.md](docs/release.md), "Install check of a release".
- `.github/workflows/scorecard.yml`: [OpenSSF Scorecard](https://github.com/ossf/scorecard) runs on the repository on every
  push to `main` and on `workflow_dispatch`, publishes the result to scorecard.dev (the Scorecard badge of the README reads
  it; the badge shows an error until the first run on `main` has published one) and uploads the SARIF file to code
  scanning. Scorecard rates the settings and the history of the repository, not our code, so the workflow is not a quality
  gate and no hook runs it. The token is read-only at the top of the file; the one job adds `id-token: write` (the action
  signs the result with it) and `security-events: write`; there is no secret, no personal access token and no schedule.
  `ossf/scorecard-action` is a Docker container action that the hosted runner starts itself, so the workflow has no Docker
  step of its own. `tests/architecture/test_workflows.py` applies its rules to the file (hosted runner, read-only token,
  `persist-credentials: false`, actions pinned to a commit, concurrency group) and holds the shape that scorecard.dev accepts
  for a published result (no `env` or `defaults`, no write permission at the top, `id-token` in the Scorecard job only, only
  the four actions it needs). A push-triggered run of `main` joins the runs that the release guard (`scripts.release.ci_green`)
  requires to be green on the release commit. See [docs/quality-gates.md](docs/quality-gates.md), "OpenSSF Scorecard and
  Dependabot".
- `.github/dependabot.yml`: Dependabot version updates for the `github-actions`, `uv`, `docker` and `npm` ecosystems, weekly, at
  most three open pull requests each, minor and patch updates of an ecosystem in one pull request, commit prefix `chore(deps)`.
  The last two watch `test_harness/docker`: the `node` base image of its Dockerfile and the agent CLIs that its `package.json`
  and `package-lock.json` pin. Three things are left out on purpose and the file says why: the
  `slsa-framework/slsa-github-generator` reference of `release.yml`, which is changed together with `SLSA_GENERATOR_VERSION` and
  `SLSA_GENERATOR_COMMIT`, the three TensorRT packages, which are bumped by hand as [docs/gpu-models.md](docs/gpu-models.md)
  describes, and major versions of the `node` image, because the Dockerfile follows the Node 24 line on purpose. It is a
  setting of GitHub's Dependabot service, not a workflow.
  `.github/CODEOWNERS` names the maintainers of `MAINTAINERS.md` as the owners of every file and of the release files.
- Coverage-guided fuzzing: three [Atheris](https://github.com/google/atheris) harnesses in `fuzz/` (`fuzz_token_claims.py`,
  `fuzz_request_arguments.py`, `fuzz_terminal_text.py`, with the helper `shared.py`) feed generated input to the functions that
  read data a client or an identity provider controls: the scopes and the tenant taken from token claims, the coercion of tool
  and query arguments (`parse_created_since` among them: the harness fails on the unfixed code, see Fixed), and the cleaning of
  terminal text and URLs. A harness fails on an exception its contract does not name
  or on a property that does not hold (no scope that the claims do not spell, no control character in printed text, no password
  in a redacted URL). Each runs offline, with no GPU and no Qdrant, and `tests/integration/test_fuzz_targets.py` runs every
  harness for 300 inputs with a fixed seed, so a harness cannot rot; a longer run is a command the contributor starts
  (`uv run python fuzz/fuzz_token_claims.py -max_total_time=60`). `atheris==3.1.0` joins the `dev` group for Linux x86_64, the
  one platform for which it publishes a build for Python 3.14. `fuzz/` is checked by the `ruff`, `mypy`, `pylint`, `bandit`,
  `semgrep`, `vulture` and `radon` gates, is outside the coverage report, and is in neither the wheel nor the sdist. See
  [docs/fuzzing.md](docs/fuzzing.md).
- `SECURITY.md` (how to report a vulnerability, which versions receive fixes, how a fix and its disclosure proceed),
  `docs/security.md` (the security requirements of Axmem, each numbered, with the code that enforces it and the tests that
  prove it, and what Axmem does not guarantee), `docs/assurance-case.md` (threat model, trust boundaries, the argument that
  the requirements are met, the secure design principles, the OWASP Top 10 and CWE Top 25 tables, the residual risks) and
  `docs/roadmap.md` (what the project intends to do in the twelve months to October 2027 and what it does not). The README
  links them, and `CONTRIBUTING.md`, `GOVERNANCE.md` and `MAINTAINERS.md` now state the coding standards, the review and merge
  conditions, and what the co-owner can and cannot do if the lead is unavailable (`GOVERNANCE.md#if-the-lead-is-unavailable`).
- `.bestpractices.json`: proposed answers for the [OpenSSF Best Practices](https://www.bestpractices.dev/) badge form, each with
  a justification that points to a file of the repository. The badge form reads them as proposals; the maintainer reviews every
  answer. It is not part of the wheel or the sdist. See [docs/quality-gates.md](docs/quality-gates.md), "OpenSSF Scorecard and
  Dependabot".
- Security headers on every answer of the HTTP server (`SecurityHeadersMiddleware`, `app/middleware/security_headers.py`, the
  outermost middleware of `main._http_middleware`): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`,
  `Cache-Control: no-store` and `Cross-Origin-Resource-Policy: same-origin` (left out when `CORS_ENABLED`, because the operator
  has then invited other origins in). They have no setting. They are on the refusals too (the `421` and `403` of the loopback
  guard, the `413` of the body limit, the `401` and `403` of the routes and of the MCP authentication). A header that the
  application sets itself is kept, so the MCP event stream keeps its own `Cache-Control` and is still not buffered. The
  answer to an unhandled exception is sent by Starlette outside every middleware and carries none of them (a test pins this).
  Every client of Axmem is a program that reads JSON or an event stream, so none should notice; a browser that is pointed at an
  address of the server now gets a response that it will not frame, sniff or cache. `tests/integration/test_security_headers.py`
  and `tests/e2e/test_security_headers_e2e.py`. See [docs/configuration.md](docs/configuration.md), "Security headers of the
  HTTP server".
- TLS from the server itself: `SERVER_SSL_CERTFILE` and `SERVER_SSL_KEYFILE` (PEM files; both or neither) make the HTTP
  transport serve HTTPS, and `SERVER_SSL_KEYFILE_PASSWORD` (a secret, never logged or repeated in an error) opens an encrypted
  key (`app/config/tls.py`). The server offers TLS 1.2 or later; for TLS 1.2 only ECDHE with AES-GCM or ChaCha20-Poly1305,
  because uvicorn's own cipher string drops every AES-GCM suite and keeps CBC and SHA-1 suites. It fails closed: one file
  without the other, a password without the files, a file that cannot be read, or a certificate and key that do not belong
  together end the start with one `Error:` line and exit code 1 before anything is bound, and never fall back to plain HTTP.
  With a certificate the answers also carry `Strict-Transport-Security: max-age=31536000`, except to a loopback `Host` (a
  browser pins a host name whatever the port), and without `includeSubDomains` or `preload`. A server that serves plain HTTP
  on an address beyond loopback now logs one warning at start that says bearer tokens and memories cross the network in clear
  text. **Nothing changes by default**: with neither setting the server serves plain HTTP as before, on the same address, and
  the warning is the only difference. Stdio ignores the settings. The `axmem` CLI verifies the certificate; `SSL_CERT_FILE`
  names the authority to trust for a private one. `tests/integration/test_tls_settings.py`, `test_tls_server.py` and
  `tests/e2e/test_tls_serve_e2e.py` use a real certificate authority and a real server in a child process. See
  [docs/configuration.md](docs/configuration.md), "Serve TLS", and `.env.example`.
- A copyright and license notice in every source file, and a `reuse` quality gate that holds it. The Python files (tests
  included), the shell scripts, the Git hooks, the workflows, `.github/dependabot.yml` and the Dockerfile start with SPDX tags,
  after a shebang line or a `# syntax=` directive: `SPDX-FileCopyrightText: 2026 Giovanni Manzoni` and
  `SPDX-License-Identifier: AGPL-3.0-only`, or, for a file derived from Forgetful, also `SPDX-FileCopyrightText: 2025 Scott
  Raisbeck` and `AGPL-3.0-only AND MIT`. A file is derived when a commit by an author of Forgetful is in its history or when it
  holds code taken from a Forgetful file: one Forgetful file holds at least 5 of its code lines and they are 25 percent or
  more of its code lines (ten files that only the maintainer committed, and three more at 15 to 20 percent that take the
  second form all the same, as the cautious choice). `REUSE.toml` covers the files that cannot hold a comment (documents,
  data, configuration, lock files, images), by type of file and never by directory, so a new source file without a header fails
  the gate; `LICENSES/` holds `AGPL-3.0-only.txt` (byte for byte `LICENSE`) and `MIT.txt` (the SPDX text). The `reuse` gate
  (`reuse[charset-normalizer]==6.2.0` in the `dev` group, five new locked packages) runs `reuse lint-file` over the files git
  sees, in `pre-commit`, `pre-push` and the `ci` stage and as a step of the `static` job of `quality.yml`, in about two
  seconds, offline. `tests/integration/test_quality_reuse_gate.py` runs the real tool on scratch repositories and
  `tests/architecture/test_licensing_headers.py` checks the two header forms, the texts in `LICENSES/` and that the set of
  licenses is the same in the headers, `REUSE.toml` and `pyproject.toml`. The `wheel` gate is **not relaxed**: no
  `check-wheel-contents` code is added to its ignore list. The twelve `app/**/__init__.py` package markers were empty and would
  have become identical with the same header, which the tool reports as duplicate files (`W002`); each now has a docstring of
  its own after the header, so no two files of the wheel are identical. **No behaviour changes**; the wheel and the sdist hold
  the same files with a header and a docstring added. `.playwright-mcp/` (the page snapshots of a browser session) is git-ignored. See
  [docs/quality-gates.md](docs/quality-gates.md), "Licensing headers".
- `.github/workflows/codeql.yml`: [CodeQL](https://codeql.github.com/) code scanning on every push to `main` and every pull
  request into `main`, one job per language (`python`, and `actions` for the workflow files; a matrix, each with its own
  category) in the advanced setup, `build-mode: none`. The token is `contents: read`, and the one job adds `security-events:
  write` to upload the result to code scanning. Actions are pinned to a commit like every other workflow. Like Scorecard it is a
  report to read on GitHub, not a quality gate, and no hook runs it. `tests/architecture/test_workflows.py` applies the rules
  of the workflows to the file and holds its shape, and `JOBS_OF` names its job. See
  [docs/quality-gates.md](docs/quality-gates.md), "OpenSSF Scorecard and Dependabot".
- `docs/security-review.md`: a review of the security requirements and of the security boundary, dated 2026-10-29 (commit
  `e2cc68b` and the uncommitted tree of that day), with sixteen numbered findings. It was prepared by an AI coding agent working
  for the lead maintainer, says so on its first page, and is not an independent human review: the maintainer has to read it and
  adopt or redo it, and a second human reviewer is an open item. Two findings are already fixed in the tree (the TLS option and
  the security headers, above), one is rated Medium (a `.env` file in the working directory could set `AXMEM_SERVER`, and
  the CLI then sent `AXMEM_TOKEN` there; fixed in the tree, see Security below) and SR-16 (the same file still names the Qdrant node and the
  provider URLs of a local-mode CLI) is recorded as accepted; none of the other code findings has been fixed. `docs/security.md` and
  `docs/assurance-case.md` now cover TLS and the headers (requirements `NET-7` to `NET-11` and `SEC-6`, goals G4 and G5, a new
  residual risk for the open findings), and no longer list "no TLS" and "no security headers" among the things Axmem does not
  guarantee. They also record the limit of the working-directory `.env`, and `docs/security.md` has rows for Dependabot,
  CodeQL and Scorecard (`SUP-7`, `SUP-8`).

### Changed

- The `ruff` gate now runs every rule group except a few that `ruff.toml` lists as off, each with its reason and the count of
  its findings (was 16 groups: `F`, `I`, `Q`, `COM`, `C4`, `ISC`, `ICN`, `PIE`, `PGH`, `TID`, `YTT`, `FA`, `LOG`, `T10`, `UP`, `S`).
  Added: `E`, `W`, `N`, `A`, `B`, `BLE`, `ASYNC`, `DTZ`, `ERA`, `EXE`, `FIX`, `FLY`, `FURB`, `G`, `INT`, `PERF`, `PTH`, `PYI`,
  `RET`, `RSE`, `RUF`, `SIM`, `SLOT`, `TD`, `TRY`, `ARG`, `SLF`, `PLC`, `PLE`, `PLW`, `PLR`, `C90`, `PT` and the framework
  groups `AIR`, `DJ`, `FAST`, `NPY`, `PD`. Ruff enforces 140 columns (`E501`) and pylint 100 under `fail-under` 8.0;
  `pre-commit` runs pylint on the staged files without the convention checks, so the 100 columns are counted only by the
  project-wide run in `pre-push` and `ci`. Four lines over 140 columns carry a `# noqa: E501 - reason`. Off inside them:
  `TRY003`, `PT011`, `G004` (the log masking filter treats the message and its arguments apart) and `PLC0415` (the lazy
  imports are deliberate); `ARG`, `SLF001` and `PLR2004` (assertions compare with literal expected values) are off in
  `tests/`, and the 16 `PLR2004` findings outside `tests/` use a named constant. Still not on: `ANN`, `D`, `EM`, `FBT`, `TC`,
  `T20`, `INP`, and the preview rules `CPY` and `DOC`. The size limits of `PLR` are those of `[tool.pylint.design]`. The
  findings were fixed without a change of what the server does:
  exceptions raised inside `except` carry their cause (`raise ... from`), the error logs of the three meta-tools, the tool
  registry and the embedding adapters call `logger.exception` (same level, same text), the temporary files of the atomic
  writes use `pathlib`, a few unused variables and trailing blanks went, `scripts/release/ci_green.py` and `sdist.py` lost
  their executable bit (they run as `python -m`), and about 55 more lines than before carry a `# noqa: <code> - reason` (209 in all).
- The `mypy` gate now runs in `strict` mode (was `check_untyped_defs`, `warn_unused_ignores`, `warn_redundant_casts`,
  `no_implicit_optional` and `strict_equality`), with `warn_unreachable`, `disallow_any_unimported` and nine optional error
  codes (`ignore-without-code`, `possibly-undefined`, `unused-awaitable`, `redundant-self`, `mutable-override`, `deprecated`,
  `exhaustive-match`, `narrowed-type-not-subtype`, `unimported-reveal`) that the code base already satisfied. Strict mode found
  165 errors in 33 files: 83 functions without annotations, 34 calls to them, 28 bare generics, 11 returns of `Any`, 8 imports of
  `logging` through `app.config.logging_config` (they import it from the standard library now) and one base class of
  `tensorrt`. They are fixed with annotations and local variable types; what the server does is unchanged. Two lines gain a
  `# type: ignore[<code>]` with a reason (the TensorRT logger base class and the tokenizer return type: both packages ship
  no type information; a third, in `app/config/settings.py`, was there before). Measured and still off: `redundant-expr` (3) and `truthy-bool` (2), which flag defensive checks, and
  `explicit-override` (18); `test_harness` is not type-checked (33 errors, some of them real).
  The `pylint` gate loads eleven more extensions that the code base satisfies completely (`check_elif`,
  `comparison_placement`, `consider_refactoring_into_while_condition`, `dict_init_mutate`, `dunder`, `eq_without_hash`,
  `for_any_all`, `private_import`, `redefined_loop_name`, `redefined_variable_type`, `typing`); its threshold stays at 8.0
  because the score is 8.00 and 1785 of the findings are lines over 100 columns.
- The test runs turn every warning into an error (`filterwarnings = error` in `pytest.ini`), so a deprecated call, an unawaited
  coroutine or a leaked resource fails the test that causes it. One `ignore` line follows, limited to one message, one category
  and one module: the `DeprecationWarning` for `typing._UnionGenericAlias` that `google.genai.types` (google-genai 1.66.0)
  raises at import, which only a newer release of the library removes. `httpx2==2.16.0` joins the `dev` group because
  `starlette.testclient` warns that `httpx` is deprecated for it; the package and its runtime dependencies do not change.
  `docs/quality-gates.md` has a new section, "Warnings", that lists for `ruff`, `mypy`, `pylint` and `pytest` what is on and
  what is off, with the reason and the count of findings. Nothing changes for a user of the server, the CLI or the package.
- `fastmcp` is locked at 3.4.10 (was 3.2.2); the relock moves `mcp` to 1.30.0 (was 1.29.0) and `opentelemetry-api` to 1.45.1
  (was 1.39.1), adds `fastmcp-slim` and `griffelib`, and drops `importlib-metadata` and `zipp`. FastMCP 3.4 keeps only the first
  paragraph of a docstring that has an `Args:` section as a tool description, which would have dropped the catalog and the
  workflow from `discover_axmem_tools`; the three meta-tools are registered with their whole docstring (`app/routes/mcp/meta_tools.py`),
  so their descriptions are byte for byte what they were. What an MCP client sees differently is that the input schemas of
  `discover_axmem_tools`, `how_to_use_axmem_tool` and `execute_axmem_tool` now carry a `description` for each argument
  (the signature snapshot gained those four lines and nothing else). From 3.4 FastMCP logs a failed tool call (`ToolError`)
  without its traceback; the caller-supplied name is still escaped in the log line. FastMCP no longer has `JsonWebToken`,
  so the tests that sign tokens use `tests/jwt_support.py` on `joserfc`, which joins the `dev` group at 1.7.0 because the tests
  import it directly. Why: see Security below.
- The image of the UAT walkthrough harness (`test_harness/docker/Dockerfile`) is reproducible: the base is `node:24` by digest
  (the comment says how to move it), and `opencode` is installed from `package.json` and `package-lock.json` (`opencode-ai`
  1.19.5, `npm ci`) instead of `npm install -g opencode-ai@latest`. The unused `OPENCODE_VERSION` argument is gone. The
  harness rebuilds its image when any of those inputs changes: the hash that it stores in the image label
  (`axmem.harness.requirements-hash`, same name) now covers the exported lock and the `Dockerfile`, `package.json` and
  `package-lock.json`, where it covered the lock alone, so a new base digest or a new `opencode` no longer leaves a stale image
  in use (`requirements_hash` became `image_inputs_hash`; an unreadable input stops the run with a harness error). An image
  built before this change has the old hash and is rebuilt once. `--rebuild-image` and `test_harness/README.md` no longer say
  that `opencode` floats.
- Every requirement of `pyproject.toml` is an exact `==` pin. `memray`, `pytest-memray` (the `dev` group) and `docker` (the
  `harness` group) were `>=`; they are now `==1.20.0`, `==1.11.0` and `==7.2.0`, the versions that `uv.lock` already held, so the
  lock changes by those three specifier lines only. New requirements are pinned the same way: `atheris==3.1.0`,
  `joserfc==1.7.0`, `reuse[charset-normalizer]==6.2.0` and `cryptography==50.0.0`, which the TLS tests import to make their own
  certificate authority and which was a locked transitive dependency already. Nothing changes for a user of the package.
- The hardware policy of `CONTRIBUTING.md` (and the matching lines of the README, `GOVERNANCE.md`, `docs/gpu-models.md`
  and `docs/contributors.md`) no longer contradicts itself: it turned away requests for Apple-silicon and CPU-only support
  but accepted a pull request with code that works natively on macOS, which on a Mac without an NVIDIA GPU can only be one of
  them. The one exception is now a pull request that adds Apple silicon with an MLX backend; a backend on Core ML, Metal,
  PyTorch MPS, ONNX Runtime or the CPU, and an Intel Mac, are not accepted. The section is now `## Hardware`
  (anchor `#hardware`).
- The install notes say that Axmem is on PyPI: `0.6.0rc1`, a release candidate and the only version there, installs with
  `uv tool install --extra-index-url https://pypi.nvidia.com axmem` or `pip install --extra-index-url https://pypi.nvidia.com axmem`
  (the flag takes the three TensorRT packages from NVIDIA's index, see the README), and `uvx axmem` runs it without installing,
  so the `uvx axmem` commands of the client guides resolve as written. The README table of Installing the package lists the two
  PyPI routes first, and `tests/integration/test_install_requirements.py` keeps them in step with `pyproject.toml`. The
  `axmem-cli-setup` and `axmem-mcp-setup` skills, the connectivity guide, the Gemini CLI, OpenCode and Copilot CLI guides with
  their `axmem-setup` commands and the roadmap no longer say that the package is not published or must be installed from a
  checkout first; the checkout, Git URL and wheel routes stay as alternatives for development.

### Fixed

- `get_recent_memories` refused a `created_since` that has no instant in UTC with an unhandled `OverflowError`, which the
  client received as a generic tool error, not the message that tells an agent what to send. The value is an ISO 8601
  timestamp with a timezone, and `0001-01-01T00:00:00+01:00` (year 1 moved forward past the range) or
  `9999-12-31T23:59:59-01:00` (year 9999 moved back) parsed but could not be converted. Both now give the same `ToolError` as
  any other value that is not a timestamp with a timezone: `created_since must be an ISO 8601 timestamp with a timezone`
  (`parse_created_since` in `app/routes/mcp/adapters/memory_arguments.py`). A timestamp inside the range is unchanged. The
  `created_since` tests moved to `tests/integration/test_mcp_created_since_argument.py` (the contract file had no room left
  under the `radon-mi` limit), `tests/e2e/test_mcp_memory_argument_errors_e2e.py` has both inputs, and the argument fuzz
  harness now covers the function.
- The docs and `.env.example` said that `AUTH_MODE=none` needs `127.0.0.1` or `::1`; the server accepts, as 0.6.0rc1 does, any
  address of `127.0.0.0/8` and `::1` (and no host name, `localhost` included). The text now says what the code does. No behaviour
  changed.

### Security

- **Breaking for anyone who chose the CLI's server in a `.env` of the working directory.** The CLI no longer takes
  `AXMEM_SERVER` from the `.env` file of the directory where it runs (finding SR-1 of `docs/security-review.md`, Medium): a
  repository cloned there could name a server of its own, and the CLI then sent that server `AXMEM_TOKEN` and every memory it
  saved or searched, and showed the answer to an agent. The filter drops the setting after the file is parsed, so `export`,
  quotes, any letter case and `${}` expansion do not get it through, and a symlinked `.env` is filtered like any other. The server
  now comes from `--server`, the `AXMEM_SERVER` environment variable or the user's settings file (`axmem auth login --server URL`
  writes it), as `docs/configuration.md` already listed. When the working directory's `.env` names a server and none of those does,
  the command runs in local mode and prints one line on stderr (also with `--json`; stdout is the result), `Note: ./.env sets
  AXMEM_SERVER, which the CLI ignores ...`, which does not repeat the ignored value; `axmem auth status` prints it too. A user who
  relied on that file moves the line to the environment, to `--server` or to the user's file (`axmem auth login --server URL`).
  Every other setting of the working directory's `.env` is read as before, `axmem serve` included; `QDRANT_URL` and the provider
  URLs there still steer a local-mode CLI, as they steer `axmem serve`. New requirement `SEC-7` in `docs/security.md`, tested by
  `tests/integration/test_cli_server_origin.py`; `docs/security.md`, `docs/assurance-case.md`, `docs/configuration.md`,
  `.env.example` and the `axmem-cli-setup` skill say the same.
- `fastmcp` is locked at 3.4.10 (was 3.2.2), `mcp` at 1.30.0 (was 1.29.0) and `opentelemetry-api` at 1.45.1 (was 1.39.1), the
  smallest releases that carry the fixes of six advisories that `osv-scanner` 2.0.3 listed for `uv.lock`, all rated medium in
  the GitHub advisory database: GHSA-7xgj-8392-5q4r (FastMCP: post-transform component authorization is skipped on direct
  calls), GHSA-w4fh-qvv9-3v23 / CVE-2026-116684 (MCP Python SDK: server bearer-token authentication accepts tokens issued for
  a different resource server), GHSA-84m7-p3x7-pcfv / CVE-2026-59951 (unbounded session accumulation in the StreamableHTTP
  transport), GHSA-5h93-6whr-6q8j / CVE-2026-116685 (HTTP client transports followed cross-origin redirects carrying custom
  headers and bodies), GHSA-rwrf-2pqf-9j8j / CVE-2026-116686 (the client fetched server-chosen `$ref` URLs while validating
  tool results) and GHSA-xcq7-p3pf-p88h / CVE-2026-114603 (unbounded `tracestate` parsing in `opentelemetry-api`). The scan of
  the new `uv.lock` reports no issue. OpenSSF Scorecard's Vulnerabilities check counts such advisories, which is how they were
  found; the `trivy` gate had not stopped on them.
- `joserfc` is pinned at 1.7.6 (was 1.7.0), the smallest release that carries the fixes of two advisories published on 2026-10-30: GHSA-jpxq-7h8p-2q76 (high: an unbounded `recipients` array in a JWE General JSON message leads to CPU exhaustion through repeated RSA private-key decryption, fixed in 1.7.5) and GHSA-9pg5-8c7x-5hfx (medium: the size guard for a compact JWS allocates the attacker-controlled segments before it rejects them). FastMCP verifies bearer tokens with `joserfc`, and `tests/jwt_support.py` signs them with it. OpenSSF Scorecard's Vulnerabilities check listed both for the old lock file the same day, before the `trivy` database had them; the scan of the new `uv.lock` reports no issue.
- Operations note, not a change to any file: four security settings of the GitHub repository `Axiumine/axmem` are enabled, as the
  GitHub API reported on 2026-10-29: private vulnerability reporting (the private channel that `SECURITY.md` names), secret scanning,
  secret scanning push protection and Dependabot security updates. The first three were off in the audit that began this work.
  Dependabot version updates are configured by `.github/dependabot.yml` and code scanning by `.github/workflows/codeql.yml`
  (above); Dependabot, CodeQL and Scorecard are reports to read, not gates. The same API reported no security advisory, no issue
  and no Dependabot alert on that date.

## [v0.6.0rc1] - 2026-10-28

### Added

- Every check of the project is a quality gate that a Git hook runs (`98a81ad5`, `da6120b`). Four gates are new: `shellcheck` (`shellcheck -x` over
  the files of `.githooks/`, the release scripts and the fake tools of the release tests), `actionlint` (the workflows, and
  their `run:` blocks through the same shellcheck), `architecture` (`tests/architecture`, which `ci.yml` ran as a raw command
  and the hooks ran only inside `tests-fast` and `tests`) and `wheel` (see the next entry). `shellcheck-py`, `actionlint-py`,
  `twine` and `check-wheel-contents` are pinned in the `dev` group, so the gates and a hand run use the same versions; the
  release script ran the last two unpinned through `uvx`. `twine` 7.0.0 needs `rich>=14.3.3` and `packaging>=26.1`, so
  `uv.lock` also moves `rich` from 14.3.2 to 15.0.0 (a major version, a transitive dependency of `fastmcp`) and `packaging`
  from 26.0 to 26.3. `actionlint-py` ships only an sdist that downloads its binary from GitHub when it is built, so the
  `bootstrap` of `qodana.yaml` leaves it out of the Qodana environment (`--no-install-package actionlint-py`): the build
  failed there with a certificate error, and would fail again whenever GitHub is slow or unreachable, and the `qodana` gate
  stopped the push. No module imports it and trivy still scans it in `uv.lock`. The first
  three run in `pre-commit`, `pre-push` and the `ci` stage; `quality.yml` and `ci.yml` call them by gate name. `AGENTS.md` and
  [docs/quality-gates.md](docs/quality-gates.md) state the rule and what a new check must do.
- The quality runner says what it left out. A stage prints a `SKIP` line for every gate that `SKIP` or `ONLY`
  left out and ends `OK (N gates left out)`; a name in `SKIP` or `ONLY` that is no gate, and an `ONLY` that
  selects no gate of the stage, end with exit code 2 instead of passing; a gate that raises fails with the traceback and
  the others of its group still report; a commit that only deletes files runs the gates over the whole project; a staged
  `tests/integration/` file runs `architecture`, and a staged document, `.env.example` or workflow runs `tests-fast`. The
  `shellcheck` and `actionlint` gates ignore `SHELLCHECK_OPTS` and a user-level rc file (a root `.shellcheckrc` stops that
  search), and the pinned tools are taken only from a virtual environment.
- `wheel` quality gate and `python -m scripts.quality wheel-check <dir>`: the wheel built from the checkout (or the one in a
  directory) must pass `uv lock --check`, `twine check` and `check-wheel-contents`, install on the locked dependencies into
  an environment of its own, run `axmem --help` and import `main` from another directory, and resolve with the extra index
  that the README documents, the three TensorRT packages coming from NVIDIA's index. It replaces the build, install, CLI
  and resolution steps of the `smoke-test` job of `ci.yml`, which only a workflow ran. It builds in a temporary directory,
  never in `dist/`. It needs the network, so it runs in `pre-push` and the `ci` stage and not in `pre-commit`.
- Quality gates run from one orchestrator, `scripts/quality`, used by the Git hooks and by hosted CI for its static gates: ruff, import-linter,
  bandit, semgrep, vulture, radon, mypy, pylint, syrupy snapshots, 100 % branch coverage, memray budgets, trivy,
  Qodana, mutmut and GitNexus (proposed upstream as #163).
- `GOVERNANCE.md`, `MAINTAINERS.md` and `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), adapted from the ones of Sankey Studio
  to Axmem: the roles and the access rules of the two maintainers, who decides, and how conduct reports are made and handled.
- `CONTRIBUTING.md`: how to report a bug and send a pull request, the rules for code,
  tests and commits, the license of contributions (`AGPL-3.0-only`), and the hardware policy: Axmem supports NVIDIA GPUs on
  Linux only, requests for other devices are not taken, and a pull request with code that works natively on macOS is the one
  way to add one.
- `package` quality gate and `python -m scripts.quality dist-check <dir>`: the wheel and the sdist hold Axmem's source and
  nothing else, so the model weights, the TensorRT engines and the NVIDIA libraries, which are not part of the AGPL
  release, cannot reach a package. The gate builds both from the files a build picks up (tracked, and untracked that git
  does not ignore) in a copy, and refuses any file that is not Python source under `app/` or `main.py`, a native library,
  a file above 256 KiB, a `.py` file with binary content, a package above 2 MiB, a wheel that is not pure Python, and
  license metadata or license files that differ from `pyproject.toml`. It runs in `pre-commit` (when a staged file goes into
  the package), in `pre-push`, in the `ci` stage and in the `smoke-test` job of `ci.yml`; `publish.yml` runs `dist-check` on
  the files it uploads, before the upload.
- Release to PyPI (`b9c5229`): `scripts/release-pypi.sh <version>` builds, tests, tags and signs a version, and dispatches the upload
  last, after `scripts/release/guard.py` has proved that the release commit is pushed and green in CI, the tag is on the
  remote, the `release.yml` run signed the files (cosign, keyless, one `.sigstore.json` bundle each, SLSA-3 provenance) and
  the signed files to publish are byte for byte the `release-digests.json` committed in the release commit. The release tag
  is a plain `v<version>`. The upload is made by `publish.yml` on `workflow_dispatch` on that tag, by trusted publishing in
  the GitHub environment `pypi` (tag rule `v*.*.*`, required reviewers, no admin bypass): it verifies the signed files
  again, leaves out the files PyPI lists already with the same sha256 (a half upload is finished with the missing file
  alone; one listed with other bytes fails the run), then uploads the rest. `release.yml` runs on the push of the tag only.
  Every action in every workflow is pinned to a commit. The script reads PyPI's public JSON until PyPI serves the manifest's bytes. No credential
  exists in the flow and no key of the local environment file is read. A re-run resumes from git, GitHub and PyPI. Before
  a release starts, an integrity check requires every version already on PyPI to have a pushed tag and a signed release, or
  to be named in `release-superseded.json`. The version comes from the tag (hatch-vcs) and each build pins it; the build
  backend (`hatchling`, `hatch-vcs`) is pinned to exact versions so that the local build and the rebuild of `release.yml`
  produce the same bytes. `check-package.sh` runs the package gate and then `wheel-check` (`twine check`,
  `check-wheel-contents` and a clean-environment smoke test), the code of the `wheel` gate. See [docs/release.md](docs/release.md).
- Release rehearsal on TestPyPI (`f966e7b`, `f95894d`, `09f79ab`, `57e5f20`, `1835c89`): `.github/workflows/publish-testpypi.yml` (dispatched on a release tag, by trusted
  publishing in the GitHub environment `testpypi`, `id-token: write` on its publish job alone, the same pinned upload action,
  `repository-url: https://test.pypi.org/legacy/`, no token, no password, no secret, no skip-existing) is `publish.yml` with
  the listed differences and no others: its jobs `verify` and `publish` upload the same signed bytes of the release of the
  same tag, after the same checks (sha256 against the manifest, the cosign identity of `release.yml`, the release guard, the
  package gate), and a test builds what the file must be from `publish.yml` and a closed table of differences.
  `scripts/release-testpypi.sh <version>` (library `scripts/lib/testpypi-flow.sh`) dispatches it for a release that
  `scripts/release-pypi.sh` has made up to the signing run, refuses unless the repository is set up for the environment
  `testpypi` (`testpypi-ready`, before anything is read), checks the signed files with the release guard, dispatches
  `publish-testpypi.yml` on the tag once (a live run is waited for, never a second dispatched), reads test.pypi.org until it
  lists the manifest's digests and downloads the files from test-files.pythonhosted.org to compare them. It makes no release
  (no commit, tag, push, build or stamp), holds its own lock so that it runs beside a release that waits for the approval of
  the environment `pypi`, never reads pypi.org and never dispatches `publish.yml`; its last line is "TestPyPI rehearsal
  complete: now approve environment pypi". The order of the two is by hand. A version TestPyPI holds with other bytes is spent
  on TestPyPI only. The guard half: `scripts/release/guard.py` gains `testpypi-ready`, `testpypi-state`,
  `testpypi-urls`, `testpypi-missing` and `testpypi-run`. They are the commands of the PyPI release with the index
  `testpypi` selected from a closed table of two rows in `scripts/release/facts.py` (PyPI: `pypi.org`, `publish.yml`,
  environment `pypi`; TestPyPI: `test.pypi.org`, `test-files.pythonhosted.org`, `publish-testpypi.yml`, environment
  `testpypi`), not copies of them: `testpypi-ready` runs every check of `publish-ready` (repository, environment, tag
  ruleset, SLSA generator tag) for the environment `testpypi`, and the state, urls and missing commands read the public JSON
  of test.pypi.org and refuse a file link that is not on its file host. The index is the name of the command, never an option,
  an input or an environment variable, and the PyPI commands and their messages are unchanged. `testpypi-run` adopts a live
  run of `publish-testpypi.yml` on the tag or dispatches one, and follows it (a cancelled run is a failure; a name TestPyPI
  refused as used says that the version is spent on TestPyPI and PyPI is unaffected). The rehearsal is advisory, not a gate,
  and needs a pending publisher on test.pypi.org (workflow `publish-testpypi.yml`, environment `testpypi`) and a GitHub
  environment `testpypi` set up like `pypi`. See [docs/release.md](docs/release.md).
- `commit-msg` hook (`95c1e5f1`): `[🤖 ]<type>(<scope>): <subject>`, subject up to 150 characters, body up to 10
  lines, AI Co-Authored-By trailers and "Generated with" lines rejected, `SKIP=commit-msg` to bypass, with tests.
- `denylist-check` hook script: `pre-commit`, `commit-msg` and `pre-push` refuse staged changes, messages and
  unpushed commits that match a term in the clone-local, untracked `.git/info/denylist`; `SKIP` does not bypass it.
- This changelog and [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md), with a rule in `AGENTS.md`
  that every commit that changes behaviour updates both.
- Optional `gateway` embeddings provider (`EMBEDDING_PROVIDER=gateway`, `0e50a541`) that posts JSON to an HTTP embeddings gateway. Settings: `GATEWAY_URL`, `GATEWAY_PATH`, `GATEWAY_TOKEN` (a secret value), `GATEWAY_AUTH_HEADER`, `GATEWAY_TENANT_HEADER`, `GATEWAY_TENANT`, `GATEWAY_BACKEND`, `GATEWAY_MODEL`, `GATEWAY_AGENT_ID`, `GATEWAY_METADATA` (a JSON object of strings) and `GATEWAY_SUPPORTS_DIMENSIONS`; none is used or required unless the provider is selected (they are loaded with the other settings, and `GATEWAY_METADATA` is parsed by the adapter alone, so a blank or malformed value cannot stop a server that uses another provider). Texts go out in requests of at most 256, each with a fresh `request_id`, an `X-Idempotency-Key` that a retry of the same chunk reuses and an `X-Correlation-Id` shared by one call. Statuses 429 and 503 are retried after `Retry-After` (at most 30 s), 502, 504 and network errors with a growing wait, four attempts in all; 403, 422 and every other status fail at once, with the status and `code` in the error and never the body. A reply is refused unless it has one finite vector per text and each is `EMBEDDING_DIMENSIONS` wide. Tests run against an in-process fake gateway.
- `generate_embeddings(texts)` on the `EmbeddingsAdapter` contract and on every provider (Azure, Google, OpenAI and Ollama embed one text per request; the FastEmbed provider that embedded a batch natively was removed, see Removed). `ReEmbeddingService` embeds each batch of memories with one call; a failed batch in `rebuild_embeddings` is retried memory by memory, so one rejected memory is still reported as `embedding_failed` alone.
- Direct TensorRT 11 reranker (`RERANKING_PROVIDER=TensorRT`, the default; `d77aedcb`), strict fp32, run on the NVIDIA GPU with
  the CUDA Python bindings and no ONNX Runtime. It fetches `onnx/model.onnx` and `tokenizer.json` once into
  `RERANKING_MODEL_DIR` (pinned revision for the default model, SHA-256 manifest) and builds one engine per
  fingerprint (model SHA-256, TensorRT version, GPU name, compute capability, driver major version, TF32, optimization
  profile of batch 1 to 64 and sequence 2 to 512) in `RERANKING_ENGINE_CACHE_DIR`, behind a file lock, with sibling
  fingerprints pruned and logged. Startup warms the engine with one scored pair and logs the duration and whether the
  cache was hit or why it was rebuilt. New settings: `RERANKING_TF32` (default `false`; fp16 is not supported at all),
  `RERANKING_MODEL_DIR`, `RERANKING_ENGINE_CACHE_DIR`; `RERANKING_WORKERS` now sets the execution contexts, each
  about 1.8 GiB of GPU memory beyond the first. A server or CLI command that cannot run the engine refuses to start
  with `Error: the reranker cannot start: <cause>` and exit code 1 (no GPU, driver older than R580, TensorRT or CUDA
  libraries missing, model unavailable, build failure). New [docs/gpu-models.md](docs/gpu-models.md) (host
  requirements, TensorRT pin and bump policy, TF32, engine cache, GPU memory); dependencies `tensorrt-cu13`,
  `tensorrt-cu13-bindings`, `tensorrt-cu13-libs` (11.4.0.106, from the NVIDIA index), `cuda-python`, `tokenizers`,
  `huggingface-hub` and `numpy`.
- GPU preflight for the integration, SQLite e2e and PostgreSQL e2e suites (`tests/gpu_preflight.py`): the pytest run
  ends with exit code 2 and the cause when no GPU is visible, TensorRT cannot be imported, or an xdist worker count
  exceeds the GPU memory. New tests run the real engine: golden scores, chunking, concurrent workers, engine cache
  rebuild and pruning, damaged plans, and subprocess runs with `CUDA_VISIBLE_DEVICES=-1` and with the TensorRT
  libraries hidden.
- In-process TensorRT embeddings provider (`EMBEDDING_PROVIDER=TensorRT`, the new default; `5b047f2b`), strict fp32, on the
  same runner, engine cache, fingerprint, warm-up and startup refusal as the reranker. The model is
  `BAAI/bge-small-en-v1.5` (its `onnx/model.onnx` and `tokenizer.json` are byte-identical to the Xenova export; both
  repositories are pinned and the SHA-256 is recorded), with CLS pooling, L2 normalisation, truncation at 512 tokens,
  padding to the longest text of a batch and an engine profile of batch 1 to 64 and sequence 2 to 512; more than 64
  texts are embedded in chunks of 64, on one execution context and one worker thread. `generate_embeddings` is native.
  New settings: `EMBEDDING_TF32` (default `false`), `EMBEDDING_MODEL_DIR` and `EMBEDDING_ENGINE_CACHE_DIR`.
  `EMBEDDING_DIMENSIONS` (default 384) is checked at start: a model that produces another width refuses to start with
  the cause `dimensions` and releases the engine. A server, `--re-embed` or other command that cannot run the
  embedder exits with code 1 and `Error: the embedder cannot start: <cause>`. The embedder and the reranker share one
  CUDA context; the server logs the start-up line of each and the combined GPU memory of the process (about 4.1 GiB
  for the pair). Offline hosts copy `EMBEDDING_MODEL_DIR` and set `HF_HUB_OFFLINE=1` (`docs/gpu-models.md`).
  `docs/embedding_migration.md` gains the FastEmbed to TensorRT and wrong-width cases.
- Standalone end-to-end test (`tests/e2e/test_standalone_e2e.py`): the real server in a subprocess with the
  GPU models, a static token, no `GATEWAY_*` variable and `HF_HUB_OFFLINE=1`, behind a guard that refuses and records
  every connection that is not loopback (its Qdrant node is on loopback), runs create, auto-link, a reranked query,
  link and mark-obsolete.
- Tenants and static bearer tokens (`3201a2c3`). `AUTH_MODE=static` maps a fixed bearer token to one tenant: `AUTH_STATIC_TOKEN_HASHES` holds only the SHA-256 hash per tenant (never the token), a presented token is hashed and compared with every hash in constant time, one token belongs to one tenant, and a token never reaches a log line. For `jwt` and `introspection` the tenant is the claim `tenant_id`, else `tenant`, else `sub`; a blank claim falls through, and a claim of another type or over 255 characters is refused. `DEFAULT_TENANT` names the tenant of `AUTH_MODE=none`. Tests cover each mode (valid, invalid, expired and missing token), the claim order, the hash comparison and tenant isolation over REST, MCP and the CLI.
- `project` argument (a free-form string, trimmed, 1 to 500 characters, created on first use) on `create_memory`, `update_memory` (an empty string clears it), `query_memory`, `get_recent_memories` and `rebuild_embeddings`, the `project` field and query parameter of the REST memory routes, and `-p/--project` on the CLI memory verbs.
- Qdrant as the only store, authenticated with a Qdrant API key. A memory is a point (vector and payload) in
  `<prefix>_memories`, an alias onto the live generation `<prefix>_memories_<stamp>`; a link is a point in
  `<prefix>_links` whose id is a deterministic UUIDv5 of tenant and pair. New settings: `QDRANT_URL` (one node, or the
  nodes of one cluster separated by commas; default `http://127.0.0.1:6333`), `QDRANT_API_KEY` (a secret value),
  `QDRANT_COLLECTION_PREFIX` (default `axmem`), `QDRANT_SHARD_NUMBER`, `QDRANT_REPLICATION_FACTOR`,
  `QDRANT_WRITE_CONSISTENCY_FACTOR`, `QDRANT_TIMEOUT_SECONDS` and `QDRANT_WRITE_ATTEMPTS`; the cluster profile
  (3, 3, 2) is documented in [docs/configuration.md](docs/configuration.md#qdrant-configuration). At start every node
  must report Qdrant 1.19.0 or later, the collections are created, and an existing collection whose width, shard
  count, replication or payload indexes differ from the settings stops the server with a message that names the
  difference. Requests spread over the nodes and move on when one cannot be reached; a write that fails ambiguously
  is repeated with the same claim token, so it takes effect once. Every read and write goes through `QdrantStore`,
  which adds the tenant condition (proved for every repository method by `tests/integration/test_tenant_chokepoint.py`);
  import-linter lets only `client.py`, `schema.py` and `store.py` import `qdrant_client`, and two semgrep rules
  (`forgetful-qdrant-write-outside-store`, `forgetful-qdrant-write-without-wait`) replace the SQL rule. Edits and
  obsolete toggles are writes conditional on the version the writer read, with the claim token read back to learn
  whether the write won; a lost race is repeated against the new state up to 5 times and then fails with
  `ConflictError` (REST 409, MCP tool error).
- Backups as Qdrant snapshots plus a JSONL export of the memories without vectors, from the CLI and nowhere else:
  `forgetful backup [--force]` (a snapshot of the live memory collection and the links from every node into
  `BACKUP_DIR`, directory `0700` and files `0600`, with a manifest of SHA-256 sums; it refuses when the replicas of a
  replicated deployment disagree unless `--force`), `forgetful restore <dir> --yes` (stop the server first; the
  restored memory collection becomes the live one), `forgetful export <file>` and `forgetful import <file>` (the
  import embeds every memory again with the configured provider, leaves an existing id untouched and can be repeated;
  a line it cannot read is skipped and counted) and `forgetful qdrant-check` (reads each node on its own and reports
  the points whose version, claim token or obsolete flag differ; exit code 1 on a difference). The check reports
  only; repairing a replica is an operator decision.
- Three-node cluster end-to-end suite (`tests/cluster`, marker `cluster`, excluded from a plain `pytest` run) and the
  `e2e-qdrant` gate that runs it and appends its coverage: replication factor 3, write consistency factor 2, strong
  ordering, an API key and internal authentication. It checks the layout, the replication of writes, writes and reads
  while a node is down, races of writers spread over the nodes, and that the race detector reports a writer that
  checks on the client.

### Changed

- **Release:** `publish-ready` (`3f7074f`), the first check of `scripts/release-pypi.sh`, also stops a release whose SLSA generator tag was
  moved. The generator can only be referenced by tag (its verifier needs one), so `release.yml` records the commit that
  `v2.1.0` pointed to (`SLSA_GENERATOR_COMMIT`) and `publish-ready` compares it with what the tag points to on GitHub, through
  the tag objects in between, and names both commits when they differ. A tag moved after the check is not seen.
- **Quality gates:** `mutation` runs in every `pre-push`, like every other gate; the opt-in (`MUTATION=1`) is gone, and only
  `SKIP` and `ONLY` leave it out. The gate mutates the `app/` modules that the push adds to the upstream of the branch
  (`git rev-parse @{upstream}`, else `main` of the remote being pushed to, which the hook passes as `QUALITY_PUSH_REMOTE`, else
  `origin/main`, else `HEAD~1`; the diff was against `origin/main` alone, which in a clone
  whose `origin` is not the tracked remote holds the whole history), and reports `SKIP` only when that diff holds no `app/`
  module; `MUTATION_TARGETS` still overrides the diff. The `gitnexus` gate of `pre-push` compares with the same base. A
  `mutmut run` that reaches its limit now stops `mutmut` with its test workers (its own process group) and says
  `mutation exceeded <cap> s` and how to go on (`MUTATION_TIMEOUT_S`, `MUTATION_TARGETS` or `SKIP=mutation`; a rerun
  resumes from `mutants/`); the limit is 24 hours instead of two, because a push of a branch that changes dozens of
  `app/` modules cannot finish in two, and `MUTATION_TIMEOUT_S` (a positive whole number of seconds, up to 2147483) changes
  it, an invalid value failing the gate instead of falling back; a diff that git cannot make fails the gate instead of
  reading as no change. The mutation sandbox now holds `CHANGELOG.md`, which two documentation tests read, and mutmut's
  run leaves out the one test that starts `main.py` in a scratch directory, and an integration test no longer leaves the
  queued logging of the commands running for the tests after it (mutmut runs them in an order of its own), so its baseline
  no longer fails; mutmut forks its workers (three at most, as each holds the TensorRT engines) from a fork server that has
  not started CUDA, which fails in a fork; `test_mutation_sandbox.py` also finds a root file that a test names in a list; the workers
  no longer leave their pytest temp directories behind (a worker ends with `os._exit`, so pytest never cleaned them up, and a run of
  14,000 mutants filled two disks with the Qdrant backups the tests had written); the test suites run with a `XDG_CONFIG_HOME` of their own, so a mutant that ignores the `--env-file` of a test cannot write `AXMEM_SERVER` to the settings file of the user (one did, and every CLI test after it failed on a server that does not exist).
- **Quality gates:** the GPU preflight of the test suites exits with code 1 instead of 2 inside a mutmut worker (`d391519`; the
  environment has `MUTANT_UNDER_TEST`); everywhere else it still ends the run with 2. mutmut books exit code 2 as "check was
  interrupted by user", so the mutants of `probe_gpu` and `_unpacked` that make the preflight fail could never be killed.
  mutmut runs the suite unmutated first and stops when that fails, so a machine whose GPU is unusable never reaches a mutant.
- **Quality gates:** the `mutation` gate runs `mutmut run` with a data directory of its own: `XDG_DATA_HOME` names a temporary
  `axmem-mutation-data-*` directory, removed when the run ends, whose `axmem/models` mirrors the model cache of the user (real
  directories, files up to 1 MiB copied, `model.onnx` and `engine.plan` as links). The tests still load the cached models and
  TensorRT engines, so nothing is downloaded or built again, but a mutant that changed a path constant (the directory name of a
  model, the fingerprint of an engine) used to write beside them in `~/.local/share/axmem/models` (128 MB per directory), and the
  engine cache removes every fingerprint but its own, so a mutant could remove the engine that the next run loads. Such a mutant
  now downloads the model and builds the engine again inside the temporary directory.
- **Quality gates:** the `semgrep` gate adds the registry packs `p/python p/security-audit` by default (they ran only in
  `quality.yml`, through an environment variable; a scoped `pre-commit` run keeps to the local rules, `SEMGREP_PACKS` in the
  environment replaces them, empty leaves them out). Staging the configuration of a gate (`pyproject.toml`, `uv.lock`,
  `ruff.toml`, `radon.cfg`, `pytest.ini`, `vulture_whitelist.py`, `trivy.yaml`, `.trivyignore`, `.semgrepignore`,
  `.shellcheckrc`, `.semgrep/`) makes `pre-commit` check the whole project, where the scoped gates reported `SKIP`; a commit
  that stages only a workflow, a hook, `pyproject.toml` or `uv.lock` now meets the `architecture` gate, and one that stages
  only a snapshot file meets `snapshots` and `tests-fast`. `tests-fast` no longer runs `tests/architecture` (the
  `architecture` gate does). `ci.yml` runs the `ruff`, `architecture`, `package` and `wheel` gates by name, no longer
  `ruff check .` and `pytest tests/architecture`, and the docs tell contributors to run `gate ruff`, which uses the pinned
  ruff, instead of `uv tool run ruff check .`.
- **CI:** the `smoke-test` job of `ci.yml` checks out the commit alone: its `fetch-depth: 0` (the full history, kept for
  hatch-vcs to read the tags when the job built the wheel itself) is gone, as the commit that moved the build into the
  `package` and `wheel` gates left it behind (`98a81ad5`). Both gates build a copy of the checkout that has no git history
  and pin the version with `SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0`, and the project install of `uv sync` is the one the
  `lint` job already makes on a shallow checkout, so the job needs no tag. `docs/quality-gates.md` now says so, and that the
  `commit-msg` hook runs `denylist-check` on the message before anything `SKIP=commit-msg` leaves out.
- **License:** Axmem is released under the GNU Affero General Public License, version 3 (`AGPL-3.0-only`), no longer under
  the MIT License. The MIT License of Forgetful (Copyright (c) 2025 Scott Raisbeck) allows this; its notice moved unchanged
  to `LICENSE-FORGETFUL-MIT.md` (it was `LICENCE.md`) and the AGPL text is in `LICENSE`. `pyproject.toml` declares
  `AGPL-3.0-only AND MIT` (PEP 639) with both files as `license-files`, and the sdist include list now names a `LICENSE` that
  exists (it named a file that did not). The models and the NVIDIA libraries are fetched by the user and are not part of the
  release; [docs/gpu-models.md](docs/gpu-models.md) lists their licenses, including that the default reranker was trained on
  MS MARCO, whose terms say non-commercial research only. The skills are under the AGPL too (`license: AGPL-3.0-only`, they
  said `MIT`), the copyright of Axmem's own code is Giovanni Manzoni's (README, `authors` in `pyproject.toml`), and the sdist
  `include` list is anchored (`/README.md`, not `README.md`), which kept the README files of `docs/`, `skills/` and
  `test_harness/` out of the sdist.
- Project renamed to Axmem under the Axiumine organisation; README rewritten, Forgetful credited, MIT notice
  kept (`b121d350`). The package, CLI and environment variables were renamed afterwards, see the rename entry below.
- Python 3.14 is the only supported version: `requires-python`, classifiers, tool targets, CI, Dockerfiles and Nix
  (`1eea81a5`); the `compat` gate is gone; 142 ruff findings from the new target were auto-fixed (`c78d3f98`).
- The environment reference moved from `docker/.env.example` to `.env.example` in the repository root, where the
  `QODANA_TOKEN` key of the quality gates joins it (commented out, because an empty value counts as set). It now
  shows the native defaults: SQLite, PostgreSQL on `127.0.0.1`, no `SQLITE_PATH` and no compose settings. Settings no
  longer read `docker/.env`; the files read are `.env` in the working directory and `~/.config/forgetful/.env`.
  The `SERVER_HOST` default stays `0.0.0.0` (its comment no longer calls it the container default).
- The PostgreSQL e2e fixture creates the `forgetful-db` test container with `docker run` (image
  `pgvector/pgvector:pg16`, volume `forgetful_db_data`) from the `POSTGRES_*` and `PGPORT` settings instead of the
  removed compose file; an existing container is reused or restarted.
- Coverage data is written to `.coverage-data/` (`[tool.coverage.run] data_file`) instead of the repository root,
  because `parallel = true` makes every measured process write its own `.coverage.*` fragment, and interrupted
  runs left them behind. `.coverage-data/` is ignored by Git, semgrep, trivy, Qodana and radon.
- NVIDIA GPU is a hard requirement for development and runtime; there is no CPU path and no skip. The reranker,
  the default embedder, the test suites and, with the removal of FastEmbed, every local model are on the GPU.
  The remote embeddings providers, `RERANKING_PROVIDER=HTTP` and `RERANKING_ENABLED=false` are the explicit ways
  to run without one.
- Embeddings are computed in the server process on the GPU by default (`EMBEDDING_PROVIDER=TensorRT`) instead of
  coming from a remote embeddings service. The `gateway` provider stays optional and no `GATEWAY_*` setting is used
  unless it is selected. `EMBEDDING_PROVIDER` defaults to `TensorRT`
  (it was `FastEmbed`) and an empty value selects it. A database filled by the old default needs `--re-embed`,
  because TensorRT's vectors come from another runtime and are not identical (`docs/embedding_migration.md`); the
  command is also needed whenever the provider, model or width changes. `RerankerError` and `RerankerStartupError` are now `TensorRTError` and
  `TensorRTStartupError` (they cover both models), `reranker_model.py` is `tensorrt_model.py` and
  `docs/gpu-reranking.md` is `docs/gpu-models.md`.
- Users become tenants and projects become an optional scope string created on first use. The stored `users` row is the tenant record (matched by name in `external_id`) and `projects` is the table behind the scope, because the storage layer is replaced later and not migrated; no revision touches either table. A scope belongs to one tenant, so two tenants that use the same label have two scopes. Memory responses carry `project` (a string or null) instead of `project_ids`, and `ObsoleteMatch` does the same. The `AUTH_MODE` setting replaces `FASTMCP_SERVER_AUTH`, `DEFAULT_TENANT` replaces `DEFAULT_USER_ID`, `DEFAULT_USER_NAME` and `DEFAULT_USER_EMAIL`; an existing deployment keeps its memories by setting `DEFAULT_TENANT` to the old `DEFAULT_USER_ID` value. `GET /api/v1/auth/info` reports `authMode` as `disabled`, `jwt`, `introspection` or `static`. The tool scopes `users` and `projects` no longer exist (`read:memories` and `write:memories` remain). `.env.example` now sets `AUTH_MODE=none` and `SERVER_HOST=127.0.0.1`. The stdio and the HTTP examples in the README and the connectivity guide pass `AUTH_MODE=none` or a bearer token. The tool count goes from 17 to 10. The test harness server runs with `AUTH_MODE=none` on its loopback bind.
- Memory ids are UUIDs (UUIDv7, so they sort in creation order) in the REST routes, the MCP tools and the CLI, because
  Qdrant has no autoincrement. A number is refused (`memory_id must be a valid UUID`, or `ids are UUIDs` on the CLI) and a
  path id that is not a UUID is 404; the upper-case spelling, the form without hyphens and surrounding whitespace are
  accepted. `linked_memory_ids`, `superseded_by`, `related_ids` and `memory_ids` carry UUID strings.
  The CLI argument tree and settings defaults snapshots are refreshed.
- Tenants are stateless: a tenant's id is the UUIDv5 of its name under a fixed namespace, so there is no tenant store,
  no `TenantService` and no `users` table. Changing the namespace (`TENANT_NAMESPACE` in `app/models/tenant_models.py`)
  would re-key every tenant.
- Re-embedding (`forgetful --re-embed`, or `forgetful re-embed`) copies the memories, embedded again, into a new
  collection and moves the live alias only after the count, dimension and search checks pass. A failure or an
  interrupt discards the new collection and leaves the live one untouched, so there is no backup to take or restore
  first; the previous collection is kept after a success and is deleted by hand. `--dry-run` is read-only and creates
  nothing. Edits made while the copy runs are picked up by further scans, and writes into the new collection are
  conditional on the version. The live collection may hold vectors of the old width while this runs; the server
  refuses to start on a width mismatch. [docs/embedding_migration.md](docs/embedding_migration.md) is rewritten.
- The integration and end-to-end suites run on a real Qdrant node that `tests/qdrant_support.py` starts through Docker
  (`qdrant/qdrant:v1.19.2`) with an API key, once per pytest process, or on a server named by `AXMEM_TEST_QDRANT_URL`.
  The in-memory repository stubs are deleted (they would repeat Qdrant's filter, ordering and conditional-write
  semantics), `tests/e2e_sqlite` is merged into `tests/e2e`, the redundant `test_auth_cache_e2e.py` is deleted, and
  tests stay apart by tenant namespace (collections are shared) or use their own collections where counts or
  generations matter. New tests prove the tenant chokepoint, the conditional writes (barrier races, lost
  acknowledgements, a detector that must report a check-then-write updater), the pool, the schema checks, the store
  guards, the archive format, the cluster check and the operator commands. The snapshot gate pins the collection layout
  and the payload types instead of the SQL DDL and the Alembic chain. The standalone end-to-end test runs on Qdrant.
  The GPU preflight and the Docker preflight apply to every suite and never skip.
- `BACKUP_DIR` now says where `forgetful backup` writes (an export writes the file named on its command line); the database backup that `--re-embed`
  made is gone with the database. `.env.example` shows the Qdrant settings instead of SQLite and PostgreSQL.
- The integration and end-to-end suites run the real TensorRT embedder and reranker on the GPU through the session
  fixtures `tensorrt_embedder` and `tensorrt_reranker` (the production adapters; there is no test-only embedder), with
  no stub embedder or reranker, fake module, `gpu` marker or skip. The embedding stubs (`MockEmbeddingsAdapter`,
  `BatchViaSingle` and the per-file hashing, constant and fixed-vector adapters) are gone: tests that depend on
  similarity (the 0.7 auto-link cutoff, the 0.89 obsolete warning, ordering of results, duplicates) use clearly
  similar and clearly unrelated texts, whose measured cosines `tests/integration/test_embedding_texts.py` pins and
  `docs/contributors.md` lists. HTTP mock transports and in-process fake endpoints for the remote providers, spies
  and wrappers that delegate to the real adapters (fault injection on cue, added latency, a vector cut to another
  width) and fault injection in the engine (a barrier around `TensorRTEngine.infer`, hidden libraries in a
  subprocess, one invalid `cudaMemcpy`) remain. The suites that run these engines need an NVIDIA GPU, which hosted runners do not have, and the project has no
  self-hosted runner: they run on the developer machine in the `pre-push` hook (see the CI entry under Fixed). The
  static gates, the architecture tests, `trivy` and the wheel smoke test run on hosted runners, and the static job
  installs without the TensorRT libraries. The Windows leg of `ci.yml` is gone.
- `RERANKING_PROVIDER` accepts `TensorRT` (default) and `HTTP`; `FastEmbed` is now rejected at startup with an error
  naming the setting. The reranker needs an NVIDIA GPU; `RERANKING_ENABLED=false` or `RERANKING_PROVIDER=HTTP` runs
  without one. The reranker is Linux-only (the engine cache locks with `fcntl`) and the package classifier reads
  `Operating System :: POSIX :: Linux`. `docs/OFFLINE_SETUP.md`, `docs/configuration.md`, `docs/search.md`,
  `docs/contributors.md`, `docs/quality-gates.md`, `.env.example`, `README.md` and `architecture.md` describe the new
  reranker; the reranker model no longer lives in the FastEmbed cache and `FASTEMBED_LOCAL_FILES_ONLY` does not
  affect it.
- Renamed from `forgetful` to `axmem` everywhere except attribution and history, so Axmem no longer shares a name with
  the project it is derived from:
  - Package and console script: the distribution is `axmem` and `axmem` is the only console script (`forgetful` and
    `forgetful-ai` are gone); the CLI program name, help texts and messages say `axmem`; the project URLs point at
    `https://github.com/Axiumine/axmem`; `uv.lock` is regenerated for the new name.
  - Environment variables: `FORGETFUL_SERVER`, `FORGETFUL_TOKEN` and `FORGETFUL_SCOPES` become `AXMEM_SERVER`,
    `AXMEM_TOKEN` and `AXMEM_SCOPES`, and the Nix build variable `FORGETFUL_VERSION` becomes `AXMEM_VERSION`. The old
    names are not read, so a deployment that sets them runs with the defaults (local mode, no token, scope `*`)
    until it is changed. An instance that sets `FORGETFUL_SCOPES` without `AXMEM_SCOPES` refuses to start (see the
    review fixes), so set `AXMEM_SCOPES` before the upgrade.
  - Directories: the user config directory is `~/.config/axmem` and the data directory `~/.local/share/axmem`
    (models and engine caches under `models/`); nothing is read from or moved out of the `forgetful` directories, so
    an existing config file has to be moved and the models are downloaded and built again. The git-ignored local
    state directory is `.axmem/`.
  - MCP: the server name (`SERVICE_NAME`, default `Axmem`; `SERVICE_DESCRIPTION` likewise) and the three
    meta-tools `discover_axmem_tools`, `how_to_use_axmem_tool` and `execute_axmem_tool` (were `discover_forgetful_tools`,
    `how_to_use_forgetful_tool` and `execute_forgetful_tool`); clients that registered the server as `forgetful`
    must register it as `axmem` (the tool prefix a client shows becomes `mcp__axmem__`). The example project and
    repository names in the tool catalogue are `axmem` and `axiumine/axmem`.
  - Code and tooling: `ForgetfulMCP` is `AxmemMCP`, `ThrowawayForgetful` is `ThrowawayAxmem`; the agent skill
    directories are `skills/axmem-*` and the harness image, container prefix and label are `axmem-test-harness`,
    `axmem-uat-` and `axmem.harness.requirements-hash`; the semgrep rules are in `.semgrep/axmem.yml` with `axmem-`
    ids; the trivy cache is `~/.cache/axmem-trivy`; the Nix package, app and wrapper are `axmem` (the `forgetful`
    symlink is dropped); the agent files for Copilot CLI, Gemini CLI and OpenCode are named `axmem-*`.
  - Tests and snapshots: names follow the code; four snapshots were refreshed and the diff holds only the renamed
    names (`SERVICE_NAME`, `SERVICE_DESCRIPTION`, the `AXMEM_*` settings, the CLI program name, the meta-tool names and
    the example values).
  - Docs: README, `architecture.md`, `docs/`, `skills/` and the environment reference use the new names. The word
    Forgetful stays only in the README attribution, `LICENSE-FORGETFUL-MIT.md`, this history, `docs/changes-from-forgetful.md` (and
    links to it). The architecture image of the upstream project is replaced by a Mermaid diagram in the README
    (the two `Forgetful Architecture` images are deleted). `AGENTS.md`
    describes the Qdrant test suites instead of the SQLite and PostgreSQL ones, and its GitNexus section no
    longer names the index.
- Axmem is documented as a standalone memory server. The README opens with what it needs (Qdrant, an NVIDIA GPU with
  the model files, an `AUTH_MODE`), says that remote embeddings, a remote rerank service and token providers are
  optional integrations nothing contacts unless selected, and says what it does not do (no plans, tasks, skills,
  files, documents, entities, activity stream or user and project registries). `docs/changes-from-forgetful.md`
  states the reason for each cut without naming a system outside the repository, and its Tests row is Done: the
  contract tests of the `gateway` adapter run against an in-process fake gateway. The `axmem-remember` skill no longer
  sends work items to a plan or task store. Documentation only; no setting, route, tool or schema changed.

### Removed

- The `git-hooks.nix` hooks of `flake.nix` (`nixfmt`, `deadnix`, `statix`, `ruff`, `typos`), the `nix fmt` formatter and the
  `pre-commit` check of `nix flake check`, with the hook installation of the Nix dev shell: they were checks that no
  hook gate ran. `.githooks/` is the only mechanism.
- Graph REST view (`GET /api/v1/graph`, `/graph/subgraph`, `/graph/memory/{id}`), `GraphService`, the graph models,
  the `MAX_GRAPH_LIMIT` setting and the subgraph SQL on both backends (`6ce740e7`). Memory links, auto-linking,
  the link tools and the `query_memory` link expansion stay. No schema change.
- Plans and tasks: services, repositories, models, REST routes, 15 MCP tools and the `PLANNING_ENABLED` flag
  (`59f3f479`). Revision `20261019_drop_planning` drops their tables and activity rows; the downgrade recreates
  the empty tables.
- Skills: service, repositories, models, REST routes, 17 MCP tools, `SKILLS_ENABLED` and the `SKILL_*` settings,
  skill import/export, `skill_ids` on memories and the skill handling in re-embedding (`e77b50e0`). Revision
  `20261019_drop_skills` drops the tables and activity rows; the downgrade recreates the empty tables. The
  `forgetful-procedures` agent skill file is deleted and the other files in the `skills/` catalogue are edited;
  the catalogue stays.
- Files (`c2cbf05a`): service, repositories, models, REST routes, 5 MCP tools, `FILES_ENABLED` and the `FILE_*` settings,
  `file_ids` on memories and the `file` activity entity type, tool category and scope. Revision
  `20261019_drop_files` drops `files`, `memory_file_association` and `entity_file_association` (the stored binary
  content included) and the file activity rows; the downgrade recreates the empty tables. The `forgetful-files`
  agent skill file is deleted and the other files in the `skills/` catalogue are edited; the catalogue stays. No
  optional feature flag is left, so the SQLite e2e feature-flag registry and its test module are gone.
- Discord badge and banner image from the README, and the PyPI install option, because `forgetful-ai` is
  upstream's package (`b121d350`).
- Documents and code artifacts (`611e76d5`): services, repositories, models, REST routes (`/api/v1/documents`,
  `/api/v1/code-artifacts`), 10 MCP tools, the `DOCUMENT_*` and `CODE_ARTIFACT_*` settings, `document_ids` and
  `code_artifact_ids` on memories and the `document` and `code_artifact` activity entity types, tool categories and
  scopes. Revision `20261019_drop_docs_artifacts` drops `documents`, `code_artifacts`, `memory_document_association`
  and `memory_code_artifact_association` (the stored content included) and the document and code artifact activity
  rows; the downgrade recreates the empty tables. The memory tool docs no longer point long text at documents.
  Agent skill files and prompts in `docs/` that taught these tools are edited; the `skills/` catalogue stays.
- Entities and relationships (`5f7f1bd1`): service, repositories, models, REST routes (`/api/v1/entities`), 16 MCP tools, the
  `entity` tool category and scope, the `ENTITY_*` settings, the `entity`, `entity_memory_link`,
  `entity_relationship` and `entity_project_link` activity entity types, and the PostgreSQL column-width clamp that
  only guarded entity columns. The `linking` tool filter now selects the memory link tools only. Revision
  `20261019_drop_entities` drops `entities`, `entity_relationships`, `entity_project_association` and
  `memory_entity_association` and deletes the entity activity rows; the downgrade recreates the empty tables. The
  `forgetful-entities` agent skill file is deleted and the other files in the `skills/` catalogue, the prompts and
  the agent commands in `docs/` are edited; the catalogue stays. The tool count goes from 33 to 17.
- Activity (`373e7eb5`): `EventBus` with its SSE stream, `ActivityService`, both activity repositories and the `activity_log`
  table, the REST routes `GET /api/v1/activity`, `/api/v1/activity/{entity_type}/{entity_id}` and
  `/api/v1/activity/stream`, the `ACTIVITY_ENABLED`, `ACTIVITY_RETENTION_DAYS`, `ACTIVITY_TRACK_READS` and
  `SSE_MAX_QUEUE_SIZE` settings, the event emission in the memory and project services, and the one-shot CLI wait for
  pending event handlers before the database closes. The memory access handler is gone with the bus, so
  `access_count` and `last_accessed_at` have no writer or reader; they are removed from the memory objects that the
  REST routes and tools return. The unused `parse_datetime_param` query helper is deleted. Revision
  `20261020_drop_activity` drops `activity_log`, `memories.access_count`, `memories.last_accessed_at` and the
  `ix_memories_last_accessed_at` index; the downgrade recreates the empty table and the two columns (every memory
  reads as never accessed). Older environment files that still set the removed settings keep working, because
  unknown settings are ignored. The removal tests of the earlier cuts now check their own revision instead of the
  head, because the log no longer exists there.
- Container deployment (`36a25494`): `docker/` (the Dockerfile and the SQLite, PostgreSQL and development compose files),
  `.dockerignore`, the `build.yml` workflow that built, tested and pushed the image to a container registry, the
  `docker-offline` job of `ci.yml` with its `tests/docker/` image test, the `docker` output of `flake.nix` (the
  dev shell, the app package and the app stay), `docs/self-hosting-guide.md` and the Docker parts of the README,
  `docs/configuration.md` (including `COMPOSE_PROJECT_NAME` and `BIND_ADDRESS`, which only the compose files read),
  `docs/OFFLINE_SETUP.md`, `docs/embedding_migration.md`, `docs/nix.md` and `docs/contributors.md`. The compose
  files still pulled the upstream image and no Axmem image was ever published. Docker stays as a test dependency
  (the PostgreSQL e2e fixture, the quality gates that fall back to a tool image, the test harness image). No schema
  change.
- SQLite, PostgreSQL, pgvector, SQLAlchemy and Alembic: `alembic/` and `alembic.ini` (every revision, no migration
  is needed because Qdrant collections are created at start), the SQLite and PostgreSQL memory repositories and
  their sessions and adapters, `vector_ddl`, the database `backup_adapter` and its `pg_dump`/`psql` handling, the user
  and tenant services, repositories and models (the tenant record lives nowhere now), the settings `DATABASE`,
  `POSTGRES_HOST`, `PGPORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `SQLITE_PATH`, `SQLITE_MEMORY` and
  `DB_LOGGING` (older environment files that still set them keep working, because unknown settings are ignored), and
  the dependencies `aiosqlite`, `alembic`, `asyncpg`, `pgvector`, `psycopg2-binary`, `sqlalchemy` and `sqlite-vec`
  (`qdrant-client` is added). The `e2e-postgres` gate, the `forgetful-db` PostgreSQL test fixture and the SQLite
  e2e suite are replaced by the Qdrant fixture and the cluster gate. Data in an existing SQLite or PostgreSQL
  database is not migrated, and no earlier version writes the export format that `forgetful import` reads: re-create the memories.
- User and project tools, routes and CLI verbs: the MCP tools `get_current_user`, `update_user_notes`, `create_project`, `list_projects`, `get_project`, `update_project` and `delete_project`, the `user` and `project` tool categories, the REST routes `/api/v1/projects`, `ProjectService`, `UserService`, the project and patch models, the user notes and `idp_metadata` handling, the `PROJECT_DESCRIPTION_MAX_LENGTH` and `PROJECT_NOTES_MAX_LENGTH` settings, `project_ids` on the memory tools and routes, the `forgetful project list` command and the CLI project name resolver. The GitHub and Google OAuth providers, the `FASTMCP_SERVER_AUTH`, `FASTMCP_SERVER_AUTH_GITHUB_*`, `FASTMCP_SERVER_AUTH_GOOGLE_*`, `OAUTH_STORAGE_PATH` and `DEFAULT_USER_*` settings, the CLI browser login with its token cache, `forgetful auth logout`, and the `py-key-value-aio` dependency that stored the OAuth state. Agent commands, prompts, the connectivity guide and the files in the `skills/` catalogue that taught these tools are edited; the catalogue stays. No schema change and no migration: the `users` and `projects` tables stay.
- FastEmbed cross-encoder reranker: `FastEmbedCrossEncoderAdapter`, its DirectML handling, the `RERANKING_THREADS` and
  `RERANKING_ONNX_PROVIDERS` settings and the tests of that adapter and of its worker pool. Older environment files
  that still set the removed settings keep working, because unknown settings are ignored.
- FastEmbed, ONNX Runtime and the local CPU embedder: `FastEmbeddingAdapter`, `fastembed_offline.py`, the first-run
  model check, `parse_onnx_providers` and the `FASTEMBED_CACHE_DIR`, `FASTEMBED_LOCAL_FILES_ONLY` and
  `EMBEDDING_ONNX_PROVIDERS` settings, with the `fastembed` dependency and the packages that only it needed
  (`onnxruntime`, `flatbuffers`, `loguru`, `mmh3`, `pillow`, `protobuf`, `py-rust-stemmers`, `win32-setctime`).
  `EMBEDDING_PROVIDER` accepts `TensorRT` (default), `Azure`, `Google`, `OpenAI`, `Ollama` and `gateway`;
  `FastEmbed` is rejected at startup with an error naming the setting. Older environment files that still set the
  removed settings keep working, because unknown settings are ignored. A database filled by FastEmbed is moved with
  `forgetful --re-embed` ([docs/embedding_migration.md](docs/embedding_migration.md)). `docs/OFFLINE_SETUP.md` is
  deleted and its offline guidance is the "Model files and offline hosts" section of
  [docs/gpu-models.md](docs/gpu-models.md); the FastEmbed cache steps of the three workflows are gone. No schema
  change.
- `org_knowledge_proposal.md` (a first-person product note by the upstream author, about a different project),
  `_typos.toml` (it only excluded that note and allowed a string of a test that no longer exists) and the upstream
  Claude Code plugin section of `docs/connectivity_guide.md` (the plugin belongs to the upstream project and is
  installed under its name).

### Fixed

Fork fixes proposed upstream as pull requests #102 to #154 (none merged there when checked on 2026-10-19).
Fixes to features that are removed above are moot once those features go.

- The `trivy` job of `quality.yml` ran red on every push of `main` since the mutation gate got a data directory of its own:
  `scripts/quality/testing.py` imported `platformdirs` at module level, and the job runs `python -m scripts.quality` on a
  bare Python with no dependency installed, so it stopped with `ModuleNotFoundError`. The import is now in `models_dir`, the
  one function that needs it, and a test in `tests/architecture/test_workflows.py` imports the gates with `python -S` so the
  job cannot break this way again. A red `Quality gates` run on `main` also stopped the release script at its CI check (`353509a`).
- The `trivy` gate skips `mutants/`, the working copy that mutmut makes of the project. The copy is not tracked and holds its
  own `uv.lock` from the last mutation run, so after a lock change it still carried the old pins and the gate failed on
  findings that the real `uv.lock` no longer had (a push stopped on the old `authlib` and `dnspython` of that copy). A test keeps
  `mutants` in `skip-dirs` of `trivy.yaml` while `.gitignore` names it, and `trivy.yaml` and `.gitignore` join `also_copy` of
  `[tool.mutmut]`, so that the test also runs inside the mutation sandbox (`13b27e7`).
- The mutation sandbox copies `release-digests.json`. The file does not exist until the first release commit adds it, and
  from then on the `architecture` test that checks the sandbox found it named by `test_release_workflow.py` and refused the
  push of the release commit (`also_copy` of `[tool.mutmut]` did not list it); the first release stopped there, before anything
  was tagged or uploaded. mutmut skips an `also_copy` path that does not exist, so the entry is harmless before that (`1834ada`).
- **Security:** token scope fails closed (#107), ownership checks on link endpoints, referenced projects and
  embeddings (#103, #113), token cache honours expiry (#110), no SQL echo or parameters in logs (#143), server
  URLs redacted (#112), owner-only backups and storage (#137), user upsert keeps notes and metadata (#108),
  pyjwt, urllib3 and fsspec locked to releases with security fixes (#102).
- **REST and MCP input handling:** 400/404 instead of 500 for malformed ids, bodies, claims and query parameters
  (#153, #132, #122, #121, #109); memory tool argument validation (#148); sanitised meta-tool errors and logs
  (#149).
- **Memory and search correctness:** query token budget (#106), update consistency and embeddings (#126, #139),
  input validation and database-compatible limits (#124, #128, #150), link listing (#123), rerank honours `k`
  (#152), idempotent links (#147), tag filters (#136), read events (#151).
- **Startup, CLI and tooling:** configuration wiring and lifecycle (#154), migration runner hardening (#127),
  embedding adapter robustness (#114), CLI fixes (#111, #112, #140 to #142), test harness fixes (#119, #120,
  #144 to #146).

Fixes found by reviewing the feature cuts (no upstream counterpart: the code they touch exists only here):

- **Start-up:** a Qdrant node that does not answer is logged and skipped while another one does, so a cluster that
  lost a node still starts and can be inspected (the alias and collection reads try each node in turn; every node that
  answers must still be 1.19.0 or later; with no answering node the start is refused as before). Settings of earlier
  versions that are ignored now are named in one warning with what replaces each, read from the environment and from
  dotenv files: the SQL store (`DATABASE`, `DB_LOGGING`, `PGPORT`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PASSWORD`,
  `POSTGRES_USER`, `SQLITE_MEMORY`, `SQLITE_PATH`), the OAuth providers and `DEFAULT_USER_*`, the FastEmbed and ONNX
  Runtime settings (`FASTEMBED_*`, `EMBEDDING_ONNX_PROVIDERS`, `RERANKING_ONNX_PROVIDERS`, `RERANKING_THREADS`), the
  caps and switches of the removed features (activity, files, planning, skills, documents, entities, code artifacts,
  `MAX_GRAPH_LIMIT`) and `FORGETFUL_SCOPES`, `FORGETFUL_SERVER` and `FORGETFUL_TOKEN`. The list holds exact names
  matched without regard to case, as the settings are read: a name that merely shares a prefix (`POSTGRES_PORT`,
  `SQLITE_TMPDIR`, `DEFAULT_USER_AGENT`) belongs to other software and is no longer reported, and a bare dotenv key
  that sets nothing is not counted. An old `FORGETFUL_SCOPES` that nothing replaces now refuses the start of `serve`
  and of the tool commands that run in process (both build the runtime; `backup`, `restore`, `export`, `import`,
  `qdrant-check`, `re-embed` and the tool commands that call a remote server never use the scope ceiling and do not
  check it) and names both settings, instead of warning while the instance allowed every
  tool: set `AXMEM_SCOPES` to the scopes you want, or to `*` on purpose. Log records go to stderr during the TensorRT
  warm-up, so the engine build and the cause of a refusal are visible before logging is configured.
- **Authentication:** `AUTH_MODE=jwt` requires `FASTMCP_SERVER_AUTH_JWT_ISSUER` and `FASTMCP_SERVER_AUTH_JWT_AUDIENCE`
  and refuses to start without them (before, both were passed on as unset and a token that the same identity provider
  issued for any other application was accepted), and a signed token without an `exp` claim is refused (it never
  expired). `AUTH_MODE=introspection` checks the `aud` of the endpoint's answer against the new
  `FASTMCP_SERVER_AUTH_INTROSPECTION_AUDIENCE` when it is set (a string or a list that names it); without it the
  audience is not checked, and the issuer never is, because the endpoint is the authority. `nbf` is not checked in
  either mode. In introspection mode the REST token cache is no longer stacked on the verifier's own cache, which
  made a revoked token valid for up to twice `TOKEN_CACHE_TTL_SECONDS`; the verifier's cache is the only one, for MCP
  and REST alike. The configuration reference says exactly what each mode checks. A mode whose settings are incomplete (`jwt`
  without an issuer and an audience, `static` without token hashes, `introspection` without its endpoint) refuses
  only the server: `axmem serve` prints `Error: <cause>` and exits with code 1, and `backup`, `export`, `re-embed` and
  the tool commands, which never serve a request, run as before. The provider is built when `main` is imported, and a
  mode that lacked settings used to end every command in a traceback from that import.
- **REST routes honour the scopes:** the memory REST routes ignored `AXMEM_SCOPES` and the scopes of the token, which
  only the MCP meta-tools enforced, so a read-only instance or token could still create, change, link and obsolete
  memories over REST. Each route now stands for a registry tool (`GET /api/v1/memories` is `get_recent_memories`,
  `POST /api/v1/memories` is `create_memory`, and so on, listed in the API reference) and answers `403` with the same
  message as the MCP call when the instance ceiling or the token's scopes do not permit that tool; the credential is
  still checked first (`401`). A token whose scopes are only a verifier requirement such as `api:read` (no Axmem scope) was
  already refused every tool over MCP and now is over REST too, so it needs an Axmem scope such as `read` or `*`. The cached
  identity of a token keeps its scopes. A route cannot be registered without a
  tool (a start-up error), and an end-to-end test fails for any route that is neither mapped nor public.
- **Request bodies must be JSON:** a REST request that carries a body without a JSON content type answers `415`
  before the body is read. A web page can send `text/plain` cross-origin with no preflight, and the routes used to
  parse such a body as JSON.
- **No credential, no web page:** with `AUTH_MODE=none` the HTTP server (REST and `/mcp`) answers `421` to a request
  whose `Host` is not `localhost` or a loopback address (DNS rebinding) and `403` to one whose `Origin` is neither a
  loopback origin nor an origin named in `CORS_ORIGINS` while `CORS_ENABLED=true` (a `*` never counts, `null` is
  refused, and with CORS off no origin counts). The new setting
  `AXMEM_ALLOWED_HOSTS` (a JSON list, empty by default) names further hosts that the server answers, such as
  `host.docker.internal` for a client in a container, which the test harness' throwaway server now lists. Clients that
  are not browsers send neither a foreign `Host` nor an `Origin` and are not affected. The credential modes are
  unchanged.
- **Operator commands:** `axmem import` creates the collections when they are missing (a new Qdrant), and
  `axmem export` and `axmem backup` on a store without collections stop with `The alias <name> does not exist yet`
  instead of a raw 404; an export reads the live generation it resolved first. `--re-embed` that fails or is
  interrupted after the alias switch had begun says that the live collection may already be the new one, instead of
  saying it was discarded.
- **Memory correctness:** the vector read for similar-memory search and the existence check for new links use quorum
  reads, so a write is visible to the next request on any node of a cluster; a list with an offset at or past the
  end answers an empty page with the total, without reading the collection; memory ids in the tool examples are
  UUIDs.
- **Gateway provider:** `GATEWAY_METADATA` is plain text that the adapter parses (`{}` by default, a blank value is
  `{}`; anything but a JSON object of string values is refused with a message naming the setting), so a malformed
  value no longer stops the settings from loading. A refusal that concerns the whole call (status 401, 403 or 404,
  retries used up, a reply of the wrong count or width) raises `EmbeddingUnavailableError`, and a targeted rebuild
  records the batch as failed at once instead of retrying every memory against a gateway that is down.
- **TensorRT:** an ONNX graph with a floating-point tensor that is not fp32 is refused (`build-failed`), because the
  builder would otherwise run it in a lower precision; NVML failures, an unreadable model manifest or a model
  directory that cannot be written, and an engine file that cannot be read are start-up errors with a cause
  (`gpu-error`, `model-unavailable`, `engine-unusable`) and not tracebacks; the model manifest is written
  atomically.
- **Platforms and documentation:** the Nix flake no longer lists `aarch64-darwin` (there is no NVIDIA GPU
  there); the README architecture picture of the upstream project is a Mermaid diagram; the setup skills, the
  connectivity guide, the configuration reference and the GPU guide describe what Axmem does now (the
  install route, local mode needing a GPU, the loopback and reverse-proxy warning, the real error messages).

- **Failures an operator can fix end in one line:** every command used to die in a traceback for a setting that does
  not validate (`LOG_LEVEL` was not validated at all; a port that is not a number; a `QDRANT_URL` without a host or with a
  port outside 1 to 65535), for a settings file that cannot be read or is not UTF-8, and for most start-up refusals. They
  now print `Error: <cause>` and exit with code 1, naming the setting and never repeating its value: `LOG_LEVEL` is
  matched without regard to case like `LOG_FORMAT`; `serve` builds the embeddings adapter of any provider (a remote
  provider without its key or URL, a package that is not installed, the reranker without its URL) and checks the scope
  ceiling, Qdrant (reachable, accepting the API key, version, collection layout) before it binds anything or starts the
  stdio server, instead of failing inside the web server's lifespan; an authentication provider that FastMCP's own
  constructor refuses (a JWKS URI together with a public key) is a refusal of `serve` only, like an incomplete mode.
  A wrong or missing Qdrant API key is named (`Qdrant refused the request (HTTP 401): check QDRANT_API_KEY`): Qdrant
  answers its version without a key, so the first real request found it, as a traceback, and the configuration guide
  said the version check would. `re-embed` and the operator commands (`backup`, `restore`, `export`, `import`,
  `qdrant-check`) report an unwritable `BACKUP_DIR`, a full disk, a failed HTTP call, a damaged JSON file, a model that
  cannot start (`import` on a host without a GPU printed a traceback) and an unavailable embeddings provider the same
  way; anything that is not such a cause is a bug and keeps its traceback. `axmem auth login` reports a settings file
  it cannot write. A failed `backup` no longer leaves an empty directory when Qdrant refuses the first question.
- **Bounds on requests:** a search `query` and its `query_context` (`MEMORY_QUERY_MAX_LENGTH`,
  `MEMORY_QUERY_CONTEXT_MAX_LENGTH`, 2000 characters each; one request could hold the only embedder worker and the
  reranker for every tenant), the number of `source_files` of a memory (`MEMORY_SOURCE_FILES_MAX_COUNT`, 50), the
  number of memories one link or unlink call names (`MEMORY_LINKS_MAX_PER_REQUEST`, 50) and the length of a tool or
  category name of the meta-tools (100) are bounded, and the body of an HTTP request is limited to
  `REQUEST_BODY_MAX_BYTES` (4 MiB; `413`, by `Content-Length` or counted while a chunked body is read). The reason of
  an obsolete mark must be a text that is not blank and at most `MEMORY_OBSOLETE_REASON_MAX_LENGTH` (500) characters:
  a number or an object was stored as it was and then failed every read of the memory with a `500`, which also stopped
  `axmem export` and `axmem re-embed` for every tenant, and a multi-megabyte reason was returned with every read. A
  reason a client stored before this change is read back as text, so such a memory is exported and re-embedded again.
- **Logs:** a title, a search query and the tags of a filter are no longer written to the INFO log (the length of the
  query is), and the filter that masks secrets now covers the `extra` fields that the JSON format writes and shortens
  a text above 32768 characters to its head and tail. Its pattern for credentials in a URL was quadratic, so one
  tool name of 1 MB made of `://a:` kept the server busy for about a minute; it is bounded, and the legacy-settings
  warning no longer loses a word of its advice to the pattern for `token:` and `password:`. The console format
  writes colour codes only to a terminal and not when `NO_COLOR` is set.
- **Health:** `/health` answers `503` with the reason once a local TensorRT engine met a CUDA error it cannot recover
  from (every later embedding or rerank call fails until the process restarts, and the route kept answering `200`),
  and a failed call now records such an error at the call, where the check that followed a successful call was never
  reached. With `CORS_ENABLED=true` the server starts uvicorn with the same options as without (it lacked the
  graceful-shutdown limit, so a stream held open could keep `dispose_runtime` from running).
- **Memory create:** a failure of the automatic linking after the memory was stored (a Qdrant timeout, a read-back that
  missed) no longer fails the call, which made the client retry and store the memory twice: the call succeeds without
  links and logs a warning that names the memory id (`rebuild_embeddings` for that id links it).
- **Backup, export and import:** `restore` reads the manifest as untrusted input and, before it uploads anything,
  refuses one that is not valid, that names a file other than a `node<N>-memories|links.snapshot` of the set, or a
  collection that is not this deployment's (`<prefix>_links`, `<prefix>_memories_<generation>`), or that does not hold
  exactly one memories and one links snapshot for each configured node (see the review of the storage layer below);
  snapshots are checksummed in chunks. `export` writes its `.partial` file
  through a descriptor it created itself (a stale file or a link at that name is removed, not followed), so a link planted in a
  shared directory no longer redirects the export onto another file. `import` skips and counts a line that is not
  UTF-8, is nested too deeply, holds a number of thousands of digits, has an id or a version of the wrong type, no
  text to embed or text that cannot be encoded, instead of stopping at the first of them.
- **Review of the robustness fixes:** a `DELETE /api/v1/memories/{id}` whose chunked body passed `REQUEST_BODY_MAX_BYTES`
  was answered `413` and still marked the memory obsolete, because the route took the cut-off body for an empty one; it
  answers `400` and changes nothing. A setting that is not a choice (`LOG_LEVEL`, `LOG_FORMAT`, `EMBEDDING_PROVIDER`,
  `RERANKING_PROVIDER`, `AUTH_MODE`) printed its value back in the `Error:` line (`Unsupported LOG_LEVEL setting:
  '<value>'`), so a key pasted into the wrong setting reached stderr; the line names the setting and the choices only.
  `restore` of a manifest nested deeper than the JSON parser goes ended in a traceback, and is one `Error:` line now. The
  bounded pattern for credentials in a URL masked less than the first one: a password with `/`, `?` or `#` was left
  in the log. It is masked again, up to 4096 characters; one with a space, or longer, is still not masked. A `GATEWAY_URL`
  with an empty `?` or `#` (which the check for a query or a fragment let through, and the path was appended after the
  marker) is refused at start-up.

- **Review of the storage layer:**
  - **`restore` on a cluster:** it uploaded only the first node's snapshot, so with a replication factor below the number
    of nodes (where a node stores only some of the shards) it brought back part of the data, silently. It now checks
    that the manifest holds one memories and one links snapshot for each node of `QDRANT_URL` and no other node (the
    manifest records each file's node URL; the file name must be `node<N>-<role>.snapshot` for its own node and the
    memories snapshots must be of one generation), verifies every checksum, uploads each file to the node it came from
    under a one hour timeout, waits after each file until every replica of that collection is `Active` again (up to 10 minutes;
    uploading the next node's file while replicas recover left every replica of a shard `Dead` in two of four repeated
    restores at replication factor 2; a replica still not `Active` after 90 seconds, which Qdrant sometimes leaves in
    `ManualRecovery` after a restore into an emptied cluster, gets its file uploaded once more, which activates it), and
    makes the generation live after the last file. A backup of other nodes, or of the same
    addresses in another order, is refused before anything is uploaded. The cluster tests restore for replication factors 3, 2 and 1, each over
    collections that exist and over an emptied Qdrant.
    `backup` deletes the snapshot from its node whatever happens to the download (a failed download left a snapshot as
    large as the node's shards behind each time; one that cannot be deleted is named in the log), and takes the snapshot
    under the same long timeout instead of the ordinary request timeout, which gave up on a large collection while the
    node went on and kept the snapshot unnamed (a snapshot that takes longer than that one hour is still not waited for; the
    cluster-scale entry below says how it is found and deleted).
  - **`re-embed` and the layout settings:** a changed `QDRANT_SHARD_NUMBER`, `QDRANT_REPLICATION_FACTOR` or
    `QDRANT_WRITE_CONSISTENCY_FACTOR` stopped the server with a message that told the operator to "create a new
    collection", which `re-embed` could not do (it refused to open the old collection with the same message, and left the
    links collection in the old layout). `re-embed` now opens the old collections whatever their layout, prints the layout
    the new one gets, fills the new generation in it, and rebuilds the links collection in that layout through a staging
    copy that is removed at the end; nothing is removed before the copy holds every link, and a run that stopped half
    way is finished by running it again. `ensure(check_width=False)` became `ensure(replacing=True)`, which tolerates the
    layout, the width and the embedder that are about to be replaced and nothing else. The start-up message names
    `axmem re-embed`.
  - **Another embedder of the same width:** a provider, model, precision (`EMBEDDING_TF32`) or gateway backend that
    produced vectors of the same width passed the start-up check, and queries were then embedded in another vector
    space than the stored vectors. Every new memory generation records the embedder that fills it (collection metadata
    `axmem_embedder`; a test now checks that it comes back with a snapshot restored into an emptied Qdrant, on one node and in
    the cluster), and the server refuses to start on a mismatch with a message that names both
    and `axmem re-embed`. A collection that recorded nothing is taken to be the configured embedder's and records it,
    with a warning.
  - **Edits that were overtaken:** a write that landed and was then overtaken by two more writes before the writer read it
    back was reported as lost and replayed over the newer state (the point kept only the latest claim token). Each point
    now keeps the tokens of its 16 earlier versions (`op_history`, a payload field written from now on; points without it
    work), the read-back finds its token by version, and a write that more than 16 writes overtook is answered with
    `OutcomeUnknownError` (409, "the outcome cannot be told ... read it again and repeat the change if it is missing")
    instead of being replayed or reported as lost.
  - **An edit that straddled a re-embedding switch:** `update_memory` read the memory from the live alias and wrote
    through the alias again, so a switch of the alias between the two wrote a vector of the old model into the new
    generation. An attempt now works on one generation from its read to its read-back and, when the alias moved
    meanwhile, is made again on the live one.
  - **Reads that decide a write or copy data away** ask a majority of the replicas: the memory an edit or an obsolete
    mark works on, the superseding memory, the existence checks of unlink, `get_memory_by_id`, `export`, `import`, and the
    scans and checks of the re-embedding copy (a stale replica could report a memory missing, apply an edit to an old
    copy, or leave a newer memory out of an export or of the new generation). Qdrant returns a point from such a read
    only while the replicas it asks hold the same version of it, so a memory that other writers are rewriting at that
    moment can be missing from the answer: the reads that decide a write or answer for one memory (the memory an edit
    or an obsolete mark works on, the superseding memory, the targets of a link, `get_memory_by_id`) repeat it a few
    times and, when the replicas never agree, fail with `ConflictError` instead of reporting the memory missing or
    skipping a link. The cluster test of overlapping edits found it.
  - **A conflict is a 409 on every REST route:** `GET /api/v1/memories/{id}`, `POST` and `GET .../links` and
    `DELETE .../links/{target}` caught only `NotFoundError`, so a memory the replicas kept disagreeing about
    (`ConflictError`, which `get_memory_by_id` and the targets of a link now raise instead of reporting the memory missing)
    ended as a 500, and `GET .../links` failed as a whole for one such linked memory where it used to skip a missing one.
    `handle_request_errors`, which wraps every memory route, now answers any `ConflictError` as `409` with the message, so
    no route can leave one unanswered; the explicit handlers of `PUT` and `DELETE` are gone for it. A list of links with
    a linked memory the replicas disagree about is a 409, never a shorter list.
  - **The read-back of a write waits for the replicas as well:** `QdrantStore.landed` made one majority read and, when it
    returned nothing, took the write for lost. A majority read returns nothing while the replicas hold different versions
    of the point, which is how a point looks that another writer has just overtaken, so an edit that had landed was
    replayed over the newer one (and an obsolete mark that had landed was refused as "already obsolete" or retried). It
    now reads like every read that answers for one point (`get_agreed`): it waits for the replicas to agree, and when they
    never do it raises `OutcomeUnknownError` (409; the write may have been applied: read the memory and repeat the change
    if it is missing) instead of guessing. A point that no read finds is still a write that did not apply.
  - **A `re-embed` that stopped while it moved the links left a server that started without them:** the links collection
    is removed and made again in the new layout before the staged links are copied back, and a run that stopped in between
    left a collection that was missing or empty and passed every start-up check, so `axmem serve` started with no links
    and no warning (and a backup or an export would have left them out). `serve` and `import` (the schema check), `backup`
    and `export` now refuse, naming `<prefix>_links_relayout` and `axmem re-embed`, while that staging collection exists,
    and never create an empty links collection in its place. The next `re-embed` finishes the move first, before it embeds
    anything (`rebuild_links(unfinished_only=True)`), so the links are back within moments whatever becomes of the rest of
    that run; the report says the links were rebuilt. `restore` removes the staging collection, since the snapshot is the
    source of truth for the links. [docs/embedding_migration.md](docs/embedding_migration.md) said that a half-finished run
    left nothing to restore and now describes this state and the cost of the run that ends it.
  - **Statements that said more than the code or the tests:** a snapshot "never stays on its node" (one that cannot be
    deleted is only named in the log, and one that the node finishes after the 3600 s and the look for it stays on the node), the cluster restore tests
    covering replication factor 3 over an emptied Qdrant (that layout is now tested) and the embedder record "kept in
    snapshots" (no test restored one; the single-node end-to-end test and every cluster restore layout now read it back
    before `ensure()` could record it again).
  - **Smaller:** moving the alias and creating it are repeated after a lost answer without failing on the alias that
    had moved (one request, checked first); a generation that cannot be discarded is retried, and a failed discard no
    longer replaces the error that ended the run (or the validation report). `qdrant-check` says when nodes could not be
    read (`N of M nodes could not be read, so the nodes were not compared in full`) and no longer words that as a
    disagreement of the points it could not compare. `get_linked_memories` returned the most important of only the first
    1000 neighbours of a memory; it now ranks every neighbour (their importance is read in chunks of 1000) before it takes
    the most important `max_links`. The checksum of a snapshot was already computed in chunks at this revision, and a test
    now pins it.

- **Review of the engine and provider errors:**
  - **A `tokenizer.json` that cannot be parsed** raised a bare `Exception` from the `tokenizers` library, which no start-up
    handler knows, so `serve` and `re-embed` ended in a traceback. It is a `model-unavailable` start-up refusal that names the
    file and the model directory to delete, like the other model-file errors.
  - **A cached engine that TensorRT refuses is no longer deleted on the spot:** every deserialize failure of a plan whose
    fingerprint matched removed the cached directory, which a GPU too full to hold the engine (or a driver that does not
    suit it) caused as well as a damaged file. An entry that lost its plan or its fingerprint is still removed and
    rebuilt; for a plan that TensorRT refuses the start builds a plan from the same model first and replaces the entry
    only when that one loads, and when TensorRT refuses the new plan too it keeps the cached entry, stores nothing and
    refuses the start with `engine-unusable` and TensorRT's message. A plan is cached only after this process has loaded
    it, a plan or a fingerprint file that cannot be read is no proof of damage (the cache is the cause of the refusal,
    nothing is removed; the fingerprint of another, older entry that cannot be read only leaves the reason of the
    rebuild without its comparison), and an engine loaded before the cache failed to store it or to prune its siblings
    is released.
  - **A closed engine, or one that met a CUDA error it cannot recover from, raises `TensorRTUnavailableError`**, a
    `TensorRTError` that is also a `ModelUnavailableError` (not an `EmbeddingUnavailableError`, see Operator errors
    below; so does the check that finds the error after a call that returned, and
    so does the call that first meets the error: it used to raise the plain `TensorRTError` of its own failure, and only
    the calls after it were refused, so the batch that met the error was still retried memory by memory; the failure of
    that call stays in the exception chain), so a targeted rebuild records the batch as failed at once instead of
    retrying every memory against an engine that refuses them all. The messages of the later calls are unchanged.
  - **A failed start releases the reranker's engine:** only the embedder's warm-up was guarded, so a reranker whose
    warm-up failed kept its engine and device buffers while anything held the exception. Both adapters now release the
    engine when anything after the engine was loaded fails, including the start-up report (CUDA context and GPU memory).
  - **`probe_gpu` fails with `TensorRTStartupError` at every step:** a CUDA error after the device count (properties,
    compute capability, memory) was a plain `TensorRTError`, which the documented causes and the preflight of the test
    suites do not expect. It is `no-gpu`, `driver` or `gpu-error` by the same table as the device count.
  - **The Azure, Google, OpenAI and Ollama adapters check every reply:** a vector of another width than
    `EMBEDDING_DIMENSIONS` was returned as it came (only the TensorRT and gateway providers checked), to be refused by
    Qdrant on a write or searched against a collection of another width. It now raises `EmbeddingUnavailableError`
    (`<provider> returned N-dimensional embeddings but EMBEDDING_DIMENSIONS is M. Set EMBEDDING_DIMENSIONS=N, or choose a
    model that produces M ...`) and a number that is NaN or infinite is refused. The adapters stay as they were otherwise;
    none of them is asked at start-up.
  - **`BACKUP_DIR` is where `axmem backup` writes, not `axmem export`:** the code was right and the documentation
    (the setting comment, `.env.example`, the configuration reference) was not. `export` takes the file to write on its
    command line, owner-only like a backup, and never touches `BACKUP_DIR`; a test pins it.

- **Tests, CI and hooks:** pylint no longer checks the members of the native modules `tensorrt` and `cuda.*`, which it
  could not see and reported as missing whenever they were installed, so a commit that touched `tensorrt_runtime.py`
  with few other files failed the pre-commit score on errors that were never real. The Qdrant preflight of the suites pulls `qdrant/qdrant:v1.19.2` when the image is not local (a
  clean or pruned runner, or a bumped version) and still ends the run with exit code 2 and the cause when Docker or the
  pull fails. The mutation sandbox holds `.githooks/` and
  `.github/`, which the hook and workflow tests read, and the protocol-coverage test ignores the names mutmut adds to a
  mutated class, so mutmut's baseline run passes (checked with the gate on `app.utils.url_helpers*`). `denylist-check` fails closed
  (exit 2, naming the line numbers of the list that are not valid expressions, never the terms) when grep cannot use the
  list, no longer drops added lines that begin with `++`, matches against the line without the diff column (so `^term`
  works) and accepts a term that begins with a dash. It checks each line of the list on its own and passes grep the
  lines as separate patterns, instead of joining them with `|` (two invalid lines, such as a term that ends in a
  backslash followed by another term, could join into a valid expression and silently stop enforcing the neighbour; a
  back-reference now keeps its own groups); a path that grep cannot read (a directory, a dangling symlink, a file
  without read permission) also exits 2 instead of passing; and `git`, `grep` and `awk` calls are captured one by one, so
  a failing or missing `awk`, a failing `git` or an unknown revision stops the check with exit 2 where the pipeline status
  used to hide it. The settings-defaults snapshot masks only credentials, so the
  defaults of `MEMORY_TOKEN_BUDGET`, `MEMORY_KEYWORDS_MAX_COUNT`, `MEMORY_KEYWORD_MAX_LENGTH` and `TOKEN_CACHE_*` are
  pinned; a schema test no longer leaves a collection behind; the tenant id derivation is pinned by tests; the trivy
  image is pinned (`aquasec/trivy:0.75.0`); the wheel smoke test installs the dependencies from `uv.lock` (NVIDIA's
  index for TensorRT, without `tensorrt-cu13-libs`) and the wheel on top, instead of resolving the two PyPI placeholders of
  the TensorRT packages (`tensorrt-cu13`, `tensorrt-cu13-libs`). `pytest`, `pytest-asyncio` and `requests` (test-only) moved from the runtime dependencies to the
  `dev` group and the unused `fastapi` pin was removed, so an installed Axmem no longer pins them (`requests` still arrives through the SDKs that need it).
- **CI runs only what needs no GPU and no Docker; the pre-push hook is the GPU gate:** the earlier CI moved the test,
  snapshot, coverage, memory and mutation jobs to a self-hosted GPU runner behind a guard against pull requests from
  forks. There is no such runner and hosted runners have no GPU, so those jobs (`ci.yml` `test`, the whole of `e2e.yml`,
  and the `snapshots`, `coverage`, `memory`, `qodana` and weekly `mutation` jobs of `quality.yml`) are gone, with the fork
  guard that protected them and the schedule. GitHub Actions now runs, on hosted `ubuntu-latest` runners only, ruff, the
  architecture tests (which include the rules of the workflow files), the static gates of `quality.yml` (ruff, import
  boundaries, bandit, semgrep with the registry packs, vulture, radon, mypy, pylint), `trivy` (the pinned release binary,
  checked against its SHA-256, instead of a Docker image) and the wheel smoke test, all installed with `--locked` and
  without `tensorrt-cu13-libs`; the steps were run in a clean container with no GPU and no Docker. `tests/architecture/test_workflows.py` now fails
  on a self-hosted or non-Ubuntu runner, a container or service, a schedule, `pull_request_target`, a secret, a token that
  is not read-only, a checkout that keeps its credentials, a gate that needs the GPU or Docker, an install that fetches the
  TensorRT libraries, a missing concurrency group, and a trivy version that differs from the gate's; `test_mutation_sandbox.py`
  checks that mutation is a gate of the pre-push hook and of no workflow. Both concurrency groups are now
  `workflow-ref` (the event no longer matters without a schedule). The `pre-push` hook is the one place that runs the
  suites: tests, the Qdrant cluster suite, 100 % coverage, memory budgets, Qodana and mutation. A gate whose
  tool or daemon is missing (the cluster suite without Docker, trivy with neither a binary nor Docker, Qodana without
  its CLI, semgrep with nothing to run it) now fails with the cause instead of reporting `SKIP`, and `trivy` runs with
  the static gates, before the long suites. The hook refuses the push of a commit that is not the checked-out `HEAD`
  (it used to skip the pipeline for such a push and let it through unchecked), and still lets a deletion and a tag on
  `HEAD` through its checks. Tests run real `git push` commands against a bare repository for the hook, each gate of the
  pipeline failing, the order of the gates, and the real suites ending their run without a GPU and without Docker.
  `stage ci` is the set of hosted gates. Two findings of the first clean run were fixed: semgrep's registry packs
  flagged the two log lines that name a rejected token's client id (inline `nosemgrep` with the reason, in
  `app/middleware/token_verifiers.py`), and `trivy` reported `certifi` 2026.1.4 (GHSA-6q48-6c7w-9gw2, critical, fixed in
  2026.10.19), so `uv.lock` now locks `certifi` 2026.10.19 (nothing else moved).
- **Documentation review:** statements that the code contradicted were corrected. `docs/tool_reference.md` gives the
  real return values (`mark_memory_obsolete` and `unobsolete_memory` return `{"success": true}`, `query_memory` the
  whole result object), lists every parameter of `get_recent_memories` and the provenance fields `agent_id`,
  `agent_version` and `agent_model`, and documents the tenth tool, `rebuild_embeddings`; `docs/api_reference.md` shows
  the real search response (`primary_memories`, `linked_memories`, `scores`, `total_count`, `token_count`,
  `truncated`) and `obsolete_matches` in the create response; `docs/prompts/custom_agent_integration.md` says ten tools
  and names `linked_memory_ids` instead of a field that never existed. The Copilot CLI, OpenCode and Gemini CLI guides
  and the two `axmem-setup` commands start `axmem` from a checkout with `AUTH_MODE=none` (or a bearer token for a
  server on a network) instead of a `uvx axmem` that has no package to resolve and a server that exits without an
  auth mode, drop the OAuth sign-in, and no longer point at a log file that nothing writes (the log goes to standard
  error); the Axmem CLI section of the connectivity guide installs with `uv tool install .`. `docs/search.md` lists
  all six embedding providers and the search settings in place of an empty section; the architecture overview names the
  tenant in the link id and anchors every line link at the right symbol; `docs/quality-gates.md` describes how
  the mutation gate is scoped instead of `SKIP`;
  `docs/dev/architecture-tests.md` names the `lint` job and seven rules; the test harness README counts its six
  skills. `.env.example` no longer pins `SERVICE_VERSION` (a copied file made `/` and `/health` report a stale
  version for every release) and lists the settings it left out; `docs/configuration.md` warns about the `["*"]`
  default of `CORS_ORIGINS`. `docs/gpu-models.md` explains that the tiktoken data is fetched on the first query unless
  `TIKTOKEN_CACHE_DIR` holds it, and that a host which drops packets makes that query wait for the connect timeout, with
  the pre-load command. Three of these corrections were themselves wrong and were fixed: `skipped_ids` of
  `rebuild_embeddings` holds only a memory that was edited, made obsolete or deleted while the rebuild ran (an id that
  does not exist, belongs to another tenant or is obsolete is a `failed` entry with `not_found_or_not_owned`, and an
  `auto_link_failed` entry also carries `phase`), in `docs/tool_reference.md`, `docs/embedding_migration.md` and the
  tool's own description (the discovered tool catalogue snapshot follows); `.env.example` puts the explanation of
  `FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY` (and of `GATEWAY_TENANT` and the provenance defaults, which had the same fault)
  on its own line, because a comment after an empty value is read as the value and a copied block made both a JWKS URI
  and a public key reach the verifier, which refuses to start; `docs/gpu-models.md` sets `TIKTOKEN_CACHE_DIR` in the
  process environment, since tiktoken reads only that and a line in a settings file never reaches it. The mutation
  sandbox copies `.env.example`, which `tests/integration/test_reference_docs.py` parses.
- **Start-up, tokenizer:** the tiktoken encoding is loaded before the server serves (`preload_token_counter`, in a
  daemon thread that `TIKTOKEN_LOAD_TIMEOUT_SECONDS` bounds, 30 seconds by default) instead of on the first query. A host
  with the data in `TIKTOKEN_CACHE_DIR` starts with no network; a host that has no cache and cannot download the data
  within the limit (it drops packets, or has no network) ends the start with one line, which names `TIKTOKEN_CACHE_DIR`
  and the command that pre-loads the data: `Error: the token counter cannot start: ...` for `serve`, and
  `Error: the tiktoken encoding of the token counter cannot be loaded (...)` for the commands that run the tools in
  process (`axmem memory`, `axmem call`, `axmem tools`), where the first query used to wait out the connect timeout and
  then fall back to an estimate. A host without a cache but with a working network downloads the data and starts.
- **Wheel install:** a wheel names the three pinned TensorRT packages without an index, because `[tool.uv.sources]` is not
  part of a built wheel, and on PyPI `tensorrt-cu13` and `tensorrt-cu13-libs` are placeholders that fetch the 3.8 GB of
  libraries from NVIDIA's index while they build (`tensorrt-cu13-bindings` is a real wheel there). The README (Installing
  the package) lists the commands that work for `uv tool install` and pip from a checkout, a Git URL and a built wheel
  (`--extra-index-url https://pypi.nvidia.com`); the smoke test of
  CI resolves the built wheel with that flag and checks that the packages come from NVIDIA's index; a test keeps the
  documented commands in step with `pyproject.toml`. The redundant `ollama` optional extra (the SDK is a dependency) is
  removed from the package and from `uv.lock`.
- **Old configuration directory:** the settings file of the directory the old name used (`~/.config/forgetful/.env` on
  Linux) and `docker/.env` are no longer read, and the start now says so when one exists, naming the file and the
  settings in it (never a value) and the files that are read now; the file is left as it was.
- **CORS:** `Access-Control-Allow-Credentials: true` was sent whatever `CORS_ORIGINS` said, and the default is `["*"]`,
  so any web page could make credentialed calls and read the answers. Credentials are now off, and
  `CORS_ALLOW_CREDENTIALS=true` (refused together with `*` in `CORS_ORIGINS`) sends them to the named origins only.
- **Tool catalogue:** `link_memories` returns `{"linked_memory_ids": [...]}` (the targets that were newly linked) and
  `mark_memory_obsolete` and `unobsolete_memory` return `{"success": true}` and raise on every failure; the catalogue
  said "List of memory IDs" and "Boolean", and `execute_axmem_tool` callers that trusted it looked for a value that was
  never there. A test runs the three tools through the registry and compares the answers with the catalogue text.
- **Operator errors:** an SDK or HTTP error of a service other than Qdrant was named for Qdrant, or for the embeddings
  provider whatever failed. It now says which service failed (the embeddings gateway, the rerank service, the token
  introspection endpoint, Ollama, Google, OpenAI or Azure OpenAI, by the settings and the request that failed) without a
  URL or a key, and a failure that the web server's lifespan meets (Qdrant or a provider failing between the start-up
  check and the lifespan) ends as `Error: <cause>` with exit code 1 instead of the traceback of the lifespan. A closed
  TensorRT reranker or embedder raises `TensorRTUnavailableError`, which is no longer an `EmbeddingUnavailableError`
  (both are a `ModelUnavailableError`), so the reranker is not reported as the embeddings provider; `rebuild_embeddings`
  still stops at either.
- **TensorRT teardown after a failure:**
  - **A release that fails no longer hides the error that led to it, and no longer leaves the rest allocated.** After a
    sticky CUDA error (an illegal address, a hardware fault) every CUDA call reports it, the calls that destroy a stream
    and free a buffer included. `_Slot.release` stopped at the first of them, so the buffers behind it were never freed,
    `TensorRTEngine.close` stopped at the first slot (the later slots, the engine and the runtime stayed, and a second
    `close` did nothing because the engine already counted as closed), and the error of the release replaced the error
    of the warm-up that the adapter had been cleaning up after (a plain `TensorRTError` instead of the
    `TensorRTUnavailableError` the warm-up met). Every step now runs once, in the order context, stream, buffers, then
    the engine and the runtime, whatever a step reports; the steps that failed are one `ERROR` log line (the steps and
    each distinct reason) and `close` does not raise, so the error that ended the start is the one that surfaces.
  - **An engine or a slot that cannot be made releases what it made.** `_Slot` made its stream and buffers one after the
    other and `TensorRTEngine.__init__` made its slots in a loop; an out-of-memory error at the second buffer or the
    third slot left the stream, the buffers and the slots already made allocated on the device, with nothing that could
    free them (the half-made object is never handed out). Both now release what they had made and raise the original
    error. The adapters make their worker pool before the engine (a count the pool refuses used to build the engine
    first) and log the start-up report inside the guard that closes the engine on a failure, and their `close` releases
    the engine even when the wait for the workers fails. `_release_tensorrt_adapters` (at exit) closes every shared
    adapter, the latest first, even when one of them raises.
  - **Tests** (`tests/integration/test_tensorrt_teardown.py`): the failures are injected at the CUDA wrapper calls
    (`cudaMalloc`, `cudaStreamCreate`, `cudaFree`, `cudaStreamDestroy`, `cudaStreamSynchronize`,
    `cudaPeekAtLastError`) of `tensorrt_runtime` on the real engines, never in a model. They assert that the original
    error surfaces, that every stream and buffer that was made was released exactly once, that the secondary failures
    are one log line, and that the GPU memory of the process (read through `gpu_memory()`) is back to where it was; a
    loop of five starts, failed warm-ups and closes of a real embedder and of a real reranker shows no growth.
- **Tests:** pytest's base temp directory is made at the start of the session (a session fixture of `tests/conftest.py`),
  so that no test that records or replaces `atexit.register` can meet pytest's own registration of the clean-up of that
  directory (it passes five arguments), and the recorder of `tests/integration/test_cli_operator_commands.py` accepts
  the arguments of any registration. This is hardening: that file already made the directory first in its own fixture.
- **Documentation, corrected after a review of the runtime commit:** a closed TensorRT engine is a `ModelUnavailableError`
  and no longer an `EmbeddingUnavailableError` in the earlier notes too; on PyPI only `tensorrt-cu13` and
  `tensorrt-cu13-libs` are placeholders (`tensorrt-cu13-bindings` is a real wheel); `axmem memory`, `axmem call` and
  `axmem tools` print the tokenizer refusal without the prefix that `serve` adds (a test runs the command). The tests of
  the GPU-gate paragraphs of the README and `docs/contributors.md` (paragraphs of their own) and of the Quality row of
  `docs/changes-from-forgetful.md` (it no longer says that hosted CI runs every check), which the runtime commit left
  uncommitted, are committed with this one.
- **Cluster scale** (found by running the scans, the snapshots and the restore against writers and against clusters of
  other sizes; the cluster tests are the proof):
  - **Scans are complete or they fail.** A quorum read that asks for the content of a page leaves out a point whose
    replicas are not all at the same version at that moment. Beside eight writers rewriting 600 memories on three
    nodes (3 shards, replication factor 3, write consistency factor 2) such a scan missed a memory in about one round
    in three (and with one shard it once lost a whole page of 100); a read of the ids alone never did. `export`,
    `import` and the copy of `re-embed` (`outdated_in`, `copy_into`) could therefore write a file or
    a generation without a memory that existed (`qdrant-check` was never affected: it reads each node on its own, not by
    quorum, and keeps doing so; the first wording of this entry listed it by mistake). Every page is now listed by id with a plain read (`QdrantStore.page`)
    and the listed memories are fetched with the repeating quorum read (`get_agreed`, which now asks again only for
    the ids still missing); a memory the replicas never agree about ends the scan with a `ConflictError` (one `Error:`
    line on the command line, 409 over REST) instead of being left out. `import` and the copy checked their
    "already there" reads with a plain quorum read and now use `get_agreed` too. `export` counts the memories before it
    starts and refuses to write a file with fewer than the collection held then (memories are never deleted, so it
    would have lost one), and prints a `Warning:` on stderr when the counts at its end differ from the file's (writers
    created memories meanwhile). With writers running a scan is still a moving picture, which
    [docs/configuration.md](docs/configuration.md) now says; an exact copy needs the writers stopped. The cluster tests
    (`tests/cluster/test_cluster_scans.py`) hold the omission (a detector shows the old scan loses memories), 25 rounds of a complete scan, an
    export and the copy of a `re-embed` beside writers, and, as a control, `qdrant-check`'s per-node counts beside writers; 17 of the 21 new
    single-node tests (`tests/integration/test_qdrant_complete_scans.py`, replicas that disagree injected between the
    pool and the client) fail without the change.
  - **A failed snapshot request deletes the snapshot it left behind.** A request that gave up after the 3600 seconds the client
    waits (or whose connection dropped, or whose answer carried no name) left the snapshot the node went on to make
    on the node, unnamed, as large as its shards. `NodePool.create_snapshot` lists the snapshots of the collection
    before the request, and after any failure deletes the ones that were not there; after a timeout or a dropped
    connection it looks for up to 5 minutes more, because the node goes on, and says in the log which snapshots existed
    before when none shows up in that time. The listing, the deletion and the wait never replace the error the backup
    ends with. Tests: `tests/integration/test_qdrant_snapshot_cleanup.py` against an in-process stand-in for the snapshot
    routes of a Qdrant node (`tests/qdrant_fake_node.py`).
  - **The number a re-embed reports is the number it wrote.** `_replace_older` counted the memories carrying its own claim
    token after the write, so a write that another writer overtook (the memory is then newer than the copy, which is
    fine) was not counted, and the report could name fewer memories than the copy had processed. `copy_into` now reads back
    what it wrote and counts a memory when the new generation holds it at the version written or a newer one; a write
    that did not take effect is not counted. The cluster test with writers asserts the total (60 of 60; it was 58 before).
  - **A server that keeps running cannot write vectors of the old embedder into the new generation.** `ensure` checks the
    embedder of the live generation when a process starts; a server left running while `axmem re-embed` moved the alias to a
    generation of another model went on writing old-model vectors into it. `QdrantSchema.writable_generation` now checks the
    record of the live generation the first time this process sees it, and create, edit, targeted rebuild and import
    (which write by that generation's name) raise `EmbedderChangedError` (a `ConflictError`: 409, and the text is shown over MCP)
    and write nothing when it is not this process's embedder; a create (and each batch of an import) that straddles a switch to a generation of the same embedder
    is written again into the new one. A create, an edit or an import that the switch overtakes (the refusal comes after the write) did
    write: the memory is in the previous generation, and the catch-up copy of `re-embed` may or may not carry it into the new one.
    Its refusal says so (see the corrections below) instead of "Nothing was written". Reads are not affected. The procedure stays: stop every server before `re-embed`
    ([docs/embedding_migration.md](docs/embedding_migration.md), which also records that a run with writers across the
    switch lost an acknowledged edit in the cluster test, because the catch-up copy goes by version number and the two generations can
    hold different histories of a memory). Tests: `tests/integration/test_qdrant_embedder_switch.py`.
  - **Corrections to the cluster-scale review** (found by a second review of the commit above):
    - `qdrant-check` was listed among the scans that are now complete or end in a `ConflictError`. It never used a quorum scan
      (it reads each node on its own, which is its purpose), so it did not change; the changelog, the changes table and
      [docs/configuration.md](docs/configuration.md) no longer say it did.
    - A create or an edit that landed in the previous generation just before the alias moved to another embedder's generation
      was refused with "Nothing was written", although the memory was stored there and the catch-up copy of `re-embed` may
      carry it over: a client that repeated the request could store it twice. `writable_generation(written=Written.MEMORY)`,
      asked after the write, words the refusal as an open outcome (look for the memory before repeating). A refusal before
      the write keeps saying that nothing was written. Tests: `tests/integration/test_qdrant_embedder_switch.py` (the old
      generation holds the memory, the new one nothing).
    - An `import` that the alias overtook was refused with "Nothing was written" as well, although its earlier batches
      were stored in the previous generation, and a switch that came while its last batch was written was not noticed at
      all (the command ended with a summary). `import_memories` now passes what it has stored to
      `writable_generation(written=Written.IMPORT)` and looks again after each batch, like a create: the refusal says
      that the import was written, wholly or in part, that `re-embed` may or may not have carried it over, and that the
      import is repeated after the restart, which leaves a memory that exists (same id) as it is and so stores nothing
      twice; a batch that straddles a switch to a generation of the same embedder is written into the new one too. A
      refusal before the first batch is written keeps saying that nothing was written; one after it says that the import was written. `axmem import` is a command, so the refusal ends
      it with `Error: <text>` and exit code 1, not with a 409. Tests: `tests/integration/test_qdrant_embedder_switch.py`.
    - `export` counts the collections at its end before the file is put in place, not after. A transient Qdrant error
      there used to end the command with an error although the file was in place; it now ends it like any other failure
      of the export, with no file. Test: `tests/integration/test_qdrant_complete_scans.py`.
    - The statements that a snapshot taking longer than 3600 s stays unnamed are corrected: the node's snapshot is looked
      for for up to 5 minutes more and deleted; only one finished after that stays on the node, and the log lists the
      snapshots that existed before.
    - The `ConflictError` handler of the REST routes no longer says that every conflict is to be repeated:
      `EmbedderChangedError` needs a restart of the server.
  - **Clusters of other sizes and a restore into another cluster are tested** (`tests/cluster/test_cluster_backup_restore_other_clusters.py`,
    `start_cluster(nodes=...)` in `tests/qdrant_support.py`): see the results in
    [docs/changes-from-forgetful.md](docs/changes-from-forgetful.md#facts-to-re-verify).
- **Embeddings:** the error log line of the Azure adapter for a refused request read "Error generatring embeddings"; it reads
  "Error generating embeddings" now, like the Google, OpenAI and Ollama adapters. A log filter or alert that matched the
  misspelt text must use the corrected one.
- **Quality gates:** the `mutation` gate no longer reads a failed or empty `mutmut results` as "nothing survived". It asks for
  every status (`mutmut results --all true`) and fails when that command exits with an error or lists no mutant at all; a
  targeted mutant that mutmut never tested ("not checked") fails whatever `MUTATION_MAX_SURVIVORS` says; every targeted status
  except "killed" is a survivor, as before, and mutants outside the targets are still ignored.

### Security

- `authlib` is locked at 1.9.1 (was 1.7.2, which the vulnerability database of the `trivy` gate lists as CVE-2026-96760,
  a signature verification bypass rated critical, fixed in 1.9.0, and CVE-2026-114615 and CVE-2026-114616, rated high, the
  second an authentication bypass through a zero-length HMAC key, fixed in 1.9.1); `dnspython` is locked at 2.9.0 (was
  2.8.0, CVE-2026-114605, a denial of service through excessive DNS compression pointer traversal, rated high). Both are
  transitive dependencies of `fastmcp` and the only entries of `uv.lock` that changed. The `trivy` job of `quality.yml`
  failed on them on every push of `main`, and the local `trivy` gate had not seen them because its vulnerability database
  was older (`0287e9a`).
- `filelock` is locked at 4.2.4 (was 3.21.1, which the vulnerability database of the `trivy` gate lists as CVE-2026-112940,
  an arbitrary file overwrite through the reopening of an insecure database file, fixed in 4.2.1); it is a transitive
  dependency of `huggingface-hub` and the only entry of `uv.lock` that changed (`70dde33`).
- `certifi` is locked at 2026.10.19 (was 2026.1.4, which bundles root CAs named in GHSA-6q48-6c7w-9gw2); it is a
  transitive dependency of the HTTP clients and the only entry of `uv.lock` that changed.
- Authentication fails closed (`3201a2c3`). A server with no `AUTH_MODE` refuses to start (exit code 1), `AUTH_MODE=none` is accepted only on stdio or on a loopback bind (`127.0.0.1`, `::1`; a hostname such as `localhost` does not count), and the credential modes are refused on stdio, which carries no bearer token. A request-level check refuses to serve when no provider is attached and the mode is not `none`. Fixes the finding that, without `FASTMCP_SERVER_AUTH`, every request was the same default user (`app/middleware/auth.py`), so any caller of a network-bound server read and changed every memory. Existing deployments that relied on that default must set `AUTH_MODE` (and bind to a loopback address if it is `none`) before the next start.
- Tenant isolation is tested on every access path: tenant A cannot read, update, delete, link, unlink, search or list tenant B's memories over the MCP tools, the REST routes and the CLI (local and remote), and another tenant's id answers exactly like an id that does not exist.
- Static tokens are stored as SHA-256 hashes only, compared in constant time, and never logged; the settings validator refuses a value that is not a 64-character hexadecimal digest without repeating it, so a token pasted by mistake is not echoed. A static token has no expiry: revoke it by removing its hash and restarting.
- PostgreSQL has no row-level security: there is no `CREATE POLICY` in `app/` or `alembic/`, isolation is a
  `user_id` predicate in each query, and a role that connects as the PostgreSQL bootstrap superuser (as the removed
  Docker deployment did) would bypass row-level security anyway. This goes away with the move to Qdrant, where isolation is a tenant
  filter applied in one place.
