# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The whole release flow, run on the real script against a fake remote, ``gh``, ``uv`` and PyPI.

A release uploads nothing from this machine: it asks the publish workflow to, once the signed files exist, and PyPI never
accepts a file name twice, so a publication that happened and then failed would leave a version that no signed tag names.
The invariant these tests pin is that the dispatch of ``publish.yml`` is the last step of the release: it comes only after
the exact release commit is on the remote, CI is green on it, its tag is on the remote, ``release.yml`` signed it and the
files downloaded from the GitHub release are the manifest's bytes. PyPI is read with GET only, and the release ends with
the files PyPI serves compared to the manifest. Every case asserts how many times the workflow was dispatched, and the
cases that stop a release run it again to prove that the script resumes at the first unfinished step with no new commit and
no new tag. ``test_release_pipeline_axmem.py`` pins what is particular to Axmem (the pinned version, the content of the
release commit, the branch it runs on), ``test_release_pipeline_dispatch.py`` the contract of the dispatch itself.
"""

import shutil
from pathlib import Path

import pytest

from tests.release_support.world import PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
RESUME = f"release-pypi.sh {VERSION}"
JSON_URL = f"curl https://pypi.org/pypi/{PACKAGE}/json"
FILES_URL = "curl https://files.pythonhosted.org/packages/"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _order(world: World, *needles: str) -> None:
    """Each needle appears in the event log, each after the one before it."""
    events = world.events()
    last = -1
    for needle in needles:
        found = [i for i, line in enumerate(events) if line.startswith(needle) and i > last]
        assert found, f"{needle!r} did not come after position {last} in {events}"
        last = found[0]


def _recorded(world: World) -> dict[str, str]:
    return {entry["name"]: entry["sha256"] for entry in world.manifest()["artifacts"]}


class TestAllGreen:
    def test_the_dispatch_is_the_last_step_and_happens_once(self, world: World) -> None:
        done = world.release(VERSION)
        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        _order(
            world,
            "gh ci-head green",
            "gate stage-ci",
            "build",
            "gate check-package.sh",
            "hook pre-commit",
            "hook pre-push refs/heads/main",
            "gh ci green",
            "hook pre-push refs/tags/",
            "gh release green",
            "gh release-download",
            "gh ci green",
            "gh release green",
            "gh release-download",
            "gh workflow-run",
            FILES_URL,
        )

    def test_nothing_but_reads_follows_the_dispatch(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0
        events = world.events()
        after = events[events.index(DISPATCH) + 1 :]
        assert after
        assert all(line.startswith("curl ") for line in after), after

    def test_the_script_builds_once(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0
        assert len([line for line in world.events() if line.startswith("build ")]) == 1

    def test_the_tag_and_the_release_commit_are_on_the_remote_before_the_dispatch(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0
        release_commit = world.head()
        assert world.origin_git("rev-parse", "main") == release_commit
        assert world.origin_git("rev-parse", f"{TAG}^{{commit}}") == release_commit
        assert world.git("rev-list", "--count", f"{world.state_base()}..HEAD") == "1"
        assert world.origin_git("cat-file", "-t", TAG) == "tag"

    def test_pypi_holds_exactly_the_manifests_bytes_and_the_script_compared_them(
        self, world: World,
    ) -> None:
        done = world.release(VERSION)
        assert done.returncode == 0, _out(done)
        assert world.pypi() == _recorded(world)
        assert set(_recorded(world)) == {
            f"{PACKAGE}-{VERSION}.tar.gz",
            f"{PACKAGE}-{VERSION}-py3-none-any.whl",
        }
        files = [line for line in world.events() if line.startswith(FILES_URL)]
        assert sorted(files) == sorted(f"{FILES_URL}{name}" for name in _recorded(world))
        assert done.stdout.splitlines()[-1].startswith("Release complete: axmem 0.2.0 on PyPI")

    def test_pypi_is_read_with_get_only_and_nothing_is_ever_uploaded_from_this_machine(
        self, world: World,
    ) -> None:
        world.trap_env()
        done = world.release(VERSION)
        assert done.returncode == 0, _out(done)
        reads = [line for line in world.events() if line.startswith("curl ")]
        assert reads
        assert all(line.startswith((JSON_URL, FILES_URL)) for line in reads), reads
        assert not [line for line in world.events() if line.startswith("curl-")]
        assert world.uploads() == []
        assert world.uv_publishes() == []

    def test_a_release_reads_pypi_before_it_changes_anything(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0
        events = world.events()
        assert events.index(JSON_URL) < events.index("gate stage-ci")


class TestNothingIsDispatchedBeforeTheCommitIsAccepted:
    def test_a_refused_commit_stops_before_anything_is_pushed_or_dispatched(
        self, world: World,
    ) -> None:
        base = world.head()
        world.mark("refuse-commit")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert world.head() == base
        assert world.git("status", "--porcelain", "--untracked-files=no") == ""
        assert world.origin_git("rev-parse", "main") == base

    def test_a_refused_push_of_main_dispatches_nothing_and_a_rerun_resumes_there(
        self, world: World,
    ) -> None:
        world.mark("refuse-push-main")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert RESUME in _out(done)
        commits = world.commit_count()
        world.mark("refuse-push-main", on=False)
        rerun = world.release(VERSION)
        assert rerun.returncode == 0, _out(rerun)
        assert world.dispatches() == [DISPATCH]
        assert world.commit_count() == commits

    def test_a_refused_push_of_the_tag_stops_and_a_rerun_pushes_the_same_tag(
        self, world: World,
    ) -> None:
        world.mark("refuse-push-tag")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == ""
        local = world.git("rev-parse", f"{TAG}^{{commit}}")
        world.mark("refuse-push-tag", on=False)
        assert world.release(VERSION).returncode == 0, _out(done)
        assert world.origin_git("rev-parse", f"{TAG}^{{commit}}") == local
        assert world.dispatches() == [DISPATCH]
