# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Test-driven gates: fast tests, snapshots, coverage, Qdrant cluster e2e, memory, mutation."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .core import (
    SUITES,
    Ctx,
    MissingToolError,
    Skip,
    base_ref,
    check,
    docker_ready,
    git_lines,
    run,
    venv_tool,
)

CODE_PREFIXES = ("app/", "tests/", "main.py", "scripts/", "test_harness/", "fuzz/")
PYTEST = ["-q", "-p", "no:cacheprovider"]
# What the tests of tests/architecture read: a commit that stages none of it cannot change their result.
ARCHITECTURE_INPUTS = (
    "app/", "scripts/", "tests/architecture/", ".github/", ".githooks/", "test_harness/docker/",
    "main.py", "pyproject.toml", "uv.lock", "architecture.md", ".python-version", "flake.nix", "tests/integration/",
    # test_licensing_headers.py reads the notice of every source file and the license texts. A staged test of tests/e2e or
    # tests/cluster is not listed, and the reuse gate (always the whole project) sees its missing notice; the form of the
    # notice is checked in the next push.
    "fuzz/", "test_harness/", "REUSE.toml", "LICENSE", "LICENSES/",
)
# What the integration tests read besides code: the documents, the settings example and the release inputs. A docs-only
# commit that breaks one of them would otherwise pass pre-commit and fail first at pre-push.
TEST_INPUTS = ("docs/", "README.md", "CHANGELOG.md", ".env.example", "release-superseded.json", ".github/")
# The cluster suite starts real Qdrant nodes (three, four, and a second three) and waits for replicas to settle; it
# outgrew the half hour every other gate gets
E2E_QDRANT_TIMEOUT_SECONDS = 10800
# The integration and end-to-end suites run the real models on a real Qdrant node: 22 to 29 minutes on the maintainer's machine,
# which left the half hour every other gate gets no margin as soon as another job shared the machine
TESTS_TIMEOUT_SECONDS = 10800
# One `mutmut run`, over the app/ modules the push changes: 24 h unless MUTATION_TIMEOUT_S names another cap, which
# subprocess can wait at most MUTATION_TIMEOUT_S_MAX for (poll() takes milliseconds in a C int)
MUTATION_TIMEOUT_S_DEFAULT = 86400
MUTATION_TIMEOUT_S_MAX = 2147483
# Every mutmut worker is a pytest process that holds the TensorRT embedder and reranker on the GPU (about 4.6 GiB, see
# tests/gpu_preflight.py); mutmut starts one per CPU unless told otherwise, and a card holds a handful of them
MUTATION_WORKERS = 3
# The exit code that run() reports for a command it stopped at the timeout (the one timeout(1) uses)
TIMED_OUT_EXIT_CODE = 124
# The tests load the models and the TensorRT engines from the data directory of the user (~/.local/share/axmem/models), and a
# mutant that changes a path constant builds another path there: a directory of another name for a model (with the 128 MB
# download and the engine built from it), another fingerprint directory for an engine. The engine cache also prunes every
# fingerprint but its own, so a mutant that changes the fingerprint removes the engine that the next run loads. A mutation
# run therefore gets a data directory of its own (platformdirs reads XDG_DATA_HOME) that holds a mirror of the cache: real
# directories, the files up to SMALL_FILE_BYTES copied (manifests, fingerprints, locks) and the large ones (model.onnx,
# engine.plan) as links, so that a mutant writes and removes in the mirror and the cache of the user stays as it was.
MODELS_DIR = "models"
SMALL_FILE_BYTES = 1 << 20


def check_tests(argv: list[str], **kwargs: Any) -> tuple[bool, str]:
    """Run a test command without the ``GIT_*`` variables a Git hook exports.

    Tests create temporary repositories; inheriting ``GIT_DIR``/``GIT_INDEX_FILE`` would make their
    git commands act on the repository being committed to.
    """
    return check(argv, scrub_git=True, **kwargs)


def gate_architecture(ctx: Ctx) -> tuple[bool, str]:
    """Import contracts, their rule proofs and the rules of the workflows: no GPU, no Docker, so hosted CI runs it too."""
    if ctx.scoped and not ctx.touches(*ARCHITECTURE_INPUTS):
        raise Skip("no staged file that an architecture rule reads")
    return check_tests([*venv_tool("pytest"), "tests/architecture", *PYTEST])


def gate_snapshots(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not (ctx.py_changed(CODE_PREFIXES) or ctx.touches("tests/")):  # a snapshot file is no .py
        raise Skip("no code or test changes")
    return check_tests([*venv_tool("pytest"), "-m", "snapshot", *PYTEST])


def gate_tests_fast(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not (ctx.py_changed(CODE_PREFIXES) or ctx.touches("tests/", *TEST_INPUTS)):
        raise Skip("no code or test changes")
    return check_tests([*venv_tool("pytest"), "tests/integration", "-x", *PYTEST], timeout=TESTS_TIMEOUT_SECONDS)


def gate_tests(_ctx: Ctx) -> tuple[bool, str]:
    """Architecture + integration + end-to-end on a single Qdrant node, collecting branch coverage."""
    run([*venv_tool("coverage"), "erase"])
    return check(
        [*venv_tool("pytest"), *SUITES, *PYTEST, "--cov", "--cov-branch", "--cov-report=", "--cov-fail-under=0"],
        timeout=TESTS_TIMEOUT_SECONDS,
    )


def gate_e2e_qdrant(_ctx: Ctx) -> tuple[bool, str]:
    """The three-node Qdrant cluster tests (docker): replication, failover, race and fault detectors."""
    if not docker_ready():
        raise MissingToolError(
            "no Docker daemon answers: the cluster e2e starts three Qdrant nodes and is never skipped "
            "(start Docker, or leave the gate out on purpose with SKIP=e2e-qdrant)",
        )
    return check_tests(
        [*venv_tool("pytest"), "tests/cluster", "-m", "cluster", *PYTEST,
         "--cov", "--cov-branch", "--cov-append", "--cov-report=", "--cov-fail-under=0"],
        timeout=E2E_QDRANT_TIMEOUT_SECONDS,
    )


def gate_coverage(_ctx: Ctx) -> tuple[bool, str]:
    return check([*venv_tool("coverage"), "report", "--fail-under=100"])


def gate_memray(_ctx: Ctx) -> tuple[bool, str]:
    if os.name == "nt":
        raise Skip("memray is not available on Windows")
    return check_tests([*venv_tool("pytest"), "tests/e2e", "-m", "memory", "--memray", *PYTEST])


def mutation_targets(ctx: Ctx) -> list[str]:
    """mutmut selects mutants by dotted name pattern; whole-app runs (~33k mutants) are impractical."""
    explicit = os.environ.get("MUTATION_TARGETS", "").split()
    if explicit:
        return explicit
    if ctx.scoped:
        changed = ctx.py_files(("app/",))
    else:  # pre-push: whatever the push adds to the upstream of the branch
        base = base_ref()
        diff = git_lines("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD", strict=True)
        changed = [f for f in diff if f.startswith("app/") and f.endswith(".py")]
    if not changed:
        raise Skip("no changed app/ modules; or set MUTATION_TARGETS='app.utils.*'")
    return [f.removesuffix(".py").replace("/", ".") + ".*" for f in changed]


def listed_mutants(results: str) -> dict[str, str]:
    """Every ``mutmut results`` line ('<dotted name>: <status>') by mutant name."""
    found: dict[str, str] = {}
    for line in results.splitlines():
        name, sep, status = line.strip().partition(": ")
        if sep:
            found[name] = status.strip()
    return found


def mutant_statuses(results: str, targets: list[str]) -> dict[str, str]:
    """`mutmut results` lines ('<dotted name>: <status>') belonging to the targeted modules."""
    prefixes = tuple(t.removesuffix("*") for t in targets)
    return {name: status for name, status in listed_mutants(results).items() if name.startswith(prefixes)}


def models_dir(data_home: Path | None = None) -> Path:
    """Where Axmem keeps its models and engines: in the data directory of the user, or under ``data_home`` as XDG_DATA_HOME."""
    # Imported here: the trivy job of quality.yml runs the gates with the standard library alone.
    from platformdirs import user_data_dir

    own = Path(user_data_dir("axmem", ensure_exists=False))
    return (own if data_home is None else data_home / own.name) / MODELS_DIR


def mirror_cache(source: Path, target: Path) -> None:
    """Make ``target`` a tree of real directories like ``source``: files up to ``SMALL_FILE_BYTES`` copied, larger ones linked."""
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        mirrored = target / entry.name
        if entry.is_dir():
            mirror_cache(entry, mirrored)
        elif not entry.is_file():
            continue  # a link to nothing
        elif entry.stat().st_size <= SMALL_FILE_BYTES:
            shutil.copy2(entry, mirrored)
        else:
            mirrored.symlink_to(entry)


@contextmanager
def private_data_home() -> Iterator[dict[str, str]]:
    """The environment of a mutation run: a data directory of its own with a mirror of the model cache, removed at the end."""
    cache = models_dir()
    with tempfile.TemporaryDirectory(prefix="axmem-mutation-data-", ignore_cleanup_errors=True) as home:
        if cache.is_dir():
            mirror_cache(cache, models_dir(Path(home)))
        yield {"XDG_DATA_HOME": home}


def mutation_timeout() -> int:
    """Seconds one ``mutmut run`` may take: ``MUTATION_TIMEOUT_S``, else the default; any other value is an error."""
    raw = os.environ.get("MUTATION_TIMEOUT_S")
    if raw is None:
        return MUTATION_TIMEOUT_S_DEFAULT
    digits = raw.lstrip("0")  # int() refuses a string of thousands of digits; one this long is out of range anyway
    if raw.isascii() and raw.isdigit() and 0 < len(digits) <= len(str(MUTATION_TIMEOUT_S_MAX)):
        seconds = int(digits)
        if seconds <= MUTATION_TIMEOUT_S_MAX:
            return seconds
    raise ValueError(
        f"MUTATION_TIMEOUT_S={raw!r} is not a positive whole number of seconds up to {MUTATION_TIMEOUT_S_MAX} "
        f"(unset it for the default of {MUTATION_TIMEOUT_S_DEFAULT} s)",
    )


def output_tail(out: str) -> str:
    """The last 40 lines of what a command printed (a progress bar rewrites its line with a carriage return)."""
    return "\n".join(out.replace("\r", "\n").splitlines()[-40:])


def gate_mutation(ctx: Ctx) -> tuple[bool, str]:
    tolerated = int(os.environ.get("MUTATION_MAX_SURVIVORS", "0"))
    try:
        timeout = mutation_timeout()
    except ValueError as exc:
        return False, str(exc)
    targets = mutation_targets(ctx)
    command = [*venv_tool("mutmut"), "run", "--max-children", str(MUTATION_WORKERS), *targets]
    with private_data_home() as sandbox:
        code, out = run(command, timeout=timeout, env=sandbox, scrub_git=True, own_session=True)
    if code == TIMED_OUT_EXIT_CODE:
        return False, (
            f"mutation exceeded {timeout} s over {len(targets)} module patterns and was stopped, "
            "with the test workers it started. Raise the cap (MUTATION_TIMEOUT_S, in seconds), name fewer modules "
            "(MUTATION_TARGETS='app.utils.*') or leave the gate out of this push (SKIP=mutation) and say so in the "
            "pull request. mutants/ keeps the mutants already tested, and a rerun continues from them while the code "
            "and the files the tests read stay as they are."
        )
    tail = output_tail(out)
    if code != 0 or "failed to collect stats" in out:
        return False, f"mutmut could not run the test suite (exit {code}):\n{tail}"
    return mutation_verdict(targets, tolerated, tail)


def unusable_listing(code: int, results: str) -> str:
    """Why ``mutmut results`` says nothing about the run, or an empty string when its listing can be trusted.

    An error exit, or a listing without a single mutant (mutmut names one '<function>__mutmut_<n>'), is no answer: it must
    not read as "nothing survived".
    """
    shown = output_tail(results) or "(no output)"
    if code != 0:
        return f"mutmut could not list its results (exit {code}):\n{shown}"
    if not any("__mutmut_" in name for name in listed_mutants(results)):
        return f"mutmut listed no mutant at all, so nothing says what the run tested:\n{shown}"
    return ""


def mutation_verdict(targets: list[str], tolerated: int, tail: str) -> tuple[bool, str]:
    """What the mutants of the targeted modules say after a run; ``tail`` is the end of the output of that run.

    ``mutmut results`` is asked for every status (``--all true``), so that what the gate drops is the killed mutants
    and nothing else: a mutant that was never tested ("not checked") fails whatever ``tolerated`` says, and every other
    status that is not "killed" (survived, timeout, no tests, suspicious, skipped, segfault, caught by type check,
    interrupted) is a survivor.
    """
    code, results = run([*venv_tool("mutmut"), "results", "--all", "true"])
    if problem := unusable_listing(code, results):
        return False, problem
    statuses = mutant_statuses(results, targets)
    untested = [name for name, status in statuses.items() if status == "not checked"]
    if untested:
        return False, f"{len(untested)} targeted mutants were never tested (first: {untested[0]}):\n{tail}"
    survivors = [f"{name}: {status}" for name, status in statuses.items() if status != "killed"]
    if len(survivors) > tolerated:
        header = f"{len(survivors)} mutants not killed (tolerated {tolerated}):\n"
        return False, header + "\n".join(survivors[:200])
    return True, ""
