# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The real ``uv``, to hold the fake ``uv`` of the release tests to it.

The release tests replace ``uv`` with a script. Where the fake assumes something about the tool it stands for, a test runs
the real ``uv`` instead: ``build_dist`` builds a scratch project offline, with a build backend of a few lines of standard
library, so the entries a real build leaves in ``dist/`` can be compared with the fake's. Nothing leaves the machine, no
credential of the user is read, and ``uv`` is only ever asked to build into a scratch directory. The release publishes
nothing from this machine, so there is no ``uv publish`` to hold anything to.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: the real ``uv`` on ``PATH`` (None when absent: the test that needs it fails and says so)
UV = shutil.which("uv")

PYPROJECT = """\
[build-system]
requires = []
build-backend = "backend"
backend-path = ["."]

[project]
name = "scratchpkg"
version = "1.0.0"
"""
BACKEND = '''\
"""A PEP 517 backend in the standard library: enough for ``uv build``, nothing to download."""
import io
import tarfile
import zipfile
from pathlib import Path

NAME, VERSION = "scratchpkg", "1.0.0"
METADATA = f"Metadata-Version: 2.1\\nName: {NAME}\\nVersion: {VERSION}\\n"


def build_sdist(sdist_directory, config_settings=None):
    name = f"{NAME}-{VERSION}.tar.gz"
    with tarfile.open(Path(sdist_directory) / name, "w:gz") as tar:
        for file in ("pyproject.toml", "backend.py"):
            tar.add(file, arcname=f"{NAME}-{VERSION}/{file}")
        info = tarfile.TarInfo(f"{NAME}-{VERSION}/PKG-INFO")
        info.size = len(METADATA)
        tar.addfile(info, io.BytesIO(METADATA.encode()))
    return name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = f"{NAME}-{VERSION}-py3-none-any.whl"
    info = f"{NAME}-{VERSION}.dist-info"
    with zipfile.ZipFile(Path(wheel_directory) / name, "w") as wheel:
        wheel.writestr(f"{info}/METADATA", METADATA)
        wheel.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
        wheel.writestr(f"{info}/RECORD", "")
    return name
'''


def clean_env(base: Path) -> dict[str, str]:
    """Only what uv needs: no ambient ``UV_*`` or ``VIRTUAL_ENV``, a cache of its own, no download of a Python."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(base),
        "UV_CACHE_DIR": str(base / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never",
    }


def run_uv(args: list[str], base: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the real ``uv`` with ``args`` in ``cwd`` and ``clean_env``; the caller reads the result."""
    assert UV is not None
    return subprocess.run(  # noqa: S603 - a program of the test setup, run without a shell
        [UV, *args],
        cwd=cwd,
        env=clean_env(base),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def build_dist(base: Path) -> Path:
    """The output directory of a real ``uv build`` (offline, no config) of a scratch project."""
    project = base / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (project / "backend.py").write_text(BACKEND, encoding="utf-8")
    out = base / "dist"
    argv = ["build", "--offline", "--no-config", "--python", sys.executable, "--out-dir", str(out)]
    done = run_uv(argv, base, project)
    assert done.returncode == 0, done.stderr
    return out
