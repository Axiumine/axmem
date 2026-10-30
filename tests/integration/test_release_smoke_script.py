# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``scripts/smoke-release.sh`` installs the files of a release from an index and uses the server they start, and leaves nothing behind.

The real script runs in a scratch world (``tests/release_support/smoke_script.py``): a repository with the tag ``v0.2.0`` and
its ``release-digests.json``, a fake index (TestPyPI and PyPI), and fake ``curl``, ``uv``, ``docker``, ``nvidia-smi`` and
installed ``axmem`` put first on ``PATH``. Nothing needs a GPU, Docker or the network; the script is run as an executable, from
a directory outside the repository. This file holds the text of the script (its hosts, image and extra index are the ones of the
release flow, the test suites and ``pyproject.toml`` and are written once) and what it refuses before it starts anything:
arguments, version, index, tools, GPU, Docker, tag and manifest. ``test_release_smoke_script_index.py`` holds what it refuses
once it has read the index, ``test_release_smoke_script_run.py`` the install and the run, ``test_release_smoke_script_cleanup.py``
what is stopped whatever stops the run. ``test_release_testpypi_scripts_text.py`` holds the same facts for the rehearsal.
"""

import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.release import facts
from tests.qdrant_support import IMAGE
from tests.release_support.script import TESTPYPI_FLOW
from tests.release_support.smoke_script import (
    INDEXES,
    REAL_TOOLS,
    SCRIPT,
    SmokeWorld,
    nothing_started,
    refused,
)
from tests.release_support.testpypi_script import TAG, VERSION, out
from tests.release_support.world import PACKAGE, REPO_ROOT

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in REAL_TOOLS), reason=f"needs {' '.join(REAL_TOOLS)}",
)

PROJECT_LIB = REPO_ROOT / "scripts" / "lib" / "project.sh"
USAGE = "Usage: smoke-release.sh [--keep] [--index testpypi|pypi] <version>"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[SmokeWorld]:
    made = SmokeWorld(tmp_path)
    yield made
    made.stop_leftovers()


@pytest.fixture
def make_world(tmp_path: Path) -> Iterator[Callable[..., SmokeWorld]]:
    made: list[SmokeWorld] = []

    def build(**options: Any) -> SmokeWorld:
        made.append(SmokeWorld(tmp_path, **options))
        return made[-1]

    yield build
    for each in made:
        each.stop_leftovers()


def _code(path: Path) -> str:
    """The text of a script without its comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _constants(path: Path) -> dict[str, str]:
    """The ``NAME="value"`` lines of a shell file whose value holds no expansion."""
    return dict(re.findall(r'^([A-Z_]+)="([^"$]*)"', path.read_text(encoding="utf-8"), re.MULTILINE))


class TestTheConstants:
    def test_the_hosts_the_image_and_the_extra_index_are_those_of_the_flow_the_suites_and_the_project(self) -> None:
        shell, project, rehearsal = _constants(SCRIPT), _constants(PROJECT_LIB), _constants(TESTPYPI_FLOW)
        uv = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]
        (nvidia,) = [entry for entry in uv["index"] if entry["name"] == "nvidia"]

        expected = {
            "TESTPYPI_HOST": rehearsal["TESTPYPI_HOST"],
            "TESTPYPI_FILES_HOST": facts.INDEXES["testpypi"].file_host,
            "PYPI_HOST": project["PYPI_HOST"],
            "PYPI_FILES_HOST": facts.INDEXES["pypi"].file_host,
            "NVIDIA_INDEX": nvidia["url"],
            "QDRANT_IMAGE": IMAGE,
            "CONTAINER_LABEL": "axmem-wheelsmoke=1",
            "PKG": project["PKG"],
        }

        assert {key: shell[key] for key in expected} == expected
        assert (shell["TESTPYPI_HOST"], shell["PYPI_HOST"], shell["PKG"]) == ("test.pypi.org", "pypi.org", PACKAGE)

    def test_the_indexes_the_tests_use_are_the_rows_of_the_table_of_the_flow(self) -> None:
        assert set(INDEXES) == set(facts.INDEXES)
        assert {key: files_host for key, (_host, files_host) in INDEXES.items()} == {
            key: facts.index_row(key).file_host for key in INDEXES
        }

    @pytest.mark.parametrize(
        "literal",
        [
            r"(?<![\w.-])pypi\.org",
            r"(?<![\w.-])test\.pypi\.org",
            r"(?<![\w.-])files\.pythonhosted\.org",
            r"(?<![\w.-])test-files\.pythonhosted\.org",
            r"pypi\.nvidia\.com",
            r"qdrant/qdrant",
        ],
    )
    def test_each_host_and_the_image_is_written_once_in_the_code_of_the_script(self, literal: str) -> None:
        assert len(re.findall(literal, _code(SCRIPT))) == 1

    def test_the_install_has_one_extra_index_and_it_is_the_constant_never_a_scratch_index(self) -> None:
        code = _code(SCRIPT)
        uv_lines = "\n".join(line for line in code.splitlines() if re.search(r"\buv\b", line))

        assert code.count("--extra-index-url") == 1
        assert '--extra-index-url "$NVIDIA_INDEX"' in code
        assert not re.search(r"--index\b|--index-url|--default-index|--find-links|\s-i\s|\s-f\s", uv_lines)
        assert "TESTPYPI" not in uv_lines
        assert re.findall(r"\$\{?TESTPYPI_(?:FILES_)?HOST", code) == ["$TESTPYPI_HOST", "$TESTPYPI_FILES_HOST"]

    def test_the_script_is_an_executable_that_says_what_it_is_and_ends_on_its_success_line(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        last = _code(SCRIPT).rstrip().splitlines()[-1]

        assert os.access(SCRIPT, os.X_OK)
        assert text.startswith("#!/usr/bin/env bash\n")
        assert "# Usage:\n#   ./scripts/smoke-release.sh [--keep] [--index testpypi|pypi] <version>" in text
        assert "advisory, not a gate" in text
        assert last == 'echo "${INDEX} install and server check passed: ${PKG} ${VERSION}"'


class TestTheRefusalsBeforeAnythingStarts:
    @pytest.mark.parametrize("args", [(), ("0.2.0", "0.3.0"), ("--keep",), ("--index", "pypi")])
    def test_the_number_of_arguments_is_one(self, world: SmokeWorld, args: tuple[str, ...]) -> None:
        done = world.run(*args)

        refused(done, f"exactly one argument, the version. {USAGE}")
        assert world.lines() == []

    @pytest.mark.parametrize(
        "version",
        ["0.2", "v0.2.0", "0.2.0-rc1", "0.2.0rc", "0.02.0", "1.2.3.4", "0.2.0 ", "../0.2.0", "0.2.0;ls"],
    )
    def test_the_version_is_canonical_as_the_release_script_reads_it(self, world: SmokeWorld, version: str) -> None:
        done = world.run(version)

        refused(done, f"'{version}' is not a canonical version")
        assert world.lines() == []

    @pytest.mark.parametrize("version", ["0.2.0rc1", "0.2.0a3", "0.2.0b1", "0.2.0.post1", "0.2.0.dev3", "10.20.30"])
    def test_every_version_the_release_script_releases_passes_that_check(self, world: SmokeWorld, version: str) -> None:
        refused(world.run(version), f"no tag v{version} here")

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("--index", "test", "0.2.0"), "--index is testpypi or pypi, not 'test'"),
            (("--index", "", "0.2.0"), "--index is testpypi or pypi, not ''"),
            (("--index", "PyPI", "0.2.0"), "--index is testpypi or pypi, not 'PyPI'"),
            (("--index=pypi", "0.2.0"), "unknown option --index=pypi"),
            (("--bogus", "0.2.0"), "unknown option --bogus"),
            (("--index",), "--index needs a value"),
        ],
    )
    def test_an_unknown_index_or_option_is_refused(self, world: SmokeWorld, args: tuple[str, ...], message: str) -> None:
        done = world.run(*args)

        refused(done, message)
        assert world.lines() == []

    def test_a_tool_that_is_not_installed_is_named(self, world: SmokeWorld) -> None:
        sparse = world.tmp / "sparse"
        sparse.mkdir()
        for tool in (*REAL_TOOLS, "dirname", "basename"):
            (sparse / tool).symlink_to(shutil.which(tool) or "/nonexistent")
        for tool in ("curl", "uv", "nvidia-smi"):
            (sparse / tool).symlink_to(world.bin / tool)

        done = world.run(VERSION, PATH=str(sparse))

        refused(done, "'docker' is not installed")
        assert world.lines() == []

    def test_no_nvidia_gpu_that_answers_stops_it_before_docker_is_asked(self, world: SmokeWorld) -> None:
        world.configure(nvidia_rc=1)

        done = world.run(VERSION)

        refused(done, "no NVIDIA GPU answers (nvidia-smi -L)")
        assert world.lines() == ["nvidia-smi -L"]

    def test_a_docker_daemon_that_does_not_answer_stops_it_before_anything_is_read(self, world: SmokeWorld) -> None:
        world.configure(docker_info_rc=1)

        done = world.run(VERSION)

        refused(done, "the Docker daemon does not answer")
        assert world.lines() == ["nvidia-smi -L", "docker info"]
        nothing_started(world)

    def test_a_tag_this_checkout_does_not_hold_stops_it_before_the_index_is_read(
        self, make_world: Callable[..., SmokeWorld],
    ) -> None:
        world = make_world(tag=False)

        done = world.run(VERSION)

        refused(done, f"no tag {TAG} here")
        assert world.calls("curl") == []
        nothing_started(world)

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({"manifest": False}, f"the tag {TAG} holds no release-digests.json"),
            ({"manifest": {"version": "0.2.1"}}, f"the manifest of {TAG} is for another version"),
            ({"manifest": {"package": "forgetful"}}, f"the manifest of {TAG} is for another package"),
            ({"manifest": {"artifacts": []}}, f"the manifest of {TAG} lists no file"),
        ],
        ids=["no-manifest", "other-version", "other-package", "no-file"],
    )
    def test_a_tag_whose_manifest_is_not_this_release_is_refused(
        self, make_world: Callable[..., SmokeWorld], options: dict[str, Any], message: str,
    ) -> None:
        world = make_world(**options)

        done = world.run(VERSION)

        refused(done, message)
        assert world.calls("curl") == []
        nothing_started(world)

    def test_the_tag_is_read_in_the_checkout_that_holds_the_script_not_in_the_directory_it_is_run_from(
        self, world: SmokeWorld,
    ) -> None:
        elsewhere = world.tmp / "elsewhere"
        elsewhere.mkdir()
        subprocess.run(["git", "-C", str(elsewhere), "init", "-q"], check=True, env=world.env())  # noqa: S603, S607 - a program of the test setup, run without a shell

        done = world.run(VERSION, cwd=elsewhere)

        assert done.returncode == 0, out(done)
        assert done.stdout.splitlines()[-1] == f"testpypi install and server check passed: axmem {VERSION}"
