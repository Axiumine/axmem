# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The real release script builds like the real uv, and judges the signed files, not the build, before the dispatch.

uv writes ``dist/.gitignore`` (one byte, ``*``) on every ``uv build``. The release builds once, to record the digests, and
what PyPI is given is never that build: ``rf_fetch_signed`` downloads the wheel and the sdist from the GitHub release into
a directory of their own, ``digests verify`` compares them with the manifest, and the release guard checks that directory
(``--dist``), which must hold the manifest's bytes and nothing else. A fake ``uv build`` that wrote no marker would let every
case run in a world that differs from the real tool in exactly that place, so the fake writes it on every build. The cases
here prove the two halves of that on the real script: whatever the build leaves in ``dist/`` reaches no one, and anything
in the downloaded directory that is not the manifest's bytes stops the release before the workflow is dispatched.
"""

import shutil
from pathlib import Path

import pytest

from tests.release_support.world import PACKAGE, World

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

VERSION = "0.2.0"
TAG = f"v{VERSION}"
WHEEL = f"{PACKAGE}-{VERSION}-py3-none-any.whl"
SDIST = f"{PACKAGE}-{VERSION}.tar.gz"
ARTEFACTS = [WHEEL, SDIST]
DISPATCH = f"gh workflow-run publish.yml tag={TAG}"
FILES_URL = "curl https://files.pythonhosted.org/packages/"
GUARD_FAIL = "[5] the files to publish are byte-identical to the manifest and dist holds nothing else: FAIL"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _out(done: object) -> str:
    return f"{getattr(done, 'stdout', '')}\n{getattr(done, 'stderr', '')}"


def _recorded(world: World) -> dict[str, str]:
    return {entry["name"]: entry["sha256"] for entry in world.manifest()["artifacts"]}


class TestTheFakeBuildsLikeTheRealUv:
    def test_the_build_leaves_the_one_byte_gitignore_in_dist(self, world: World) -> None:
        assert world.release(VERSION).returncode == 0

        marker = world.root / "dist" / ".gitignore"
        assert marker.read_bytes() == b"*"
        assert marker.is_file()
        assert not marker.is_symlink()

    def test_the_manifest_names_the_wheel_and_the_sdist_and_not_the_marker(
        self, world: World,
    ) -> None:
        assert world.release(VERSION).returncode == 0

        assert set(_recorded(world)) == set(ARTEFACTS)

    def test_an_older_uv_that_writes_no_marker_reaches_the_dispatch_too(self, world: World) -> None:
        world.configure(build_marker={"kind": "absent"})

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert not (world.root / "dist" / ".gitignore").exists()


class TestAFullReleaseIsJudgedOnTheDownloadedSignedFiles:
    def test_exactly_one_dispatch_and_pypi_holds_the_manifests_files_and_nothing_else(
        self, world: World,
    ) -> None:
        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert world.pypi() == _recorded(world)
        assert ".gitignore" not in world.pypi()

    def test_the_guard_reports_all_five_conditions_after_the_signed_files_are_downloaded(
        self, world: World,
    ) -> None:
        done = world.release(VERSION)

        assert "[5] the files to publish are byte-identical to the manifest and dist holds nothing else: ok" in _out(
            done,
        )
        assert "release guard: all 5 conditions hold" in _out(done)
        events = world.events()
        assert events.index("gh release-download v0.2.0") < events.index(DISPATCH)

    def test_a_resumed_release_does_not_build_again_and_dispatches_once(self, world: World) -> None:
        world.configure(ci_release=["red", "green"])
        assert world.release(VERSION).returncode != 0
        assert world.dispatches() == []

        again = world.release(VERSION)

        assert again.returncode == 0, _out(again)
        assert world.dispatches() == [DISPATCH]
        assert len([line for line in world.events() if line.startswith("build ")]) == 1


#: what a build leaves in dist/ besides the wheel and the sdist: none of it is published, because dist/ is not what the
#: workflow is given
BUILD_LEFTOVERS = {
    "a gitignore with other bytes": {"build_marker": {"kind": "file", "bytes": "*\n"}},
    "an empty gitignore": {"build_marker": {"kind": "file", "bytes": ""}},
    "a gitignore directory": {"build_marker": {"kind": "dir"}},
    "a gitignore symlink": {"build_marker": {"kind": "symlink"}},
    "a dotfile and a zip": {"build_extra": [".DS_Store", "stray-0.2.0.zip"]},
}


class TestWhatTheBuildLeavesInDistReachesNoOne:
    @pytest.mark.parametrize("config", BUILD_LEFTOVERS.values(), ids=list(BUILD_LEFTOVERS))
    def test_the_release_completes_and_pypi_holds_the_manifests_files_only(
        self, world: World, config: dict[str, object],
    ) -> None:
        world.configure(**config)

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert world.pypi() == _recorded(world)
        assert set(world.pypi()) == set(ARTEFACTS)


#: what the download of the signed files can hold besides the manifest's bytes, the entry the check must name, and the
#: check that names it: ``digests verify`` (a wheel or sdist that is not the manifest's) or the release guard (anything else)
STRAYS = {
    "an extra wheel": (
        {"release_extra": ["other-0.2.0-py3-none-any.whl"]},
        "other-0.2.0-py3-none-any.whl",
        "EXTRA",
    ),
    "a dotfile": ({"release_extra": [".DS_Store"]}, ".DS_Store", "is not an artefact of the manifest"),
    "a gitignore with other bytes": (
        {"release_extra": [".gitignore"]},
        ".gitignore",
        "is not an artefact of the manifest and is not the 1-byte file uv writes",
    ),
    "a zip": ({"release_extra": ["stray-0.2.0.zip"]}, "stray-0.2.0.zip", "is not an artefact of the manifest"),
}
#: the scenario keys of STRAYS and of the tampered bytes, put back to what a release does
CLEAN = {"release_extra": [], "release_bytes": ""}


class TestWhatIsNotTheManifestsBytesStopsBeforeTheDispatch:
    @pytest.mark.parametrize(("config", "named", "check"), STRAYS.values(), ids=list(STRAYS))
    def test_no_dispatch_and_the_entry_is_named(
        self, world: World, config: dict[str, object], named: str, check: str,
    ) -> None:
        world.configure(**config)

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert world.pypi() == {}
        assert named in _out(done)
        assert check in _out(done)

    def test_a_file_that_digests_verify_lets_through_is_stopped_by_the_guard(
        self, world: World,
    ) -> None:
        world.configure(release_extra=["stray-0.2.0.zip"])

        done = world.release(VERSION)

        assert GUARD_FAIL in _out(done)
        assert "signed/stray-0.2.0.zip is not an artefact of the manifest" in _out(done)
        assert "STOPPED: the release guard refused the publication" in _out(done)

    @pytest.mark.parametrize(("config", "named", "check"), STRAYS.values(), ids=list(STRAYS))
    def test_a_rerun_without_the_entry_continues_and_dispatches_once(
        self, world: World, config: dict[str, object], named: str, check: str,
    ) -> None:
        world.configure(**config)
        assert world.release(VERSION).returncode != 0
        commits, pushed = world.commit_count(), world.origin_git("rev-parse", "main")
        world.configure(**CLEAN)

        done = world.release(VERSION)

        assert done.returncode == 0, _out(done)
        assert world.dispatches() == [DISPATCH]
        assert world.commit_count() == commits
        assert world.origin_git("rev-parse", "main") == pushed

    def test_every_entry_is_named_when_there_are_several(self, world: World) -> None:
        world.configure(release_extra=[".DS_Store", "stray-0.2.0.zip", ".gitignore"])

        done = world.release(VERSION)

        assert world.dispatches() == []
        for named in (".DS_Store", "stray-0.2.0.zip", ".gitignore"):
            assert f"signed/{named} is not an artefact of the manifest" in _out(done)

    def test_an_artefact_with_other_bytes_stops_the_release(self, world: World) -> None:
        world.configure(release_bytes="other")

        done = world.release(VERSION)

        assert done.returncode != 0
        assert world.dispatches() == []
        assert world.pypi() == {}
        assert "DIFFERS" in _out(done)
        assert f"the files of the GitHub release {TAG} are not the manifest's bytes" in _out(done)

    def test_the_bytes_that_stopped_the_release_are_never_read_from_pypi_either(
        self, world: World,
    ) -> None:
        world.configure(release_bytes="other")

        world.release(VERSION)

        assert not [line for line in world.events() if line.startswith(FILES_URL)]
