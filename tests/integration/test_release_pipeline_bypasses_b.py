# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The ways past the release guard that go through what PyPI serves, each closed and proved on the real script.

The integrity check once proved a tag name, a run and file names, never the bytes PyPI holds, and an answer that is not
the JSON of the project was read as a project with nothing on it. These are the cases of the PyPI side: a signed manifest
that lists other bytes than PyPI serves, a tag without a manifest, bytes that differ from a correct release, a version that
appears on PyPI while the release waits, a version that holds only some of its files, a version that is spent, a version
without a tag, a project that PyPI no longer knows while its tags exist, and an answer that is not the project's JSON.
Every case runs the whole script in the scratch world and asserts the number of dispatches of ``publish.yml``, so a fix
that stops working shows here and not on PyPI.
"""

import shutil
from pathlib import Path

import pytest

from tests.release_support.world import PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
WHEEL = f"{PACKAGE}-{VERSION}-py3-none-any.whl"
SDIST = f"{PACKAGE}-{VERSION}.tar.gz"
SPENT = "that version is spent"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _put_on_pypi(world: World, name: str, content: str) -> str:
    """A shell command, for a hook of the fake ``gh``, that puts a file on the fake PyPI while the script waits."""
    return f"printf '%s' '{content}' > '{world.state / 'pypi' / name}'"


def _copy_built_to_pypi(world: World, name: str) -> str:
    """A shell command that puts the file the release built under ``name`` on the fake PyPI, as a partial upload would."""
    return f"cp '{world.state / 'built' / name}' '{world.state / 'pypi' / name}'"


class TestWhatPyPIServesIsWhatWasSigned:
    """The integrity check proved a tag name, a run and file names, never the bytes PyPI holds."""

    def test_an_old_version_whose_signed_manifest_lists_other_bytes_blocks_the_release(
        self, world: World,
    ) -> None:
        world.add_old_release("0.1.0", signed=True, manifest="other-digest")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "the signed manifest at v0.1.0 says " + "f" * 64 in _out(done)

    def test_an_old_version_whose_tag_has_no_manifest_blocks_the_release(
        self, world: World,
    ) -> None:
        world.add_old_release("0.1.0", signed=True, manifest="none")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "release-digests.json cannot be read at v0.1.0" in _out(done)

    def test_bytes_that_differ_from_a_correct_release_block_the_next_one(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0
        (world.state / "pypi" / WHEEL).write_bytes(b"EVIL: not the signed wheel\n")

        done = world.release("0.3.0")

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert f"{WHEEL}: PyPI serves" in _out(done)
        assert "the signed manifest at v0.2.0 says" in _out(done)

    def test_the_resume_path_is_checked_too(self, world: World) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        world.add_old_release("0.1.5", signed=False)

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert f"REFUSED  {PACKAGE} 0.1.5 on PyPI" in _out(done)

    def test_a_finished_release_is_not_reported_done_beside_an_unsigned_version(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0
        world.add_old_release("0.1.5", signed=False)

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert "Release complete" not in _out(done)

    def test_a_version_that_appears_on_pypi_while_the_release_waits_stops_the_dispatch(
        self, world: World,
    ) -> None:
        hook = _put_on_pypi(world, f"{PACKAGE}-0.1.5.tar.gz", "unsigned")
        world.configure(gh_hooks=[{"on": "release-download", "nth": 1, "cmd": hook}])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert f"REFUSED  {PACKAGE} 0.1.5 on PyPI" in _out(done)

    def test_this_version_appearing_on_pypi_with_other_bytes_while_the_release_waits_stops_the_dispatch(
        self, world: World,
    ) -> None:
        hook = _put_on_pypi(world, SDIST, "somebody else")
        world.configure(gh_hooks=[{"on": "release-download", "nth": 1, "cmd": hook}])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert f"REFUSED  {PACKAGE} {VERSION} on PyPI" in _out(done)
        assert f"{SDIST}: PyPI serves" in _out(done)

    def test_a_yanked_version_without_a_tag_stays_a_stop_because_its_file_names_stay_used(
        self, world: World,
    ) -> None:
        names = world.add_old_release("0.1.5", signed=False)
        world.configure(yanked=names)

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert f"REFUSED  {PACKAGE} 0.1.5 on PyPI" in _out(done)


class TestAVersionWithoutATag:
    def test_a_version_on_pypi_that_no_tag_names_blocks_a_new_release(self, world: World) -> None:
        world.add_old_release("0.1.5", signed=False)
        base = world.head()

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "tag v0.1.5 is not on axiumine" in _out(done)
        assert world.dispatches() == []
        assert world.head() == base
        assert world.origin_git("tag", "--list") == ""

    def test_a_release_whose_tag_was_lost_is_not_dispatched_again_and_not_called_done(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0
        world.origin_git("tag", "-d", TAG)

        done = world.release(VERSION)

        assert done.returncode != 0
        assert f"tag {TAG} is not on axiumine" in _out(done)
        assert "Release complete" not in _out(done)
        assert world.dispatches() == [DISPATCH]

    def test_a_tag_on_the_remote_and_nothing_on_pypi_does_not_block_the_next_version(
        self, world: World,
    ) -> None:
        world.add_old_release("0.1.0", signed=True)
        for path in (world.state / "pypi").glob("*"):
            path.unlink()

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert "0 version(s) on PyPI, 0 refused" in _out(done)

    def test_a_rehearsal_tag_without_any_version_on_pypi_does_not_block_the_next_version(
        self, world: World,
    ) -> None:
        world.origin_git("tag", "v0.2.0rc1", world.state_base())

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]


class TestAnAnswerThatIsNotTheProjectsJson:
    """An answer that was not the JSON of a project was read as a project with nothing on it."""

    def test_a_maintenance_page_does_not_hide_an_unsigned_version(self, world: World) -> None:
        world.add_old_release("0.1.5", signed=False)
        world.configure(json_raw="<html><body><h1>Maintenance</h1></body></html>")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "is not the JSON of a project" in _out(done)

    def test_a_page_that_turns_into_one_before_the_dispatch_stops_it(self, world: World) -> None:
        world.configure(json_raw=[None, "<html><body>Sorry</body></html>"])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "is not the JSON of a project" in _out(done)

    def test_a_document_of_another_project_is_not_this_projects_state(self, world: World) -> None:
        world.add_old_release("0.1.5", signed=False)
        world.configure(json_raw='{"info": {"name": "another"}, "releases": {}}')

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "is not the JSON of axmem" in _out(done)


class TestAVersionThatHoldsOnlySomeOfItsFiles:
    """A red publish run can leave some of the files on PyPI: the same signed bytes are sent again, and no others."""

    def _partly_published(self, world: World) -> None:
        """A publish run that put the wheel on PyPI and then failed; the second dispatch succeeds."""
        world.configure(
            publish_runs=[["queued", "failure"], ["queued", "success"]],
            gh_hooks=[{"on": "publish-dispatch", "nth": 1, "cmd": _copy_built_to_pypi(world, WHEEL)}],
        )

    def test_the_re_run_passes_the_integrity_check_and_dispatches_again_for_the_missing_file(
        self, world: World,
    ) -> None:
        self._partly_published(world)
        first = world.release(VERSION)
        assert first.returncode != 0
        assert list(world.pypi()) == [WHEEL]
        wheel = world.pypi()[WHEEL]
        commits = world.commit_count()

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH, DISPATCH]
        assert world.pypi()[WHEEL] == wheel
        assert sorted(world.pypi()) == [WHEEL, SDIST]
        assert world.commit_count() == commits

    def test_the_first_run_says_what_a_re_run_will_do(self, world: World) -> None:
        self._partly_published(world)

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "did not succeed" in _out(done)
        assert f"bash scripts/release-pypi.sh {VERSION}" in _out(done)
        assert "may be live or done" in _out(done)

    def test_a_file_whose_bytes_differ_from_the_manifest_makes_the_version_spent(
        self, world: World,
    ) -> None:
        world.configure(
            publish_runs=["queued", "failure"],
            gh_hooks=[{"on": "publish-dispatch", "nth": 1, "cmd": _put_on_pypi(world, WHEEL, "other bytes")}],
        )
        assert world.release(VERSION).returncode != 0

        done = world.release(VERSION)

        assert done.returncode != 0
        assert SPENT in _out(done)
        assert f"{WHEEL}" in _out(done)
        assert world.dispatches() == [DISPATCH]

    def test_a_yanked_file_counts_as_present_and_the_re_run_sends_only_what_is_missing(
        self, world: World,
    ) -> None:
        self._partly_published(world)
        assert world.release(VERSION).returncode != 0
        world.configure(yanked=[WHEEL])

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH, DISPATCH]
        assert "(yanked)" in _out(done)


class TestAVersionThatIsSpent:
    """PyPI never accepts a file name twice, even after a delete or a yank: the next version is the way on."""

    def test_a_version_on_pypi_with_other_bytes_and_no_release_commit_is_spent(
        self, world: World,
    ) -> None:
        (world.state / "pypi" / WHEEL).write_bytes(b"somebody else's wheel\n")
        (world.state / "pypi" / SDIST).write_bytes(b"somebody else's sdist\n")
        base = world.head()

        done = world.release(VERSION)

        assert done.returncode != 0
        assert f"{PACKAGE} {VERSION} is already on PyPI and no release commit of it exists" in _out(done)
        assert SPENT in _out(done)
        assert world.dispatches() == []
        assert world.head() == base
        assert world.origin_git("tag", "--list") == ""

    def test_a_version_on_pypi_with_other_bytes_than_the_manifest_never_gets_a_dispatch(
        self, world: World,
    ) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        (world.state / "pypi" / WHEEL).write_bytes(b"somebody else's wheel\n")
        commits = world.commit_count()

        done = world.release(VERSION)

        assert done.returncode != 0
        assert SPENT in _out(done)
        assert world.dispatches() == []
        assert world.commit_count() == commits

    def test_a_digest_that_pypi_lists_differently_from_the_file_it_serves_is_also_spent(
        self, world: World,
    ) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        (world.state / "pypi" / SDIST).write_bytes(b"x")
        world.configure(digest_lie=[SDIST])

        done = world.release(VERSION)

        assert done.returncode != 0
        assert SPENT in _out(done)
        assert world.dispatches() == []
