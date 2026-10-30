# Fuzzing

The harnesses in [fuzz/](../fuzz/) feed generated input to the functions of Axmem that read data a client or an
identity provider controls. They use [Atheris](https://github.com/google/atheris), the coverage-guided fuzzer for
Python (libFuzzer underneath). A harness fails when the code raises something its contract does not name, or when a
property the code promises does not hold.

Fuzzing here is a tool for the maintainer and for contributors. No service runs it continuously: the commit and push
hooks run each harness for a few hundred inputs so that it cannot rot, and a longer run is a command you start yourself.

## The harnesses

| Harness | Code under test | What must hold |
|---|---|---|
| [fuzz/fuzz_token_claims.py](../fuzz/fuzz_token_claims.py) | `token_scopes_from_claims` ([scope_resolver.py](../app/routes/mcp/scope_resolver.py)), `tenant_from_claims` ([auth.py](../app/middleware/auth.py)) | The scopes never raise, are `None` or a set of scopes of the grammar, and are all spelled in the claims (a claim never grants what it does not name). The tenant is a non-blank name without margins and not longer than `TENANT_MAX_LENGTH`, or a `ValueError`. |
| [fuzz/fuzz_request_arguments.py](../fuzz/fuzz_request_arguments.py) | The coercions of tool arguments (`coerce_int`, `clamp_int`, `coerce_bool`, `coerce_memory_id`, `coerce_memory_ids`, `coerce_str_list` in [arguments.py](../app/routes/mcp/adapters/arguments.py)), `parse_created_since` ([memory_arguments.py](../app/routes/mcp/adapters/memory_arguments.py)) and `parse_int_param` ([query_params.py](../app/routes/api/query_params.py)) | A value of the promised type inside the promised bounds, or a `ValueError` (a `ToolError` for `parse_created_since`, whose result is a time in UTC that reads back as itself); never another exception, because the route answers a `ValueError` with a 400 and anything else with a server error. |
| [fuzz/fuzz_terminal_text.py](../fuzz/fuzz_terminal_text.py) | `sanitize_line`, `sanitize_text`, `redact_urls_in_text` ([render.py](../app/routes/cli/render.py)), `redact_url` ([url_helpers.py](../app/utils/url_helpers.py)) | No control character, escape introducer or line separator survives in printed text; a password in the userinfo of a URL never survives redaction; none of them raises. |

Every target is a pure function of its input. A harness needs no GPU, no Qdrant, no network and no setting of the
machine: it imports `app` under `isolated_settings` ([fuzz/shared.py](../fuzz/shared.py)), which hides the environment,
the dotenv file of the working directory and the settings file of the user for the import.

## Running a harness

Atheris is in the `dev` group and `uv sync` installs it on Linux x86_64, the only platform for which Atheris publishes
a build for CPython 3.14 (it has no source distribution). Elsewhere the dependency is skipped and the harnesses cannot run.

```bash
uv run python fuzz/fuzz_token_claims.py -max_total_time=60
```

The options after the file name are those of libFuzzer. The useful ones:

| Option | Effect |
|---|---|
| `-max_total_time=N` | stop after N seconds |
| `-runs=N` | stop after N inputs |
| `-seed=N` | the same sequence of inputs on every run |
| `-max_len=N -len_control=0` | inputs of up to N bytes from the first run (by default libFuzzer starts at 4 bytes and grows) |
| a directory | the corpus: libFuzzer reads the inputs in it and adds the ones that reach new code |

A corpus makes a second run start where the first one ended. Keep it out of the repository:

```bash
mkdir -p fuzz/corpus/token_claims
uv run python fuzz/fuzz_token_claims.py fuzz/corpus/token_claims -max_total_time=600
```

`fuzz/corpus/` and `fuzz/artifacts/` are ignored by Git.

## When a harness fails

libFuzzer prints the exception, saves the input that caused it as `fuzz/artifacts/crash-<hash>` and exits with a
non-zero code. Run the harness with that file as its only argument to repeat the failure:

```bash
uv run python fuzz/fuzz_token_claims.py fuzz/artifacts/crash-<hash>
```

Then decide which side is wrong. If the code breaks its contract, fix it and add a test with the input to
`tests/integration/`; the harness keeps guarding the property. If the contract is wider than the harness says (the
function may raise another documented exception), widen the `except` of the harness to that exception and no further.

## Keeping the harnesses honest

- A harness catches only the exceptions that the code under test documents (`ValueError`, `ToolError`). It never
  catches `Exception`: that would hide the findings.
- A property is checked with `invariant` ([fuzz/shared.py](../fuzz/shared.py)), not with `assert`, which `python -O`
  removes and which the security linters flag.
- A harness imports the code under test inside `with isolated_settings(), atheris.instrument_imports(include=["app"])`,
  so that Atheris sees the branches of `app` and not those of FastMCP or Pydantic.
- A new harness is `fuzz/fuzz_<target>.py`, imports `atheris`, ends in `run(test_one_input)` and gets a row in the
  table above. [tests/integration/test_fuzz_targets.py](../tests/integration/test_fuzz_targets.py) fails when the table
  and the directory differ, and runs every harness for 300 inputs with a fixed seed, so a renamed function or a
  property that stopped holding fails the `tests-fast` and `tests` gates and not only the next long run.

## What the quality gates do with `fuzz/`

| Gate | `fuzz/` |
|---|---|
| ruff | checked with the rest of the repository |
| mypy, pylint, bandit, semgrep, vulture, radon | checked (the path lists in `scripts/quality/core.py` and `[tool.mypy]`, `[tool.vulture]` in `pyproject.toml` name it); Atheris ships no type stubs, so `[tool.mypy]` ignores its missing imports for that module only |
| coverage | not measured: the report covers `app/` and `main.py`, and a harness is exercised by the 300-input run, not by branch |
| package, wheel | not shipped: the wheel lists `app` and `main.py`, the sdist an explicit list, and `fuzz/` is in neither |
| mutation | `tests/integration/test_fuzz_targets.py` is left out of the mutmut run: it starts child processes outside the sandbox |
