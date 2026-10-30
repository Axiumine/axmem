# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""What is particular to Axmem in a release, run on the real script against the scratch world.

The version of the package comes from the tag and is written nowhere, so the release commit holds the changelog and the
digest manifest and nothing else, and every build (the one of the release, the one ``release.yml`` signs) pins
``SETUPTOOLS_SCM_PRETEND_VERSION`` and ``SOURCE_DATE_EPOCH``; the release builds once, and what PyPI is given is the
signed files, never a second build. A release runs on the local branch that tracks
``axiumine/main``, whatever that branch is called, and is pushed to the remote's ``main``.
The cases that stop or resume a release are in ``test_release_pipeline.py``.
"""

import shutil
from pathlib import Path

import pytest

from tests.release_support.world import PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
RELEASE_MESSAGE = f"🤖 chore(release): release {PACKAGE} {VERSION} to PyPI"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


class TestWhatTheReleaseCommits:
    def test_the_release_commit_holds_the_changelog_and_the_manifest_only(
        self, world: World,
    ) -> None:
        base = world.state_base()
        assert world.release(VERSION).returncode == 0
        changed = world.git("diff", "--name-only", base, "HEAD").splitlines()
        assert sorted(changed) == ["CHANGELOG.md", "release-digests.json"]
        assert world.git("log", "-1", "--format=%s") == RELEASE_MESSAGE
        assert world.git("tag", "-l", "--format=%(contents:subject)", TAG) == RELEASE_MESSAGE

    def test_the_changelog_is_stamped_with_the_tag_and_a_new_unreleased_section_opens(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0
        lines = world.git("show", "HEAD:CHANGELOG.md").splitlines()
        stamped = [line for line in lines if line.startswith(f"## [{TAG}] - ")]
        assert len(stamped) == 1
        assert lines.index("## [Unreleased]") < lines.index(stamped[0])

    def test_the_version_is_written_nowhere_and_nothing_is_locked(self, world: World) -> None:
        pyproject = (world.root / "pyproject.toml").read_text(encoding="utf-8")
        assert world.release(VERSION).returncode == 0
        assert (world.root / "pyproject.toml").read_text(encoding="utf-8") == pyproject
        assert not (world.root / "uv.lock").exists()
        assert world.manifest()["version"] == VERSION

    def test_the_manifest_names_the_package_the_tag_and_the_epoch_and_no_index(
        self, world: World,
    ) -> None:
        epoch = world.git("log", "-1", "--pretty=%ct", world.state_base())
        assert world.release(VERSION).returncode == 0
        manifest = world.manifest()
        assert (manifest["package"], manifest["tag"]) == (PACKAGE, TAG)
        assert manifest["source_date_epoch"] == int(epoch)
        assert "index" not in manifest


class TestEveryBuildPinsTheVersionAndTheEpoch:
    def test_the_one_build_pins_the_version_and_the_epoch_of_the_head_commit(
        self, world: World,
    ) -> None:
        epoch = world.git("log", "-1", "--pretty=%ct", world.state_base())
        assert world.release(VERSION).returncode == 0
        builds = [line for line in world.events() if line.startswith("build ")]
        assert builds == [f"build {VERSION} epoch={epoch} variant=a"]
        assert world.manifest()["source_date_epoch"] == int(epoch)

    def test_a_build_that_names_another_version_than_the_release_is_refused(
        self, world: World,
    ) -> None:
        base = world.head()
        world.configure(build_version="9.9.9")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert f"did not produce dist/{PACKAGE}-{VERSION}-py3-none-any.whl" in _out(done)
        assert world.dispatches() == []
        assert world.head() == base
        assert world.origin_git("tag", "--list") == ""


class TestTheBranchTheReleaseRunsOn:
    def test_the_branch_is_not_called_main_and_the_release_goes_to_the_remotes_main(
        self, world: World,
    ) -> None:
        assert world.branch != "main"
        assert world.release(VERSION).returncode == 0
        assert world.git("rev-parse", "--abbrev-ref", "HEAD") == world.branch
        assert world.origin_git("rev-parse", "refs/heads/main") == world.head()
        assert world.origin_git("for-each-ref", "--format=%(refname)", "refs/heads") == "refs/heads/main"

    def test_a_branch_that_does_not_track_the_remotes_main_is_refused(self, world: World) -> None:
        world.git("switch", "-q", "-c", "feature")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "tracks axiumine/main" in _out(done)
        assert not [
            line for line in world.events() if line.startswith(("gh workflow", "gate", "build"))
        ]
        assert world.origin_git("tag", "--list") == ""
