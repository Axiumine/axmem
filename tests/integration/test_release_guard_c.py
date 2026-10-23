"""The release guard waits for the signing run, and condition 4 is the tag's own run and a whole signed bundle.

``wait-release`` polls the ``release.yml`` run that the push of the tag started, then requires the bundles; it ends at once
on a red run, at a deadline with exit 3, and after a grace period with no run at all. Condition 4 must not be satisfied by
the run of another tag, a dispatch or a release event on the same commit, nor by a bundle that exists by name only, a draft
release, a bundle that signed other bytes or a file that is not a bundle. The waits take finite, non-negative seconds.
"""

import time

import pytest

from scripts.release import guard
from tests.integration.release_guard_world import NAMES, TAG, World
from tests.release_support import fakes


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


class TestWaitRelease:
    def test_it_waits_for_a_pending_run_and_then_requires_the_bundles(self, world, capsys):
        pending = [fakes.run("Release", status="in_progress", conclusion="")]
        world.script(release_runs={"*": [pending, pending, [fakes.run("Release")]]})

        assert world.wait() == 0

        out = capsys.readouterr().out
        assert out.count("is in_progress") == 1
        assert "release.yml succeeded" in out

    def test_a_red_run_is_exit_one_at_once(self, world, capsys):
        world.script(release_runs={"*": [[fakes.run("Release", conclusion="failure")]]})

        assert world.wait() == 1

        assert "concluded failure" in capsys.readouterr().err

    def test_a_missing_bundle_after_a_green_run_is_exit_one(self, world, capsys):
        world.script(releases={TAG: fakes.signed_assets(NAMES, drop=NAMES[0])})

        assert world.wait() == 1

        assert f"ERROR: the release has no {NAMES[0]}.sigstore.json" in capsys.readouterr().err

    def test_the_deadline_is_exit_three(self, world, capsys):
        world.script(release_runs={"*": [[fakes.run("Release", status="queued", conclusion="")]]})

        assert world.wait(deadline=time.time() - 1) == 3

        assert "did not finish before the deadline" in capsys.readouterr().err

    def test_no_run_at_all_is_waited_for_and_then_an_error(self, world, monkeypatch, capsys):
        world.script(release_runs={"*": [[], [], [fakes.run("Release")]]})
        assert world.wait() == 0

        world.script(release_runs={"*": [[]]})
        monkeypatch.setattr(guard, "NO_RUN_GRACE_S", 0)
        capsys.readouterr()

        assert world.wait() == 1
        assert "no release.yml run of the tag push is recorded" in capsys.readouterr().err


class TestConditionFourIsTheTagsOwnRunAndAWholeSignedBundle:
    """Another run hiding the tag's red one, and a bundle by name only, must not pass condition 4."""

    OWN_RED = fakes.run("Release", conclusion="failure", created="2026-01-01T10:00:00Z")

    def other(self, **row):
        return {**fakes.run("Release", created="2026-01-01T10:05:00Z"), **row}

    @pytest.mark.parametrize(
        "row",
        [
            {"headBranch": "v0.1.0-hotfix"},
            {"event": "workflow_dispatch", "headBranch": "main"},
            {"event": "release"},
        ],
        ids=["a second tag", "a dispatch", "a release event"],
    )
    def test_a_newer_green_run_of_the_same_commit_does_not_hide_the_tags_red_one(
        self, world, capsys, row,
    ):
        world.script(release_runs={"*": [[self.OWN_RED, self.other(**row)]]})

        assert world.check() == 1

        assert "release.yml concluded failure" in capsys.readouterr().out

    def test_a_green_run_that_the_tags_push_did_not_start_is_no_run_at_all(self, world, capsys):
        world.script(
            release_runs={"*": [[self.other(event="workflow_dispatch", headBranch="main")]]},
        )

        assert world.check() == 1

        assert "no release.yml run of the tag push is recorded" in capsys.readouterr().out

    def test_the_wait_is_not_ended_by_the_run_of_another_tag(self, world, monkeypatch, capsys):
        elsewhere = self.other(headBranch="v0.1.0-hotfix")
        monkeypatch.setattr(guard, "NO_RUN_GRACE_S", 0)
        world.script(release_runs={"*": [[elsewhere]]})

        assert world.wait() == 1

        assert "no release.yml run of the tag push" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("asset", "word"),
        [
            ({"size": 0, "state": "starter"}, "unfinished (state starter, 0 bytes)"),
            ({"size": 0, "state": "uploaded"}, "unfinished (state uploaded, 0 bytes)"),
            ({"size": 12, "state": "starter"}, "unfinished (state starter, 12 bytes)"),
        ],
    )
    def test_a_bundle_that_exists_by_name_only_fails_condition_four(
        self, world, capsys, asset, word,
    ):
        names = fakes.signed_assets(NAMES)
        names[names.index(f"{NAMES[0]}.sigstore.json")] = {
            "name": f"{NAMES[0]}.sigstore.json",
            **asset,
        }
        world.script(releases={TAG: names})

        assert world.check() == 1

        assert f"{NAMES[0]}.sigstore.json on the release is {word}" in capsys.readouterr().out

    def test_a_draft_release_fails_condition_four(self, world, capsys):
        world.script(drafts=[TAG])

        assert world.check() == 1

        assert f"the GitHub release of {TAG} is still a draft" in capsys.readouterr().out

    def test_a_bundle_that_signed_other_bytes_fails_condition_four(self, world, capsys):
        world.script(bundle_digests={NAMES[1]: "e" * 64})

        assert world.check() == 1

        assert f"{NAMES[1]}.sigstore.json signed sha256 {'e' * 64}, not the {NAMES[1]} digest" in (
            capsys.readouterr().out
        )

    def test_a_foreign_file_with_the_right_name_fails_condition_four(self, world, capsys):
        world.script(bundle_text={f"{NAMES[0]}.sigstore.json": "not a bundle"})

        assert world.check() == 1

        assert f"{NAMES[0]}.sigstore.json is not a Sigstore bundle" in capsys.readouterr().out

    def test_the_bundles_are_downloaded_once_per_check(self, world):
        assert world.check() == 0

        assert world.events().count(f"gh release-download {TAG}") == 1

    def test_the_wait_also_requires_the_bundles_to_have_signed_the_manifests_digests(
        self, world, capsys,
    ):
        world.script(bundle_digests={NAMES[0]: "e" * 64})

        assert world.wait() == 1

        assert "signed sha256" in capsys.readouterr().err


class TestTheWaitsAreFiniteNumbers:
    @pytest.mark.parametrize("flag", ["--deadline", "--interval"])
    @pytest.mark.parametrize("value", ["nan", "inf", "-1", "soon"])
    def test_anything_but_a_finite_non_negative_number_is_refused(
        self, world, capsys, flag, value,
    ):
        argv = ["wait-release", "--repo", "Axiumine/axmem", "--sha", world.sha, "--tag", TAG]
        argv += ["--deadline", "1", "--interval", "1"]
        argv[argv.index(flag) + 1] = value

        with pytest.raises(SystemExit) as stop:
            guard.main(argv)

        assert stop.value.code == 2
        assert "seconds" in capsys.readouterr().err
