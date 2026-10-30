# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The release flow holds no credential: it reads no environment file and puts no ambient secret on a command line.

PyPI is given the files by ``publish.yml`` with an OIDC identity (trusted publishing); this machine only reads PyPI's public
JSON and files, GitHub through ``gh`` and git. So ``scripts/release-pypi.sh`` has no use for an environment file, a token or
a password, and these cases run the real script in the scratch repository of ``tests/release_support/world.py`` to prove it:

* a world with no environment file releases, and so does one with no argument (the read-only report of the last version);
* a world whose environment file is a directory, which fails to open for every user (root included), and whose caller
  exports ``UV_PUBLISH_TOKEN`` and ``TWINE_PASSWORD``, releases just the same: nothing opened the file, and no command that
  the script ran (``git``, ``curl``, ``gh``, ``uv``, ``python3``, ``timeout``, recorded with their arguments by shims first on
  ``PATH``), no line of the event log and no output carries either value.

The names of the credentials in the tracked code are pinned in ``test_release_publish_guard.py``.
"""

import os
import re
import shutil
import stat
from pathlib import Path

import pytest

from tests.release_support.world import World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
#: made-up values of the two variables the usual upload tools read; nothing real is read or sent
AMBIENT = {"UV_PUBLISH_TOKEN": "fake-token-4c1f9a27", "TWINE_PASSWORD": "fake-password-93b6e0d1"}
#: the commands the release runs that could carry an argument to a server
RECORDED = ("git", "curl", "gh", "uv", "python3", "timeout")
#: what a ``curl`` that authenticates or sends something would carry: a user, a header, a netrc or config file, a body, a file
SENDING = re.compile(
    r"--user\b| -u |Authorization|--netrc|--config\b| -T |--upload-file| -F |--form\b| -X |--data|--json",
)
SHIM = """#!/bin/sh
# record the command line, then run the tool this shim stands for (PATH is left as it is: its children are recorded too)
printf '%s\\n' "{name} $*" >> "$ARGV_LOG"
exec "{tool}" "$@"
"""


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _recording(tmp_path: Path, world: World) -> dict[str, str]:
    """Shims for ``RECORDED`` in front of the world's fakes: the environment that makes them log into ``argv.log``."""
    shims = tmp_path / "shims"
    shims.mkdir()
    behind = f"{world.bin}:{os.environ['PATH']}"
    for name in RECORDED:
        tool = shutil.which(name, path=behind)
        assert tool is not None, name
        shim = shims / name
        shim.write_text(SHIM.format(name=name, tool=tool), encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return {
        "PATH": f"{shims}:{behind}",
        "ARGV_LOG": str(tmp_path / "argv.log"),
    }


def _argv(tmp_path: Path) -> list[str]:
    log = tmp_path / "argv.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


class TestNoEnvironmentFile:
    def test_a_release_needs_none(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        assert [p.name for p in world.root.iterdir() if p.name.startswith(".en")] == []

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert len(world.dispatches()) == 1
        assert world.uv_publishes() == []
        assert world.uploads() == []
        assert set(world.pypi()) == {f"axmem-{VERSION}.tar.gz", f"axmem-{VERSION}-py3-none-any.whl"}

    def test_the_read_only_report_needs_none(self, tmp_path: Path) -> None:
        world = World(tmp_path)

        done = world.read_only()

        assert done.returncode == 0, _out(done)
        assert "No published versions of axmem on PyPI yet." in done.stdout
        assert world.dispatches() == []


class TestAnEnvironmentFileThatCannotBeOpenedAndAmbientSecrets:
    def test_the_release_goes_through_and_nothing_was_opened(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        trap = world.trap_env()

        done = world.release(VERSION, **AMBIENT)

        assert done.returncode == 0, _out(done)
        assert trap.is_dir()
        assert list(trap.iterdir()) == []
        assert len(world.dispatches()) == 1
        assert world.uv_publishes() == []
        assert world.uploads() == []

    def test_the_read_only_report_goes_through_too(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        world.trap_env()

        done = world.read_only()

        assert done.returncode == 0, _out(done)
        assert "No published versions of axmem on PyPI yet." in done.stdout

    def test_no_command_the_release_ran_carries_an_ambient_secret(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        world.trap_env()

        done = world.release(VERSION, **_recording(tmp_path, world), **AMBIENT)

        assert done.returncode == 0, _out(done)
        argv = _argv(tmp_path)
        assert {line.split()[0] for line in argv} >= {"git", "curl", "gh", "uv", "python3"}, "the shims saw the release"
        for value in AMBIENT.values():
            assert [line for line in argv if value in line] == []
            assert [line for line in world.events() if value in line] == []
            assert value not in _out(done)

    def test_no_command_authenticates_or_sends_a_file_to_pypi(self, tmp_path: Path) -> None:
        world = World(tmp_path)

        done = world.release(VERSION, **_recording(tmp_path, world))

        assert done.returncode == 0, _out(done)
        argv = _argv(tmp_path)
        curls = [line for line in argv if line.startswith("curl ")]
        assert curls, "PyPI is read with curl"
        assert [line for line in curls if "--proto =https" not in line] == []
        assert [line for line in curls if SENDING.search(line)] == []
        assert [line for line in argv if re.match(r"uv .*\bpublish\b|python3 .*\btwine\b", line)] == []
