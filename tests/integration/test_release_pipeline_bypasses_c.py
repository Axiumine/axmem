# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The ways past the release guard that go through the tree, the version and the environment, each closed and proved.

A tree with a change is built into ``dist/`` and the manifest but is not in the tag, which burns the version. A version
that is not canonical reached PyPI under another name than the tag and blocked every later release. A tag name that is
already taken (an inherited tag, a tag of the remote, a branch of the remote), a hook skipped by the environment, a
variable that turns a failing release into a dispatch, and a wait that the shell would evaluate are refused before
anything runs or before the release commit exists. So are a clone that is not where a release runs (a branch that does
not track the remote's main, a remote that is not on github.com, a branch behind, diverged or ahead of it) and a commit
that has no green CI. Every case runs the whole script in the scratch world and asserts the number of dispatches of
``publish.yml`` (it must stay zero when the case is a refusal), so a fix that stops working shows here and not on PyPI.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.release_support.world import World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
#: the events of the steps that come after the first read of PyPI and the check of the local branch
LATER_STEPS = ("gate", "build")
#: the reads of the repository settings that every release makes before it changes anything
SETTINGS = "gh api repos/"
#: the tracked file of the scratch repository that the cases of the tree change
TRACKED = "app/__init__.py"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _later_steps(world: World) -> list[str]:
    """The events of the gate and the build."""
    return [line for line in world.events() if line.startswith(LATER_STEPS)]


def _commit_locally(world: World) -> None:
    """One commit on the local branch that the remote does not have."""
    (world.root / "local.txt").write_text("local\n", encoding="utf-8")
    world.git("add", "local.txt")
    world.git("commit", "-q", "-m", "local only")


class TestARunNeverBuildsTheWrongTree:
    """A tree with a change is built into dist/ and the manifest, but is not in the tag: the version is burnt."""

    def test_a_modified_tracked_file_stops_the_release_before_anything_is_built(
        self, world: World,
    ) -> None:
        base = world.head()
        (world.root / TRACKED).write_text("x = 999\n", encoding="utf-8")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "tracked files have uncommitted changes" in _out(done)
        assert world.dispatches() == []
        assert world.head() == base
        assert world.origin_git("rev-parse", "main") == base
        assert world.origin_git("tag", "--list") == ""
        assert not [
            line for line in world.events() if line.startswith(("build", "gate"))
        ]

    def test_a_staged_change_stops_it_too(self, world: World) -> None:
        (world.root / "notes.txt").write_text("staged\n", encoding="utf-8")
        world.git("add", "notes.txt")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "uncommitted changes" in _out(done)
        assert world.dispatches() == []

    def test_a_change_that_git_was_told_to_ignore_stops_it_too(self, world: World) -> None:
        world.git("update-index", "--skip-worktree", TRACKED)
        (world.root / TRACKED).write_text("x = 999\n", encoding="utf-8")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "skip-worktree or assume-unchanged" in _out(done)
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == ""

    def test_a_formatter_hook_that_rewrites_a_tracked_file_stops_it_before_the_push(
        self, world: World,
    ) -> None:
        hook = world.root / ".git" / "hooks" / "post-commit"
        hook.write_text(f"#!/bin/sh\necho 'x = 3' > {TRACKED}\n", encoding="utf-8")
        hook.chmod(0o755)
        base = world.head()

        done = world.release(VERSION)

        assert done.returncode != 0
        assert "tracked files have uncommitted changes" in _out(done)
        assert world.dispatches() == []
        assert world.origin_git("rev-parse", "main") == base
        assert world.origin_git("tag", "--list") == ""


class TestATagNameThatIsTakenIsRefusedBeforeTheReleaseCommit:
    """A pushed tag is never moved: a name that is taken ends a new release before it changes anything."""

    def _refused_untouched(
        self, world: World, done: subprocess.CompletedProcess[str], word: str,
    ) -> None:
        assert done.returncode != 0
        assert word in _out(done)
        assert world.dispatches() == []
        assert world.head() == world.state_base()
        assert world.origin_git("rev-parse", "main") == world.state_base()
        assert not [
            line for line in world.events() if line.startswith(("build", "gate", "hook"))
        ]

    @pytest.mark.parametrize("annotated", [False, True], ids=["a lightweight tag", "an annotated tag"])
    def test_a_tag_of_that_name_in_this_clone_is_refused(
        self, world: World, annotated: bool,
    ) -> None:
        # the clone holds the tags the project inherited from its first history; a version at or below them collides
        world.git("tag", *(["-a", "-m", "inherited"] if annotated else []), TAG, "HEAD")

        done = world.release(VERSION)

        self._refused_untouched(world, done, f"the tag {TAG} exists locally")
        assert world.origin_git("tag", "--list") == ""

    def test_a_tag_of_that_name_on_the_remote_is_refused_and_stays_where_it_is(
        self, world: World,
    ) -> None:
        # a commit that main does not hold, so the fetch of main does not bring the tag into this clone
        elsewhere = world.origin_git("commit-tree", world.origin_git("rev-parse", "main^{tree}"), "-m", "x")
        world.origin_git("tag", "-a", TAG, elsewhere, "-m", "somebody else's tag")

        done = world.release(VERSION)

        self._refused_untouched(world, done, f"the name {TAG} is already taken on axiumine")
        assert world.origin_git("rev-parse", f"{TAG}^{{commit}}") == elsewhere

    def test_a_branch_of_that_name_on_the_remote_is_refused(self, world: World) -> None:
        world.origin_git("branch", TAG, world.state_base())

        done = world.release(VERSION)

        self._refused_untouched(world, done, f"the name {TAG} is already taken on axiumine")
        assert world.origin_git("tag", "--list") == ""

    def test_the_next_version_is_not_held_back_by_a_taken_name(self, world: World) -> None:
        world.origin_git("tag", "-a", TAG, world.state_base(), "-m", "a rehearsal")

        done = world.release("0.2.1")

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == ["gh workflow-run publish.yml tag=v0.2.1"]
        assert world.origin_git("rev-parse", f"{TAG}^{{commit}}") == world.state_base()


class TestTheVersionIsCanonical:
    """An un-normalised version reached PyPI under another name than the tag, and blocked every later release."""

    @pytest.mark.parametrize(
        "version",
        [
            "0.3.0-rc1",
            "1.0",
            "1",
            "01.2.3",
            "v1.2.3",
            "1.2.3.4",
            "1.2.3 ",
            "1.2.3;touch x",
            "$(id)",
        ],
    )
    def test_a_version_that_is_not_canonical_is_refused_before_anything_runs(
        self, world: World, version: str,
    ) -> None:
        done = world.release(version)

        assert done.returncode != 0
        assert "invalid version" in _out(done)
        assert world.events() == []

    @pytest.mark.parametrize("version", ["0.2.0", "0.2.0rc1", "0.2.1.dev0", "1.0.0.post2"])
    def test_a_canonical_version_is_accepted(self, world: World, version: str) -> None:
        done = world.release(version)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [f"gh workflow-run publish.yml tag=v{version}"]


class TestNoEnvironmentSwitchSkipsAControl:
    @pytest.mark.parametrize("name", ["SKIP", "ONLY"])
    @pytest.mark.parametrize("value", ["1", "0", "yes", "ruff"])
    def test_a_variable_that_leaves_gates_out_of_the_hooks_stops_the_release_before_anything_runs(
        self, world: World, name: str, value: str,
    ) -> None:
        done = world.release(VERSION, **{name: value})

        assert done.returncode != 0
        assert f"{name} is set" in _out(done)
        assert world.events() == []

    @pytest.mark.parametrize(
        "switches",
        [
            {"RELEASE_SKIP_CI_CHECK": "1"},
            {"RELEASE_SKIP_SIGNING": "1", "RELEASE_FORCE": "1", "FORCE": "1"},
            {"RELEASE_GUARD": "off", "SKIP_GUARD": "1", "DRY_RUN": "0"},
            {"UV_PUBLISH_TOKEN": "pypi-ambient-token", "UV_PUBLISH_URL": "https://upload.pypi.org/legacy/"},
            {"TWINE_PASSWORD": "pypi-ambient-token", "TWINE_USERNAME": "__token__", "PYPI_TOKEN": "x"},
        ],
    )
    @pytest.mark.parametrize("scenario", ["red-ci", "red-signing", "missing-bundle"])
    def test_no_variable_turns_a_failing_release_into_an_upload(
        self, world: World, switches: dict[str, str], scenario: str,
    ) -> None:
        world.configure(
            **{
                "red-ci": {"ci_release": ["red"]},
                "red-signing": {"release_runs": ["pending", "red"]},
                "missing-bundle": {"assets": "missing_bundle"},
            }[scenario],
        )

        done = world.release(VERSION, **switches)

        assert done.returncode != 0
        assert "STOPPED: " in _out(done)
        assert world.dispatches() == []

    @pytest.mark.parametrize(
        "value", ["a[$(touch PWNED)]", "0", "-5", "1.5", "soon", "nan", "$((1+1))"],
    )
    def test_a_wait_that_is_not_a_whole_number_above_zero_is_refused_and_never_evaluated(
        self, world: World, tmp_path: Path, value: str,
    ) -> None:
        value = value.replace("PWNED", str(tmp_path / "pwned"))

        done = world.release(VERSION, RELEASE_WAIT_TIMEOUT_S=value)

        assert done.returncode != 0
        assert "RELEASE_WAIT_TIMEOUT_S must be a whole number" in _out(done)
        assert not (tmp_path / "pwned").exists()
        assert world.events() == []

    @pytest.mark.parametrize("value", ["0", "0.0", "-1", "nan", "inf", "x", "1e3"])
    def test_a_poll_interval_that_is_not_a_number_above_zero_is_refused(
        self, world: World, value: str,
    ) -> None:
        done = world.release(VERSION, RELEASE_POLL_INTERVAL_S=value)

        assert done.returncode != 0
        assert "RELEASE_POLL_INTERVAL_S must be a number of seconds above 0" in _out(done)
        assert world.events() == []


class TestTheRepositoryIsNotWhereAReleaseRuns:
    def test_a_branch_that_does_not_track_the_remotes_main_is_refused(self, world: World) -> None:
        world.git("checkout", "-q", "-b", "feature")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "releases run on the local branch that tracks" in _out(done)
        assert all(line.startswith((SETTINGS, "curl ")) for line in world.events())
        assert world.dispatches() == []

    def test_a_remote_that_is_not_on_github_is_refused(self, world: World) -> None:
        world.git("remote", "set-url", world.remote, "https://example.invalid/Axiumine/axmem.git")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "is not on github.com" in _out(done)
        assert world.events() == []


class TestALocalBranchThatIsNotThePushedTip:
    def test_a_branch_that_is_behind_the_remote_is_refused_before_anything_runs(
        self, world: World,
    ) -> None:
        world.advance_origin()
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "BEHIND" in _out(done)
        assert _later_steps(world) == []
        assert world.dispatches() == []

    def test_a_branch_that_has_diverged_is_refused_before_anything_runs(
        self, world: World,
    ) -> None:
        world.advance_origin()
        _commit_locally(world)
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "DIVERGED" in _out(done)
        assert _later_steps(world) == []
        assert world.dispatches() == []

    def test_a_branch_with_a_commit_ci_never_saw_is_refused(self, world: World) -> None:
        _commit_locally(world)
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "CI has not seen" in _out(done)
        assert _later_steps(world) == []
        assert world.dispatches() == []


class TestCiHasToBeGreenOnTheCommitThatIsReleased:
    @pytest.mark.parametrize(
        ("states", "word"),
        [(["none"], "no CI runs recorded"), (["red"], "CI: failure"), (["pending"], "still in_progress")],
        ids=["unseen", "red", "unfinished"],
    )
    def test_a_commit_without_a_green_ci_is_not_released(
        self, world: World, states: list[str], word: str,
    ) -> None:
        base = world.head()
        world.configure(ci_base=states)
        done = world.release(VERSION)
        assert done.returncode != 0
        assert word in _out(done)
        assert "CI is not green for" in _out(done)
        assert _later_steps(world) == []
        assert world.head() == base
        assert world.origin_git("tag", "--list") == ""
        assert world.dispatches() == []
