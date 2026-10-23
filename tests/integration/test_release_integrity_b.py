"""The release integrity check proves that what PyPI serves is what was signed, not only that a tag and a run exist.

A tag, a green run and bundle file names alone proved nothing about PyPI's bytes. For every version the check also
requires: the sha256 PyPI serves for each file to be the one in the manifest committed at the tag (and the manifest to
name that version and tag and list exactly those files), the tagged commit to be on the remote's main and held by
this clone (it never fetches), each bundle to be whole and to sign that file's sha256, and the run to be the tag's own,
not another tag's on the same commit.
"""

import json
import subprocess

import pytest

from scripts.release import integrity
from tests.integration.release_integrity_world import PKG, Proof, World, digest, names
from tests.release_support import fakes


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


class TestWhatPyPIServesIsWhatWasSigned:
    def test_bytes_on_pypi_that_are_not_the_signed_manifests_are_refused(self, world, capsys):
        world.version("0.3.9")
        wheel = names("0.3.9")[0]
        world.page_files[wheel] = "c" * 64

        assert world.run() == 1

        out = capsys.readouterr().out
        assert f"REFUSED  {PKG} 0.3.9 on PyPI" in out
        assert (
            f"{wheel}: PyPI serves {'c' * 64}, the signed manifest at v0.3.9 says {digest(wheel)}"
            in out
        )

    @pytest.mark.parametrize(
        ("kind", "word"),
        [
            ("none", "release-digests.json cannot be read at v0.3.9"),
            ("unreadable", "release-digests.json at v0.3.9 cannot be read"),
            ("other-version", "says version '9.9.9', not '0.3.9'"),
            ("other-tag", "says tag 'v9.9.9', not 'v0.3.9'"),
            ("other-digest", "the signed manifest at v0.3.9 says " + "f" * 64),
            ("one-file", "the signed manifest at v0.3.9 says no such file"),
            (
                "extra-file",
                "PyPI serves no such file, the signed manifest at v0.3.9 says " + "a" * 64,
            ),
        ],
    )
    def test_a_manifest_at_the_tag_that_is_not_this_release_is_refused(
        self, world, capsys, kind, word,
    ):
        world.version("0.3.9", Proof(manifest=kind))

        assert world.run() == 1

        assert word in capsys.readouterr().out

    def test_a_tag_whose_commit_is_not_on_the_remotes_main_is_refused(self, world, capsys):
        world.version("0.3.9", Proof(on_main=False))

        assert world.run() == 1

        out = capsys.readouterr().out
        assert "tag v0.3.9 points at" in out
        assert "which is not on origin/main" in out

    def test_a_tag_whose_commit_this_clone_does_not_hold_is_refused_not_fetched(
        self, world, capsys,
    ):
        world.version("0.3.9")
        argv = world.args()
        elsewhere = world.base / "elsewhere"
        elsewhere.mkdir()
        subprocess.run(  # noqa: S603
            ["git", "init", "-q", "-b", "main", str(elsewhere)],  # noqa: S607
            env=world.env,
            check=True,
        )
        subprocess.run(  # noqa: S603
            ["git", "-C", str(elsewhere), "remote", "add", "origin", str(world.origin)],  # noqa: S607
            env=world.env,
            check=True,
        )
        argv[argv.index("--root") + 1] = str(elsewhere)

        assert integrity.main(argv) == 1

        out = capsys.readouterr().out
        assert "which is not on origin/main" in out
        assert "release-digests.json cannot be read at v0.3.9" in out

    def test_a_bundle_that_signed_other_bytes_is_refused(self, world, capsys):
        world.version("0.3.9")
        wheel = names("0.3.9")[0]
        argv = world.args()
        world.patch_scenario(bundle_digests={wheel: "d" * 64})

        assert integrity.main(argv) == 1

        assert (
            f"{wheel}.sigstore.json signed sha256 {'d' * 64}, not the {wheel} digest"
            in capsys.readouterr().out
        )

    def test_a_bundle_that_is_empty_is_refused_without_a_download(self, world, capsys):
        world.version("0.3.9")
        wheel = names("0.3.9")[0]
        argv = world.args()
        assets = json.loads((world.state / "gh.json").read_text(encoding="utf-8"))["releases"]
        assets["v0.3.9"][assets["v0.3.9"].index(f"{wheel}.sigstore.json")] = {
            "name": f"{wheel}.sigstore.json",
            "size": 0,
            "state": "starter",
        }
        world.patch_scenario(releases=assets)

        assert integrity.main(argv) == 1

        out = capsys.readouterr().out
        assert f"{wheel}.sigstore.json on the release is unfinished (state starter, 0 bytes)" in out
        assert not [call for call in world.gh_calls() if "download" in call]

    def test_a_run_of_another_tag_on_the_same_commit_does_not_stand_in_for_the_tags_own(
        self, world, capsys,
    ):
        world.version("0.3.9", Proof(run="failure"))
        sha = world.commits["0.3.9"]
        argv = world.args()
        later_green = {
            **fakes.run("Release", created="2026-02-01T00:00:00Z"),
            "headBranch": "v0.3.9-hotfix",
        }
        runs = {sha: [[world.runs[sha][0][0], later_green]]}
        world.patch_scenario(release_runs=runs)

        assert integrity.main(argv) == 1

        assert "release.yml concluded failure" in capsys.readouterr().out
