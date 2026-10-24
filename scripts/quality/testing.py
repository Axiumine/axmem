"""Test-driven gates: fast tests, snapshots, coverage, Qdrant cluster e2e, memory, mutation."""

from __future__ import annotations

import os

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

CODE_PREFIXES = ("app/", "tests/", "main.py", "scripts/", "test_harness/")
PYTEST = ["-q", "-p", "no:cacheprovider"]
# What the tests of tests/architecture read: a commit that stages none of it cannot change their result.
ARCHITECTURE_INPUTS = (
    "app/", "scripts/", "tests/architecture/", ".github/", ".githooks/", "test_harness/docker/",
    "main.py", "pyproject.toml", "uv.lock", "architecture.md", ".python-version", "flake.nix", "tests/integration/",
)
# What the integration tests read besides code: the documents, the settings example and the release inputs. A docs-only
# commit that breaks one of them would otherwise pass pre-commit and fail first at pre-push.
TEST_INPUTS = ("docs/", "README.md", "CHANGELOG.md", ".env.example", "release-superseded.json", ".github/")
# The cluster suite starts real Qdrant nodes (three, four, and a second three) and waits for replicas to settle; it
# outgrew the half hour every other gate gets
E2E_QDRANT_TIMEOUT_SECONDS = 10800
# One `mutmut run`, over the app/ modules the push changes: 24 h unless MUTATION_TIMEOUT_S names another cap, which
# subprocess can wait at most MUTATION_TIMEOUT_S_MAX for (poll() takes milliseconds in a C int)
MUTATION_TIMEOUT_S_DEFAULT = 86400
MUTATION_TIMEOUT_S_MAX = 2147483
# Every mutmut worker is a pytest process that holds the TensorRT embedder and reranker on the GPU (about 4.6 GiB, see
# tests/gpu_preflight.py); mutmut starts one per CPU unless told otherwise, and a card holds a handful of them
MUTATION_WORKERS = 3


def check_tests(argv: list[str], **kwargs) -> tuple[bool, str]:
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
    return check_tests([*venv_tool("pytest"), "tests/integration", "-x", *PYTEST])


def gate_tests(_ctx: Ctx) -> tuple[bool, str]:
    """Architecture + integration + end-to-end on a single Qdrant node, collecting branch coverage."""
    run([*venv_tool("coverage"), "erase"])
    return check(
        [*venv_tool("pytest"), *SUITES, *PYTEST, "--cov", "--cov-branch", "--cov-report=", "--cov-fail-under=0"],
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


def mutant_statuses(results: str, targets: list[str]) -> dict[str, str]:
    """`mutmut results` lines ('<dotted name>: <status>') belonging to the targeted modules."""
    prefixes = tuple(t.removesuffix("*") for t in targets)
    found: dict[str, str] = {}
    for line in results.splitlines():
        name, sep, status = line.strip().partition(": ")
        if sep and name.startswith(prefixes):
            found[name] = status.strip()
    return found


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


def gate_mutation(ctx: Ctx) -> tuple[bool, str]:
    tolerated = int(os.environ.get("MUTATION_MAX_SURVIVORS", "0"))
    try:
        timeout = mutation_timeout()
    except ValueError as exc:
        return False, str(exc)
    targets = mutation_targets(ctx)
    command = [*venv_tool("mutmut"), "run", "--max-children", str(MUTATION_WORKERS), *targets]
    code, out = run(command, timeout=timeout, scrub_git=True, own_session=True)
    if code == 124:
        return False, (
            f"mutation exceeded {timeout} s over {len(targets)} module patterns and was stopped, "
            "with the test workers it started. Raise the cap (MUTATION_TIMEOUT_S, in seconds), name fewer modules "
            "(MUTATION_TARGETS='app.utils.*') or leave the gate out of this push (SKIP=mutation) and say so in the "
            "pull request. mutants/ keeps the mutants already tested, and a rerun continues from them while the code "
            "and the files the tests read stay as they are."
        )
    tail = "\n".join(out.replace("\r", "\n").splitlines()[-40:])
    if code != 0 or "failed to collect stats" in out:
        return False, f"mutmut could not run the test suite (exit {code}):\n{tail}"
    _, results = run([*venv_tool("mutmut"), "results"])
    statuses = mutant_statuses(results, targets)
    untested = [name for name, status in statuses.items() if status == "not checked"]
    if untested:
        return False, f"{len(untested)} targeted mutants were never tested (first: {untested[0]}):\n{tail}"
    survivors = [f"{name}: {status}" for name, status in statuses.items()]
    if len(survivors) > tolerated:
        header = f"{len(survivors)} mutants not killed (tolerated {tolerated}):\n"
        return False, header + "\n".join(survivors[:200])
    return True, ""
