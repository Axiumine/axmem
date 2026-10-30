# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Wheel gate: the built package is what a user gets when installing it.

``package`` reads what the wheel and the sdist hold. This gate uses them: ``twine check`` on the metadata,
``check-wheel-contents`` on the layout, an install on top of the locked dependencies in an environment of its own, the
console script and the import of the entry module from another directory, and the resolution of the wheel's own
metadata with the index that the README documents (the TensorRT packages have no index in the metadata of the wheel).

Nothing is written to the repository: the gate builds into a temporary directory (never ``dist/``, which the release
flow checks and signs) and the clean environment lives there too, never in ``.venv``. ``check_wheel`` also checks a
``dist/`` that was built elsewhere: ``scripts/check-package.sh`` runs it on the files it is about to release.
"""

from __future__ import annotations

import shutil
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import package
from .core import ROOT, Ctx, MissingToolError, Skip, run, venv_binary

# The staged files that can change how the wheel installs or what it resolves to.
WHEEL_INPUTS = (
    *package.PACKAGE_INPUTS, "uv.lock", ".python-version", "scripts/lib/project.sh", "scripts/check-package.sh",
    "scripts/quality/wheel.py",
)
# check-wheel-contents codes to ignore. W005: the top-level package is called `app`, which the tool lists among the
# common names a wheel should not claim. W009: the wheel holds two top-level entries, the package `app/` and the module
# `main.py` that the console script points at (`[tool.hatch.build.targets.wheel] packages`): the layout of the project.
# W002 (duplicate files) is not ignored: the tool cannot ignore a path, so every package marker (`__init__.py`) that holds
# the notice of the license (docs/quality-gates.md, "Licensing headers") also holds a docstring of its own.
WHEEL_IGNORE = "W005,W009"
# Modules the clean environment imports. `main` is the console script's own module, which imports the application with
# the runtime dependencies alone.
IMPORT_MODULES = ("main",)
# The 3.8 GB of TensorRT libraries: no check loads them, and PyPI holds a placeholder that fetches them.
LIBRARIES = "tensorrt-cu13-libs"
# The wheel names the TensorRT packages without an index: the dependencies come from uv.lock (NVIDIA's index for them),
# without the libraries, which `axmem --help` never loads. The wheel goes on top of them alone.
LOCKED_SYNC = ["uv", "sync", "--locked", "--no-dev", "--no-install-project", "--no-install-package", LIBRARIES]
# The copy that the gate builds has no git history to read the version from.
VERSION_ENV = {"SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0"}

Step = Callable[[], tuple[bool, str]]


def _pyproject(project: Path) -> dict[str, Any]:
    return tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))


def _nvidia_index(project: Path) -> tuple[str, list[str]]:
    """The URL of the explicit index, and the packages ``[tool.uv.sources]`` takes from it."""
    config = _pyproject(project)["tool"]["uv"]
    urls = {index["name"]: index["url"].rstrip("/") for index in config["index"]}
    sources = {name: urls[source["index"]] for name, source in config["sources"].items() if "index" in source}
    (url,) = set(sources.values())
    return url, sorted(sources)


def lock_is_current(project: Path) -> tuple[bool, str]:
    code, out = run(["uv", "lock", "--check"], env=VERSION_ENV, cwd=project, scrub_git=True)
    return code == 0, out


def twine_check(dist: Path) -> tuple[bool, str]:
    artefacts = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    code, out = run([venv_binary("twine"), "check", *map(str, artefacts)])
    return code == 0, out


def wheel_contents(wheel: Path) -> tuple[bool, str]:
    code, out = run([venv_binary("check-wheel-contents"), "--ignore", WHEEL_IGNORE, str(wheel)])
    return code == 0, out


def clean_install(wheel: Path, project: Path, work: Path) -> tuple[bool, str]:
    """The locked dependencies, then the wheel alone on top, in an environment that is not ``.venv``; then use it."""
    venv = work / "venv"
    if venv.resolve().is_relative_to(ROOT.resolve()):
        return False, f"refusing to build an environment inside the repository: {venv}"
    code, out = run(LOCKED_SYNC, env={**VERSION_ENV, "UV_PROJECT_ENVIRONMENT": str(venv)}, cwd=project, scrub_git=True)
    if code == 0:
        code, out = run(["uv", "pip", "install", "--python", str(venv / "bin" / "python"), "--no-deps", str(wheel)])
    console_script = next(iter(_pyproject(project)["project"]["scripts"]))
    # Away from the checkout and isolated, so that the installed wheel is what gets imported, not app/ and main.py.
    commands = [[str(venv / "bin" / console_script), "--help"]]
    commands += [[str(venv / "bin" / "python"), "-I", "-c", f"import {module}"] for module in IMPORT_MODULES]
    for command in commands if code == 0 else []:
        code, out = run(command, cwd=work)
        if code != 0:
            out = f"{' '.join(command)}\n{out}"
            break
    return code == 0, out


def documented_resolution(wheel: Path, project: Path, work: Path) -> tuple[bool, str]:
    """Resolve the wheel's metadata with the extra index of the README (nothing is downloaded) and read the result.

    The three TensorRT packages must come from NVIDIA's index, and the libraries as a wheel: PyPI holds a placeholder
    for them that fetches the real ones when installed.
    """
    url, names = _nvidia_index(project)
    requirement, resolution = work / "wheel-requirement.txt", work / "pylock.toml"
    requirement.write_text(f"{wheel}\n", encoding="utf-8")
    python = (project / ".python-version").read_text(encoding="utf-8").strip()
    code, out = run([
        "uv", "pip", "compile", "--python-version", python, "--format", "pylock.toml", "--extra-index-url", url,
        "--output-file", str(resolution), str(requirement),
    ])
    if code != 0:
        return False, out
    packages = {entry["name"]: entry for entry in tomllib.loads(resolution.read_text(encoding="utf-8"))["packages"]}
    problems = []
    for name in names:
        entry = packages.get(name, {})
        files = [file["url"] for file in entry.get("wheels", [])] + ([entry["sdist"]["url"]] if "sdist" in entry else [])
        if not files or not all(file.startswith(f"{url}/{name}/") for file in files):
            problems.append(f"{name} does not resolve to {url}: {files}")
    if not packages.get(LIBRARIES, {}).get("wheels"):
        problems.append(f"{LIBRARIES} must resolve to a wheel, not to the placeholder")
    return not problems, "\n".join(problems)


def check_wheel(dist: Path, project: Path, work: Path) -> tuple[bool, str]:
    """Run the checks on the wheel in ``dist`` until one fails; ``project`` holds the pyproject and lock to install by."""
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        return False, f"{dist}: expected one wheel, found {len(wheels)}"
    (wheel,) = wheels
    steps: list[tuple[str, Step]] = [
        ("uv lock --check", lambda: lock_is_current(project)),
        ("twine check", lambda: twine_check(dist)),
        ("check-wheel-contents", lambda: wheel_contents(wheel)),
        ("clean environment: install and use the wheel", lambda: clean_install(wheel, project, work)),
        ("resolve the wheel with the documented index", lambda: documented_resolution(wheel, project, work)),
    ]
    for title, step in steps:
        ok, out = step()
        if not ok:
            return False, f"{title}:\n{out}"
    return True, ""


def check_built_dist(dist: Path) -> tuple[bool, str]:
    """The checks on a ``dist/`` built elsewhere, installed by the lock of this checkout."""
    with tempfile.TemporaryDirectory(prefix="axmem-wheel-") as work:
        return check_wheel(dist, ROOT, Path(work))


def gate_wheel(ctx: Ctx) -> tuple[bool, str]:
    if ctx.scoped and not ctx.touches(*WHEEL_INPUTS):
        raise Skip("no staged file that goes into the wheel or decides how it installs")
    if shutil.which("uv") is None:
        raise MissingToolError("uv is not installed, and the gate builds and installs the wheel with it")
    with tempfile.TemporaryDirectory(prefix="axmem-wheel-") as tmp:
        work = Path(tmp)
        built, log = package.build(ROOT, work)
        return check_wheel(work / "dist", work / "source", work) if built else (False, log)
