"""Test-driven gates: fast tests, snapshots, coverage, Qdrant cluster e2e, memory, mutation."""

from __future__ import annotations

import os

from .core import (
    SUITES,
    Ctx,
    MissingToolError,
    Skip,
    check,
    docker_ready,
    git_lines,
    run,
    venv_tool,
)

CODE_PREFIXES = ("app/", "tests/", "main.py", "scripts/", "test_harness/")
PYTEST = ["-q", "-p", "no:cacheprovider"]
# The cluster suite starts real Qdrant nodes (three, four, and a second three) and waits for replicas to settle; it
# outgrew the half hour every other gate gets
E2E_QDRANT_TIMEOUT_SECONDS = 10800


def check_tests(argv: list[str], **kwargs) -> tuple[bool, str]:
    """Run a test command without the ``GIT_*`` variables a Git hook exports.

    Tests create temporary repositories; inheriting ``GIT_DIR``/``GIT_INDEX_FILE`` would make their
    git commands act on the repository being committed to.
    """
    return check(argv, scrub_git=True, **kwargs)


def gate_snapshots(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_files(CODE_PREFIXES):
        raise Skip("no code or test changes")
    return check_tests([*venv_tool("pytest"), "-m", "snapshot", *PYTEST])


def gate_tests_fast(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_files(CODE_PREFIXES):
        raise Skip("no code or test changes")
    return check_tests([*venv_tool("pytest"), "tests/architecture", "tests/integration", "-x", *PYTEST])


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
    else:  # pre-push / ci: whatever the branch changed relative to main
        diff = git_lines("diff", "--name-only", "--diff-filter=ACMR", "origin/main...HEAD")
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


def gate_mutation(ctx: Ctx) -> tuple[bool, str]:
    tolerated = int(os.environ.get("MUTATION_MAX_SURVIVORS", "0"))
    targets = mutation_targets(ctx)
    code, out = run([*venv_tool("mutmut"), "run", *targets], timeout=7200, scrub_git=True)
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
