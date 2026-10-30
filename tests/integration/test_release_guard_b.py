# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The release guard, conditions 4 and 5, all five at once, and what is not a pass at all.

Condition 4 needs a green ``release.yml`` run and a release with a whole bundle per file; condition 5 needs ``dist/``
byte-identical to the manifest with nothing else in it (in ``publish.yml`` that directory
holds the signed files downloaded from the GitHub release, not a local rebuild). Broken together the guard lists every
unmet condition and exits 1;
a manifest it cannot read, or GitHub not answering, is exit 2 and never a pass. ``test_release_guard_dist.py`` and
``test_release_guard_links.py`` cover the entries of ``dist/`` one by one.
"""

import shutil

import pytest

from scripts.release import guard
from tests.integration.release_guard_world import NAMES, TAG, World
from tests.release_support import fakes

NOT_AN_ARTEFACT = "is not an artefact of the manifest"
CONDITION_FIVE = "[5] the files to publish are byte-identical to the manifest and dist holds nothing else: FAIL"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


class TestSigningAndDist:
    @pytest.mark.parametrize(
        ("runs", "word"),
        [
            ([fakes.run("Release", conclusion="failure")], "concluded failure"),
            ([], "no release.yml run of the tag push is recorded"),
            ([fakes.run("Release", status="queued", conclusion="")], "is queued"),
        ],
    )
    def test_a_signing_run_that_is_not_green_fails_condition_four(self, world, capsys, runs, word):
        world.script(release_runs={"*": [runs]})

        assert world.check() == 1

        assert word in capsys.readouterr().out

    def test_a_missing_bundle_fails_condition_four(self, world, capsys):
        world.script(releases={TAG: fakes.signed_assets(NAMES, drop=NAMES[1])})

        assert world.check() == 1

        assert f"the release has no {NAMES[1]}.sigstore.json" in capsys.readouterr().out

    def test_a_tag_without_a_release_fails_condition_four(self, world, capsys):
        world.script(releases={})

        assert world.check() == 1

        assert f"tag {TAG} has no GitHub release" in capsys.readouterr().out

    def test_a_dist_that_is_not_the_manifest_fails_condition_five(self, world, capsys):
        world.tamper()

        assert world.check() == 1

        out = capsys.readouterr().out
        assert CONDITION_FIVE in out
        assert f"DIFFERS  {NAMES[0]}" in out

    def test_a_dist_that_is_missing_fails_condition_five(self, world, capsys):
        for path in (world.repo / "dist").iterdir():
            path.unlink()

        assert world.check() == 1

        assert "MISSING" in capsys.readouterr().out

    def test_every_unmet_condition_is_named_at_once(self, world, capsys):
        world.script(ci_runs={"*": [fakes.RED]}, releases={})
        world.tamper()

        assert world.check() == 1

        out = capsys.readouterr().out
        assert "REFUSED, 3 of 5 conditions unmet" in out
        assert [": FAIL" in line for line in out.splitlines() if line.startswith("[")] == [
            False,
            True,
            False,
            True,
            True,
        ]

    def test_a_dist_directory_can_be_named(self, world):
        other = world.repo.parent / "elsewhere"
        other.mkdir()
        for name in NAMES:
            (other / name).write_bytes((world.repo / "dist" / name).read_bytes())

        assert world.check("", "--dist", str(other)) == 0


class TestTheSignedFilesAreWhatIsJudged:
    """``publish.yml`` runs ``check`` with ``--dist`` on the files it downloaded from the release, in a clean checkout."""

    @pytest.fixture
    def signed(self, world):
        """The directory the signed files are downloaded into, apart from the repository's own ``dist/``."""
        directory = world.repo.parent / "signed"
        directory.mkdir()
        for name in NAMES:
            (directory / name).write_bytes((world.repo / "dist" / name).read_bytes())
        return directory

    def test_the_workflows_own_check_passes(self, world):
        assert guard.main(world.check_ci_argv()) == 0

    def test_signed_files_that_match_pass_without_any_local_dist(self, world, signed):
        shutil.rmtree(world.repo / "dist")

        assert world.check("", "--dist", str(signed)) == 0

    def test_a_local_rebuild_does_not_stand_in_for_the_signed_files(self, world, signed, capsys):
        (signed / NAMES[0]).write_text("other signed bytes\n", encoding="utf-8")

        assert world.check("", "--dist", str(signed)) == 1

        out = capsys.readouterr().out
        assert CONDITION_FIVE in out
        assert f"DIFFERS  {NAMES[0]}" in out

    def test_a_signed_file_that_was_not_downloaded_is_missing(self, world, signed, capsys):
        (signed / NAMES[1]).unlink()

        assert world.check("", "--dist", str(signed)) == 1

        assert f"MISSING  {NAMES[1]}" in capsys.readouterr().out

    def test_a_file_downloaded_beside_the_signed_files_is_refused(self, world, signed, capsys):
        (signed / f"{NAMES[0]}.sigstore.json").write_text("{}\n", encoding="utf-8")

        assert world.check("", "--dist", str(signed)) == 1

        assert f"signed/{NAMES[0]}.sigstore.json {NOT_AN_ARTEFACT}" in capsys.readouterr().out


class TestWhatIsNotAPass:
    def test_github_not_answering_is_exit_two_and_never_a_pass(self, world, capsys):
        world.script(fail="HTTP 502")

        assert world.check() == 2

        assert "ERROR:" in capsys.readouterr().err

    def test_a_missing_manifest_file_is_exit_two(self, world, capsys):
        (world.repo / "release-digests.json").unlink()

        assert world.check() == 2

        assert "cannot read the release manifest" in capsys.readouterr().err

    def test_a_manifest_that_lists_nothing_is_exit_two(self, world, capsys):
        (world.repo / "release-digests.json").write_text('{"artifacts": []}\n', encoding="utf-8")

        assert world.check() == 2

        assert "it lists no artefact" in capsys.readouterr().err


class TestConditionFiveHoldsNothingButTheManifestsFiles:
    def test_a_file_in_dist_that_is_not_an_artefact_fails_condition_five(self, world, capsys):
        (world.repo / "dist" / "stray-0.1.0.zip").write_text("x\n", encoding="utf-8")

        assert world.check() == 1

        out = capsys.readouterr().out
        assert CONDITION_FIVE in out
        assert "dist/stray-0.1.0.zip is not an artefact of the manifest" in out

    def test_the_artefacts_command_prints_the_manifests_file_names_and_nothing_else(
        self, world, capsys,
    ):
        (world.repo / "dist" / "stray-0.1.0.zip").write_text("x\n", encoding="utf-8")

        assert guard.main(["artefacts", "--root", str(world.repo)]) == 0

        assert capsys.readouterr().out.splitlines() == NAMES

    def test_a_file_that_is_not_a_manifest_is_exit_two_for_the_artefacts_command(
        self, world, capsys,
    ):
        (world.repo / "release-digests.json").write_text("[]\n", encoding="utf-8")

        assert guard.main(["artefacts", "--root", str(world.repo)]) == 2

        assert "cannot read the release manifest" in capsys.readouterr().err

