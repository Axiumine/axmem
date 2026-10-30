# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The gates between the release commit and the dispatch of ``publish.yml``, and the resumption of a stopped release.

``test_release_pipeline.py`` pins the flow that goes green and the push that is refused. These cases pin what stops the
dispatch after the push: a red or an unfinished CI run, a signing run that is red, late or missing its bundles, the
files of the GitHub release that are not the manifest's bytes, and the integrity preflight over the versions PyPI
already holds. Each case asserts how many times the workflow was dispatched, and the ones that stop a release run it
again to prove that the script resumes at the first unfinished step with no new commit and no new tag.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.release_support.world import PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
RESUME = f"release-pypi.sh {VERSION}"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _recorded(world: World) -> dict[str, str]:
    return {entry["name"]: entry["sha256"] for entry in world.manifest()["artifacts"]}


class TestNothingIsDispatchedBeforeCiIsGreenOnTheReleaseCommit:
    def test_a_red_run_on_the_release_commit_stops_the_release(self, world: World) -> None:
        world.configure(ci_release=["pending", "red"])
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "STOPPED: CI is not green on the release commit" in _out(done)
        assert world.origin_git("rev-parse", "main") == world.head(), "the release commit was pushed, the stop came after it"
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == ""

    def test_a_ci_that_never_finishes_stops_at_the_deadline_with_the_resume_command(
        self, world: World,
    ) -> None:
        world.configure(ci_release=["pending"])
        done = world.release(VERSION, RELEASE_WAIT_TIMEOUT_S="1", RELEASE_POLL_INTERVAL_S="0.2")
        assert done.returncode != 0
        assert world.dispatches() == []
        assert RESUME in _out(done)
        assert "Nothing was uploaded to PyPI." in _out(done)
        assert world.origin_git("tag", "--list") == ""

    def test_a_rerun_after_the_red_run_is_fixed_resumes_without_a_new_commit(
        self, world: World,
    ) -> None:
        world.configure(ci_release=["red", "green"])
        first = world.release(VERSION)
        assert first.returncode != 0
        assert "CI is not green on the release commit" in _out(first)
        commits = world.commit_count()
        assert world.release(VERSION).returncode == 0
        assert world.commit_count() == commits
        assert world.dispatches() == [DISPATCH]


class TestNothingIsDispatchedBeforeTheSigningRunIsGreen:
    def test_a_red_release_run_stops_after_the_tag_is_pushed(self, world: World) -> None:
        world.configure(release_runs=["pending", "red"])
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "release.yml concluded failure" in _out(done)
        assert f"STOPPED: the release of {TAG} is not signed" in _out(done)
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == TAG

    def test_a_rerun_waits_for_the_signing_run_and_never_moves_the_tag(self, world: World) -> None:
        world.configure(release_runs=["red", "green"])
        first = world.release(VERSION)
        assert first.returncode != 0
        assert "release.yml concluded failure" in _out(first)
        tag_commit = world.origin_git("rev-parse", TAG)
        assert world.release(VERSION).returncode == 0
        assert world.origin_git("rev-parse", TAG) == tag_commit
        assert world.origin_git("tag", "--list") == TAG
        assert world.dispatches() == [DISPATCH]

    def test_a_signing_run_that_never_finishes_stops_at_the_deadline(self, world: World) -> None:
        world.configure(release_runs=["pending"])
        done = world.release(VERSION, RELEASE_WAIT_TIMEOUT_S="2", RELEASE_POLL_INTERVAL_S="0.2")
        assert done.returncode != 0
        assert world.dispatches() == []
        assert RESUME in _out(done)

    def test_a_release_without_a_bundle_for_every_artefact_is_not_dispatched(
        self, world: World,
    ) -> None:
        world.configure(assets="missing_bundle")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert ".sigstore.json" in _out(done)

    def test_a_missing_github_release_is_not_dispatched(self, world: World) -> None:
        world.configure(assets="no_release")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert f"tag {TAG} has no GitHub release" in _out(done)
        assert world.dispatches() == []


class TestNothingIsDispatchedUnlessTheSignedFilesAreTheManifest:
    def test_files_of_the_github_release_with_other_bytes_are_refused(self, world: World) -> None:
        world.configure(release_bytes="other")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert "DIFFERS" in _out(done) or "not the manifest" in _out(done)
        assert world.pypi() == {}

    def test_a_rerun_with_the_right_files_dispatches_once_and_makes_no_new_commit(
        self, world: World,
    ) -> None:
        world.configure(release_bytes="other")
        first = world.release(VERSION)
        assert first.returncode != 0
        assert "not the manifest's bytes" in _out(first) or "DIFFERS" in _out(first)
        commits = world.commit_count()
        world.configure(release_bytes="")
        assert world.release(VERSION).returncode == 0
        assert world.commit_count() == commits
        assert world.dispatches() == [DISPATCH]


class TestThePublicationIsResumable:
    def test_a_failed_run_is_dispatched_again_with_no_new_commit_or_tag(
        self, world: World,
    ) -> None:
        world.configure(publish_runs=[["in_progress", "failure"], ["success"]])
        first = world.release(VERSION)
        assert first.returncode != 0
        assert world.dispatches() == [DISPATCH]
        assert world.pypi() == {}
        assert RESUME in _out(first)
        commits, tag = world.commit_count(), world.origin_git("rev-parse", TAG)
        assert world.release(VERSION).returncode == 0
        assert world.dispatches() == [DISPATCH, DISPATCH]
        assert world.commit_count() == commits
        assert world.origin_git("rev-parse", TAG) == tag
        assert world.pypi() == _recorded(world)

    def test_a_rerun_after_the_release_is_done_dispatches_nothing_more(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0
        commits = world.commit_count()
        again = world.release(VERSION)
        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]
        assert world.commit_count() == commits
        assert "Release complete" in _out(again)
        assert "nothing to dispatch" in _out(again)

    def test_a_version_on_pypi_with_other_bytes_and_no_release_commit_is_spent(
        self, world: World,
    ) -> None:
        (world.state / "pypi" / f"{PACKAGE}-{VERSION}.tar.gz").write_bytes(b"someone else's bytes\n")
        base = world.head()
        done = world.release(VERSION)
        assert done.returncode != 0
        assert world.dispatches() == []
        assert world.head() == base
        assert "spent" in _out(done)


class TestTheIntegrityPreflight:
    def test_an_unsigned_version_on_pypi_that_nobody_listed_blocks_the_release(
        self, world: World,
    ) -> None:
        world.add_old_release("0.1.0", signed=True)
        world.add_old_release("0.1.5", signed=False)
        base = world.head()
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "0.1.5" in _out(done)
        assert world.dispatches() == []
        assert world.head() == base

    def test_the_same_version_listed_as_superseded_with_its_digests_lets_it_through(
        self, world: World,
    ) -> None:
        world.add_old_release("0.1.0", signed=True)
        names = world.add_old_release("0.1.5", signed=False)
        entry: dict[str, Any] = {
            "version": "0.1.5",
            "files": [
                {"name": name, "sha256": digest}
                for name, digest in sorted(world.pypi().items())
                if "0.1.5" in name
            ],
            "published": "2026-10-01",
            "reason": "an upload made before the pipeline signed releases",
            "superseded_by": "0.2.0",
        }
        assert {file["name"] for file in entry["files"]} == set(names)
        (world.root / "release-superseded.json").write_text(
            json.dumps([entry], indent=2) + "\n", encoding="utf-8",
        )
        world.git("add", "release-superseded.json")
        world.git("commit", "-q", "-m", "list 0.1.5 as superseded")
        world.git("push", "-q", world.remote, "HEAD:refs/heads/main")
        world.configure()
        (world.state / "base.sha").write_text(world.head(), encoding="utf-8")
        done = world.release(VERSION)
        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]

    def test_a_version_that_reaches_pypi_unsigned_during_the_release_is_seen_before_the_dispatch(
        self, world: World,
    ) -> None:
        names = world.add_old_release("0.1.5", signed=False)
        world.configure(appears_on_read=dict.fromkeys(names, 2))  # absent at the start, listed at the read before the dispatch
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "the release integrity check failed" in _out(done)
        assert "0.1.5" in _out(done)
        assert world.dispatches() == []
        assert world.origin_git("tag", "--list") == TAG, "the release got as far as the signing run"

    def test_a_local_edit_of_the_superseded_list_does_not_excuse_a_version(self, world: World) -> None:
        world.mark("refuse-push-main")
        assert world.release(VERSION).returncode != 0  # the release commit exists and is not pushed
        world.mark("refuse-push-main", on=False)
        for name in (f"{PACKAGE}-0.1.5.tar.gz", f"{PACKAGE}-0.1.5-py3-none-any.whl"):
            (world.state / "pypi" / name).write_bytes(f"old {name}\n".encode())
        entry: dict[str, Any] = {
            "version": "0.1.5",
            "files": [{"name": name, "sha256": digest} for name, digest in sorted(world.pypi().items())],
            "published": "2026-10-01",
            "reason": "an upload made before the pipeline signed releases",
            "superseded_by": VERSION,
        }
        (world.root / "release-superseded.json").write_text(json.dumps([entry]) + "\n", encoding="utf-8")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "the release integrity check failed" in _out(done), "the list that counts is the pushed one"
        assert "0.1.5" in _out(done)
        assert world.dispatches() == []


class TestAFailureBetweenTheStampAndTheCommitLeavesNothingBehind:
    def test_a_package_gate_that_fails_after_the_stamp_puts_the_tree_back_and_a_rerun_goes_through(
        self, world: World,
    ) -> None:
        base = world.head()
        world.mark("refuse-package-gate")
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "Package check failed" in _out(done)
        assert world.git("status", "--porcelain", "--untracked-files=no") == ""
        assert "[Unreleased]" in (world.root / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"[{TAG}]" not in (world.root / "CHANGELOG.md").read_text(encoding="utf-8")
        assert world.head() == base
        world.mark("refuse-package-gate", on=False)
        rerun = world.release(VERSION)
        assert rerun.returncode == 0, _out(rerun)
        assert world.dispatches() == [DISPATCH]

    def test_a_build_that_is_not_the_pinned_version_puts_the_changelog_back(self, world: World) -> None:
        world.configure(build_version="9.9.9")  # the build does not honour the pin: the script stops before the manifest
        done = world.release(VERSION)
        assert done.returncode != 0
        assert "the build did not produce" in _out(done)
        assert world.git("status", "--porcelain", "--untracked-files=no") == ""
        assert not (world.root / "release-digests.json").exists()
