"""Static-analysis gates: lint, boundaries, security lint, dead code, complexity, types."""

from __future__ import annotations

import json
import os
import shutil

from .core import (
    PYLINT_PATHS,
    RADON_PATHS,
    ROOT,
    SOURCE_PATHS,
    TYPED_PATHS,
    Ctx,
    MissingToolError,
    Skip,
    check,
    docker_ready,
    run,
    scoped_paths,
    venv_tool,
)

SEMGREP_VERSION = "1.181.0"
RADON_BAD_CC = "CDEF"


def gate_ruff(ctx: Ctx) -> tuple[bool, str]:
    return check([*venv_tool("ruff"), "check", *scoped_paths(ctx, ["."], ("",))])


def gate_imports(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_files(("app/",)):
        raise Skip("no app/ changes")
    return check([*venv_tool("lint-imports"), "--no-cache"])


def gate_mypy(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_files(tuple(TYPED_PATHS)):
        raise Skip("no typed-path changes")
    # Whole project: a changed signature breaks unchanged callers.
    return check([*venv_tool("mypy")])


def gate_pylint(ctx: Ctx) -> tuple[bool, str]:
    paths = scoped_paths(ctx, PYLINT_PATHS, tuple(PYLINT_PATHS))
    argv = [*venv_tool("pylint"), "--fail-under=8.0"]
    if ctx.scoped:
        # A score over a handful of changed files is meaningless for convention/refactor
        # messages; those count in the project-wide score checked on push and in hosted CI.
        argv.append("--disable=C,R")
    return check([*argv, *paths])


BANDIT_BASE = [
    "-c", "pyproject.toml", "-q", "-f", "custom",
    "--msg-template", "{relpath}:{line}: {test_id} [{severity}/{confidence}] {msg}",
]


def gate_bandit(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped:
        paths = [f for f in ctx.py_files() if not f.startswith("tests/")]
        if not paths:
            raise Skip("no non-test Python files")
        return check([*venv_tool("bandit"), *BANDIT_BASE, *paths])
    return check([*venv_tool("bandit"), *BANDIT_BASE, "-r", *SOURCE_PATHS])


def gate_vulture(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_files():
        raise Skip("no Python changes")
    # Dead-code detection is meaningful only across the whole code base.
    return check([*venv_tool("vulture")])


def gate_radon_cc(ctx: Ctx) -> tuple[bool, str]:
    paths = scoped_paths(ctx, RADON_PATHS, ("",))
    code, out = run([*venv_tool("radon"), "cc", "-j", *paths])
    if code != 0:
        return False, out
    offenders = [
        f"{path}:{block['lineno']} {block['name']} "
        f"rank {block['rank']} (complexity {block['complexity']})"
        for path, blocks in json.loads(out).items()
        for block in blocks
        if block.get("type") != "class" and block["rank"] in RADON_BAD_CC
    ]
    if offenders:
        header = "functions rated C or worse (minimum allowed rating is B):\n"
        return False, header + "\n".join(offenders)
    return True, ""


def gate_radon_mi(ctx: Ctx) -> tuple[bool, str]:
    paths = scoped_paths(ctx, RADON_PATHS, ("",))
    code, out = run([*venv_tool("radon"), "mi", "-j", "-s", *paths])
    if code != 0:
        return False, out
    offenders = [
        f"{path}: radon could not analyse it ({info['error']})"
        if "error" in info
        else f"{path} rank {info['rank']} (MI {info['mi']:.2f})"
        for path, info in json.loads(out).items()
        if "error" in info or info["rank"] != "A"
    ]
    if offenders:
        header = "files below maintainability grade A (score must be above 19):\n"
        return False, header + "\n".join(offenders)
    return True, ""


def semgrep_cmd() -> list[str]:
    if shutil.which("semgrep"):
        return ["semgrep"]
    if shutil.which("uvx"):
        return ["uvx", "--from", f"semgrep=={SEMGREP_VERSION}", "semgrep"]
    if docker_ready():
        return [
            "docker", "run", "--rm", "-v", f"{ROOT}:/src", "-w", "/src",
            f"semgrep/semgrep:{SEMGREP_VERSION}", "semgrep",
        ]
    raise MissingToolError("semgrep is not installed and neither uvx nor a Docker daemon can run it")


def gate_semgrep(ctx: Ctx) -> tuple[bool, str]:
    argv = [
        *semgrep_cmd(), "scan", "--config", ".semgrep", "--metrics=off", "--error",
        "--quiet", "--disable-version-check",
    ]
    for pack in os.environ.get("SEMGREP_PACKS", "").split():
        argv += ["--config", pack]
    if ctx.scoped:
        files = [f for f in ctx.py_files() if not f.startswith("tests/")]
        if not files:
            raise Skip("no non-test Python files")
        argv += files
    else:
        argv += [p for p in SOURCE_PATHS if (ROOT / p).exists()]
    return check(argv)
