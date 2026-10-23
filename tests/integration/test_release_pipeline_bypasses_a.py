"""The ways past the release guard that go through the signing evidence, each closed and proved on the real script.

A red team tried to reach the dispatch of ``publish.yml`` with one of the five conditions of the invariant false. These
are the cases of the signing run and its bundles: the tag's red ``release.yml`` run hidden by another green run of the
same commit, and bundles that exist by name only or sign other bytes. Every case runs the whole script in the scratch
world and asserts the number of dispatches (it must stay zero), so a fix that stops working shows here and not on PyPI.
``test_release_pipeline_bypasses_b.py`` holds what PyPI serves, ``_c`` the tree, the version and the environment, ``_d``
the guard asked again at the last moment, ``_e`` what is dispatched and read.
"""

import shutil
from pathlib import Path

import pytest

from tests.release_support.world import World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
FAILURE = {
    "status": "completed",
    "conclusion": "failure",
    "createdAt": "2026-01-01T10:00:00Z",
}
SUCCESS = {
    "status": "completed",
    "conclusion": "success",
    "createdAt": "2026-01-01T10:05:00Z",
}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


class TestTheTagsOwnRunDecides:
    """The newest run of the commit decided, so another tag's green run hid the tag's red one."""

    def test_a_green_run_of_another_tag_on_the_same_commit_does_not_hide_a_red_one(
        self, world: World,
    ) -> None:
        world.configure(
            release_raw=[
                {**FAILURE, "headBranch": "@tag", "url": "https://github.example/run/own"},
                {
                    **SUCCESS,
                    "headBranch": "v0.2.0-hotfix",
                    "url": "https://github.example/run/other",
                },
            ],
        )

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert "release.yml concluded failure — https://github.example/run/own" in _out(
            done,
        )

    @pytest.mark.parametrize(
        "row",
        [
            {"event": "workflow_dispatch", "headBranch": "main"},
            {"event": "release", "headBranch": "@tag"},
            {"headBranch": "@tag", "headSha": "0" * 40},
        ],
        ids=["a dispatch", "a release event", "another commit"],
    )
    def test_a_green_run_the_tags_push_did_not_start_is_no_signing_run(
        self, world: World, row: dict[str, str],
    ) -> None:
        world.configure(release_raw=[{**SUCCESS, **row}])

        done = world.release(
            VERSION, RELEASE_WAIT_TIMEOUT_S="2", RELEASE_POLL_INTERVAL_S="0.2",
        )

        assert done.returncode != 0
        assert "STOPPED: the signing run did not finish before the deadline" in _out(done)
        assert world.dispatches() == []
        assert "release-pypi.sh 0.2.0" in _out(done)

    def test_an_unfinished_run_of_the_tag_holds_the_release_back_beside_a_green_one(
        self, world: World,
    ) -> None:
        pending = {
            "status": "in_progress",
            "conclusion": "",
            "createdAt": "2026-01-01T09:00:00Z",
        }
        world.configure(
            release_raw=[
                {**pending, "headBranch": "@tag"},
                {**SUCCESS, "headBranch": "@tag"},
            ],
        )

        done = world.release(
            VERSION, RELEASE_WAIT_TIMEOUT_S="2", RELEASE_POLL_INTERVAL_S="0.2",
        )

        assert done.returncode != 0
        assert world.dispatches() == []


class TestASignatureIsAWholeBundleOfTheArtefact:
    """A bundle was counted by name alone, so an empty, unfinished or foreign file was a signature."""

    @pytest.mark.parametrize(
        ("mode", "word"),
        [
            ("empty_bundle", "unfinished (state uploaded, 0 bytes)"),
            ("starter_bundle", "unfinished (state starter, 0 bytes)"),
            ("draft", "is still a draft"),
            ("foreign_bundle", "signed sha256"),
            ("garbage_bundle", "is not a Sigstore bundle"),
            ("other_attached", "on the release has sha256"),
        ],
    )
    def test_a_release_whose_bundle_or_artefact_is_not_the_signed_bytes_is_not_dispatched(
        self, world: World, mode: str, word: str,
    ) -> None:
        world.configure(assets=mode)

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert word in _out(done)
