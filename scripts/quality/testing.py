"""Test-driven gates: fast tests, snapshots, coverage, Postgres e2e, py3.14, memory, mutation."""

from __future__ import annotations

import os
import shutil

from .core import SUITES_SQLITE, Ctx, Skip, check, docker_ready, run, venv_tool

COMPAT_PYTHON = "3.14"
CODE_PREFIXES = ("app/", "tests/", "main.py", "alembic/")
PYTEST = ["-q", "-p", "no:cacheprovider"]


def gate_snapshots(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_files(CODE_PREFIXES):
        raise Skip("no code or test changes")
    return check([*venv_tool("pytest"), "-m", "snapshot", *PYTEST])


def gate_tests_fast(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_files(CODE_PREFIXES):
        raise Skip("no code or test changes")
    return check([*venv_tool("pytest"), "tests/architecture", "tests/integration", "-x", *PYTEST])


def gate_tests(_ctx: Ctx) -> tuple[bool, str]:
    """Architecture + integration + SQLite end-to-end, collecting branch coverage."""
    run([*venv_tool("coverage"), "erase"])
    return check(
        [*venv_tool("pytest"), *SUITES_SQLITE, *PYTEST, "--cov", "--cov-branch", "--cov-report="],
    )


def gate_e2e_postgres(_ctx: Ctx) -> tuple[bool, str]:
    if not docker_ready():
        raise Skip("docker daemon unavailable (PostgreSQL e2e needs it)")
    return check(
        [*venv_tool("pytest"), "tests/e2e", "-m", "e2e", *PYTEST,
         "--cov", "--cov-branch", "--cov-append", "--cov-report="],
    )


def gate_coverage(_ctx: Ctx) -> tuple[bool, str]:
    return check([*venv_tool("coverage"), "report", "--fail-under=100"])


def gate_compat(_ctx: Ctx) -> tuple[bool, str]:
    if not shutil.which("uv"):
        raise Skip("uv unavailable")
    return check(
        ["uv", "run", "--frozen", "--python", COMPAT_PYTHON, "--all-extras",
         "pytest", *SUITES_SQLITE, *PYTEST],
        env={"UV_PROJECT_ENVIRONMENT": ".venv314"},
    )


def gate_memray(_ctx: Ctx) -> tuple[bool, str]:
    if os.name == "nt":
        raise Skip("memray is not available on Windows")
    return check([*venv_tool("pytest"), "tests/e2e_sqlite", "-m", "memory", "--memray", *PYTEST])


def gate_mutation(ctx: Ctx) -> tuple[bool, str]:
    tolerated = int(os.environ.get("MUTATION_MAX_SURVIVORS", "0"))
    targets: list[str] = []
    if ctx.scoped:
        targets = ctx.py_files(("app/",))
        if not targets:
            raise Skip("no app/ changes to mutate")
    code, out = run([*venv_tool("mutmut"), "run", *targets], timeout=7200)
    if code not in (0, 1):
        return False, out
    _, results = run([*venv_tool("mutmut"), "results"])
    survivors = [line for line in results.splitlines() if "survived" in line]
    if len(survivors) > tolerated:
        header = f"{len(survivors)} surviving mutants (tolerated {tolerated}):\n"
        return False, header + "\n".join(survivors[:200])
    return True, ""
