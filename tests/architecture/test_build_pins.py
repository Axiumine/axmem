"""The build backend is pinned to exact versions, because the build on the maintainer's machine and the rebuild of ``release.yml`` must be the same bytes.

A pin that is loosened (``>=``, ``~=``, a bare name) is found by ``release.yml`` only after the tag of the release is
pushed, and the version is lost for it. The dependencies of the two packages are not pinned (docs/release.md says so).
"""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
EXACT = re.compile(r"[A-Za-z][A-Za-z0-9._-]*==\d+(\.\d+)*")


def _requires():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["build-system"]["requires"]


def test_every_requirement_of_the_build_system_is_an_exact_pin():
    requires = _requires()

    assert requires
    assert all(EXACT.fullmatch(entry) for entry in requires), requires


def test_the_backend_and_the_version_plugin_are_among_the_pins():
    names = {entry.partition("==")[0] for entry in _requires()}

    assert {"hatchling", "hatch-vcs"} <= names
