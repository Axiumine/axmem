"""Static-analysis gates: lint, boundaries, security lint, dead code, complexity, types."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

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
    repo_files,
    run,
    scoped_paths,
    venv_binary,
    venv_tool,
)

SEMGREP_VERSION = "1.181.0"
RADON_BAD_CC = "CDEF"
# The registry packs of the semgrep gate: hosted CI and pre-push run them (they need the network); a pre-commit run on
# staged files keeps to the local rules. SEMGREP_PACKS in the environment replaces them, and empty leaves them out.
SEMGREP_PACKS = "p/python p/security-audit"
SHELL_INTERPRETERS = frozenset({"sh", "bash", "dash", "ksh"})
SHELLCHECK_CONFIG = ".shellcheckrc"
# The two tools read these from the environment and from a user-level rc file; the gate must not differ by machine
NO_AMBIENT_SHELLCHECK = {"SHELLCHECK_OPTS": ""}
WORKFLOWS_DIR = ".github/workflows/"
ACTIONLINT_CONFIG = (".github/actionlint.yaml", ".github/actionlint.yml")


def gate_ruff(ctx: Ctx) -> tuple[bool, str]:
    return check([*venv_tool("ruff"), "check", *scoped_paths(ctx, ["."], ("",))])


def gate_imports(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_changed(("app/",)):
        raise Skip("no app/ changes")
    return check([*venv_tool("lint-imports"), "--no-cache"])


def gate_mypy(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.py_changed(tuple(TYPED_PATHS)):
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
    if ctx.scoped and not ctx.py_changed():
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


def semgrep_packs(ctx: Ctx) -> list[str]:
    if "SEMGREP_PACKS" in os.environ:
        return os.environ["SEMGREP_PACKS"].split()
    return [] if ctx.scoped else SEMGREP_PACKS.split()


def gate_semgrep(ctx: Ctx) -> tuple[bool, str]:
    argv = [
        *semgrep_cmd(), "scan", "--config", ".semgrep", "--metrics=off", "--error",
        "--quiet", "--disable-version-check",
    ]
    for pack in semgrep_packs(ctx):
        argv += ["--config", pack]
    if ctx.scoped:
        files = [f for f in ctx.py_files() if not f.startswith("tests/")]
        if not files:
            raise Skip("no non-test Python files")
        argv += files
    else:
        argv += [p for p in SOURCE_PATHS if (ROOT / p).exists()]
    return check(argv)


def interpreter_of(first_line: str) -> str:
    """The program a ``#!`` line runs (``env``'s argument when it goes through env), or an empty string."""
    words = first_line.removeprefix("#!").split() if first_line.startswith("#!") else []
    if words and Path(words[0]).name == "env":
        words = [word for word in words[1:] if not word.startswith("-")]
    return Path(words[0]).name if words else ""


def is_shell_script(name: str) -> bool:
    """A ``.sh`` file, or a file whose first line names a POSIX shell (the hooks and fake tools have no suffix)."""
    if name.endswith(".sh"):
        return True
    try:
        with (ROOT / name).open("rb") as handle:
            first_line = handle.readline(256).decode("utf-8", "replace")
    except OSError:
        return False
    return interpreter_of(first_line.strip()) in SHELL_INTERPRETERS


def shell_script_changed(ctx: Ctx) -> bool:
    """A staged shell script or its configuration, or a removed ``.sh`` file (a script that sourced it now breaks)."""
    staged = any(is_shell_script(name) for name in ctx.files or []) or any(name.endswith(".sh") for name in ctx.removed)
    return staged or ctx.touches(SHELLCHECK_CONFIG)


def gate_shellcheck(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not shell_script_changed(ctx):
        raise Skip("no staged shell script")
    scripts = [name for name in repo_files() if is_shell_script(name)]
    if not scripts:
        raise Skip("no shell script in the project")
    # -x follows `source` into the sourced files, which are linted as part of the same run
    return check([venv_binary("shellcheck"), "-x", *scripts], env=NO_AMBIENT_SHELLCHECK)


def is_workflow(name: str) -> bool:
    return name.startswith(WORKFLOWS_DIR) and name.endswith((".yml", ".yaml"))


def gate_actionlint(ctx: Ctx) -> tuple[bool, str]:
    changed = [*(ctx.files or []), *ctx.removed]
    if ctx.scoped and not (ctx.touches(SHELLCHECK_CONFIG, *ACTIONLINT_CONFIG) or any(map(is_workflow, changed))):
        raise Skip("no staged workflow")
    workflows = [name for name in repo_files() if is_workflow(name)]
    if not workflows:
        raise Skip("no workflow in the project")
    shellcheck = venv_binary("shellcheck")
    # actionlint lints the `run:` blocks with the shellcheck it finds, so the pinned one goes first on the PATH
    path = os.pathsep.join([str(Path(shellcheck).parent), os.environ.get("PATH", "")])
    return check([venv_binary("actionlint"), "-shellcheck", shellcheck, *workflows], env={**NO_AMBIENT_SHELLCHECK, "PATH": path})
