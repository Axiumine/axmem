# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The release guard, conditions 1 to 3: the release commit, green CI on it, and the tag on the remote.

A scratch repository with a bare remote, a digest manifest, an annotated tag and a ``dist/`` is judged by the real guard,
running the real ``scripts.release.ci_green`` and ``scripts.release.digests`` as children, with a scripted ``gh``. Each of
these conditions is broken on its own to show that the guard names what is missing and exits 1. The remote's main is read
by its fully qualified tracking ref, so a local tag or a hand-moved ref never stands in for it; a child that refuses
without a word is still a refusal; and the remote can have any name (Axmem's is ``axiumine``).
"""

import subprocess

import pytest

from scripts.release import guard
from tests.integration.release_guard_world import TAG, World
from tests.release_support import fakes

CONDITION_ONE = "[1] the release commit is on the remote's main and its manifest is the one on disk: FAIL"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


class TestCommitAndCi:
    def test_all_five_conditions_hold(self, world, capsys):
        assert world.check() == 0

        out = capsys.readouterr().out
        assert out.count(": ok") == 5
        assert "release guard: all 5 conditions hold" in out

    def test_it_asks_for_the_branch_runs_of_the_exact_commit(self, world):
        world.check()

        asked = [line for line in world.events() if line.startswith("gh ci-runs")]
        assert asked == [f"gh ci-runs {world.sha[:8]} branch=main"]

    def test_a_commit_that_the_remote_does_not_have_fails_condition_one(self, world, capsys):
        unpushed = world.unpushed()

        assert world.check(unpushed) == 1

        out = capsys.readouterr().out
        assert f"{unpushed[:8]} is not on origin/main — push it" in out
        assert CONDITION_ONE in out

    def test_a_file_that_is_not_a_manifest_is_exit_two(self, world, capsys):
        (world.repo / "release-digests.json").write_text("{}\n", encoding="utf-8")

        assert world.check() == 2

        assert "cannot read the release manifest" in capsys.readouterr().err

    def test_a_manifest_edited_after_the_commit_fails_condition_one(self, world, capsys):
        world.write_manifest(tag="v9.0.0")

        assert world.check() == 1

        assert "release-digests.json on disk is not the one committed at" in capsys.readouterr().out

    def test_a_commit_without_a_manifest_fails_condition_one(self, world, capsys):
        world.git("rm", "-q", "release-digests.json")
        world.git("commit", "-m", "drop the manifest")
        bare = world.git("rev-parse", "HEAD")
        world.git("push", "origin", "main")
        world.write_manifest(tag=TAG)

        assert world.check(bare) == 1

        assert f"release-digests.json does not exist at {bare[:8]}" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("runs", "word"),
        [
            (fakes.RED, "Lint: failure"),
            (fakes.PENDING, "Lint: still in_progress"),
            ([], "no CI runs recorded"),
        ],
    )
    def test_ci_that_is_not_green_fails_condition_two(self, world, capsys, runs, word):
        world.script(ci_runs={"*": [runs]})

        assert world.check() == 1

        assert word in capsys.readouterr().out

    def test_a_tag_missing_on_the_remote_fails_condition_three(self, world, capsys):
        world.origin_git("tag", "-d", TAG)

        assert world.check() == 1

        assert f"tag {TAG} is not on origin" in capsys.readouterr().out

    def test_a_tag_on_another_commit_fails_condition_three(self, world, capsys):
        (world.repo / "other.txt").write_text("x\n", encoding="utf-8")
        world.git("add", "-A")
        world.git("commit", "-m", "later")
        world.git("tag", "-f", "-a", TAG, "-m", "moved", "HEAD")
        world.git("push", "-f", "origin", "main", TAG)

        assert world.check() == 1

        assert f"tag {TAG} points at" in capsys.readouterr().out


class TestConditionOneUsesTheRemoteTrackingRefInFull:
    def test_a_local_tag_named_like_the_remote_branch_does_not_stand_in_for_it(self, world, capsys):
        unpushed = world.unpushed()
        world.git("tag", "origin/main", unpushed)

        assert world.check(unpushed) == 1

        assert f"{unpushed[:8]} is not on origin/main — push it" in capsys.readouterr().out

    def test_a_tracking_ref_moved_by_hand_is_not_believed(self, world, capsys):
        unpushed = world.unpushed()
        world.git("update-ref", "refs/remotes/origin/main", unpushed)

        assert world.check(unpushed) == 1

        assert f"{unpushed[:8]} is not on origin/main — push it" in capsys.readouterr().out


class TestTheRemoteHasAnyName:
    """Axmem's remote is ``axiumine``: every condition and message follows ``--remote``, which defaults to ``origin``."""

    @pytest.fixture
    def world(self, tmp_path, monkeypatch):
        return World(tmp_path, monkeypatch, remote="axiumine")

    def test_all_five_conditions_hold_on_a_remote_that_is_not_called_origin(self, world, capsys):
        assert world.check() == 0

        assert "all 5 conditions hold" in capsys.readouterr().out

    def test_the_messages_name_that_remote(self, world, capsys):
        unpushed = world.unpushed()
        world.origin_git("tag", "-d", TAG)

        assert world.check(unpushed) == 1

        out = capsys.readouterr().out
        assert f"{unpushed[:8]} is not on axiumine/main — push it" in out
        assert f"tag {TAG} is not on axiumine" in out

    def test_without_the_flag_the_guard_asks_origin_which_this_clone_does_not_have(
        self, world, capsys,
    ):
        argv = [word for word in world.check_argv() if word not in ("--remote", "axiumine")]

        assert guard.main(argv) == 2

        assert "ERROR:" in capsys.readouterr().err


class TestAChildThatRefusesWithoutAWordIsStillARefusal:
    """A check script that exits 1 and prints nothing must not read as a pass."""

    @pytest.mark.parametrize("which", [(2, "ci_green"), (5, "digests")])
    def test_exit_one_with_no_message_fails_the_condition(self, world, monkeypatch, capsys, which):
        number, module = which
        name = f"scripts.release.{module}"
        real = guard.run_text

        def silent(argv, **kwargs):
            if name in argv:
                return subprocess.CompletedProcess(argv, 1, "", "")
            return real(argv, **kwargs)

        monkeypatch.setattr(guard, "run_text", silent)

        assert world.check() == 1

        out = capsys.readouterr().out
        assert f"{name} exited 1 without a reason" in out
        assert out.count(": FAIL") == 1
        assert f"[{number}]" in next(line[:3] for line in out.splitlines() if ": FAIL" in line)
