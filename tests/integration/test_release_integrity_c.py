# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The release integrity check: listed (superseded) versions, the release being resumed, and what it cannot read.

A version named in ``release-superseded.json`` is skipped only when PyPI serves exactly the listed files with the listed
sha256; anything else is an error. A tag with no entry on PyPI is a release that never arrived and is ignored. For the
version named by ``--version`` alone PyPI may hold a subset of the manifest's files, each with the manifest's sha256 (a
red ``publish.yml`` run uploads one file after the other). The PyPI JSON, a file, git or GitHub that cannot be read is
exit 2.
"""

import json
from pathlib import Path

import pytest

from scripts.release import integrity
from tests.integration.release_integrity_world import PKG, Proof, World, digest, names
from tests.release_support import fakes


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


class TestSupersededVersions:
    def test_a_listed_version_with_pypis_digests_is_skipped_without_asking_github(
        self, world, capsys,
    ):
        world.version("0.4.0", Proof(tag=False, run=None, assets=None))
        world.list_version("0.4.0")

        assert world.run() == 0

        out = capsys.readouterr().out
        assert f"superseded  {PKG} 0.4.0 on PyPI" in out
        assert world.gh_calls() == []

    def test_removing_the_entry_makes_the_check_refuse(self, world, capsys):
        world.version("0.4.0", Proof(tag=False, run=None, assets=None))
        world.list_version("0.4.0")
        assert world.run() == 0

        world.superseded.clear()

        assert world.run() == 1
        assert f"REFUSED  {PKG} 0.4.0 on PyPI" in capsys.readouterr().out

    def test_a_different_digest_is_an_error_even_though_the_version_is_listed(self, world, capsys):
        world.version("0.4.0", Proof(tag=False, run=None, assets=None))
        world.list_version("0.4.0", sha={names("0.4.0")[1]: "f" * 64})

        assert world.run() == 1

        out = capsys.readouterr().out
        sdist = names("0.4.0")[1]
        assert (
            f"{sdist} on PyPI has sha256 {digest(sdist)}, but release-superseded.json lists {'f' * 64}"
            in out
        )
        assert "uploaded again or the entry is wrong" in out

    def test_a_file_pypi_serves_that_the_entry_does_not_list_is_an_error(self, world, capsys):
        world.version("0.4.0", Proof(tag=False, run=None, assets=None))
        world.list_version("0.4.0")
        world.superseded[0]["files"] = world.superseded[0]["files"][:1]

        assert world.run() == 1

        assert "which release-superseded.json does not list" in capsys.readouterr().out

    def test_a_listed_version_that_is_not_on_pypi_changes_nothing(self, world):
        world.version("0.3.9")
        world.list_version("0.4.0")

        assert world.run() == 0


class TestAProjectWithNoVersionYet:
    def test_a_tag_with_no_entry_on_pypi_is_ignored(self, world, capsys):
        """A release that never reached PyPI is no error: the next version releases normally."""
        world.version("0.3.9", Proof(pypi="absent"))
        world.version("0.4.0")

        assert world.run() == 0

        assert "release integrity: 1 version(s) on PyPI, 0 refused" in capsys.readouterr().out

    def test_pypi_empty_while_the_remote_holds_release_tags_passes(self, world, capsys):
        """The project JSON of a project PyPI has none of is an empty file; the tags left by a rehearsal do not matter."""
        world.version("0.3.9", Proof(pypi="absent"))
        world.version("0.4.0", Proof(pypi="absent"))
        world.git("tag", "models-v1.0.0")
        world.git("push", "origin", "models-v1.0.0")

        assert world.run() == 0

        assert "0 version(s) on PyPI, 0 refused" in capsys.readouterr().out

    def test_a_brand_new_project_with_no_tags_at_all_passes(self, world):
        assert world.run() == 0


class TestTheVersionBeingResumed:
    """``--version V``: PyPI may serve a subset of V's manifest, with equal sha256, and of no other version."""

    def test_a_subset_of_the_manifest_with_equal_digests_passes(self, world, capsys):
        world.version("0.3.9", Proof(pypi="subset"))

        assert world.run("0.3.9") == 0

        assert f"ok  {PKG} 0.3.9 on PyPI" in capsys.readouterr().out

    def test_the_full_set_passes(self, world):
        world.version("0.3.9")

        assert world.run("0.3.9") == 0

    def test_a_subset_is_refused_without_the_version_named(self, world, capsys):
        world.version("0.3.9", Proof(pypi="subset"))

        assert world.run() == 1

        assert f"{names('0.3.9')[1]}: PyPI serves no such file" in capsys.readouterr().out

    def test_a_subset_of_another_version_is_refused(self, world, capsys):
        world.version("0.3.9", Proof(pypi="subset"))
        world.version("0.4.0")

        assert world.run("0.4.0") == 1

        out = capsys.readouterr().out
        assert f"REFUSED  {PKG} 0.3.9 on PyPI" in out
        assert f"ok  {PKG} 0.4.0 on PyPI" in out

    def test_a_subset_with_a_differing_digest_is_refused(self, world, capsys):
        world.version("0.3.9", Proof(pypi="subset"))
        wheel = names("0.3.9")[0]
        world.page_files[wheel] = "c" * 64

        assert world.run("0.3.9") == 1

        assert f"{wheel}: PyPI serves {'c' * 64}, the signed manifest at v0.3.9 says {digest(wheel)}" in (
            capsys.readouterr().out
        )

    def test_a_digest_mismatch_is_refused_even_for_the_version_named(self, world, capsys):
        world.version("0.3.9", Proof(pypi="digest-mismatch"))

        assert world.run("0.3.9") == 1

        assert "the signed manifest at v0.3.9 says" in capsys.readouterr().out

    def test_a_file_the_manifest_does_not_list_is_refused_even_for_the_version_named(self, world, capsys):
        world.version("0.3.9", Proof(manifest="one-file"))

        assert world.run("0.3.9") == 1

        sdist = names("0.3.9")[1]
        assert f"{sdist}: PyPI serves {digest(sdist)}, the signed manifest at v0.3.9 says no such file" in (
            capsys.readouterr().out
        )

    def test_a_subset_with_a_yanked_file_is_judged_like_any_other(self, world):
        world.version("0.3.9", Proof(pypi="subset"))
        world.yanked.add(names("0.3.9")[0])

        assert world.run("0.3.9") == 0

    def test_the_version_named_still_needs_its_tag_and_signing(self, world, capsys):
        world.version("0.3.9", Proof(pypi="subset", tag=False))

        assert world.run("0.3.9") == 1

        assert "tag v0.3.9 is not on origin" in capsys.readouterr().out

    def test_a_listed_version_is_judged_by_its_entry_even_when_named(self, world, capsys):
        world.version("0.3.9", Proof(pypi="subset", tag=False, run=None, assets=None))
        world.list_version("0.3.9")

        assert world.run("0.3.9") == 0

        assert f"superseded  {PKG} 0.3.9 on PyPI" in capsys.readouterr().out


class TestWhatCannotBeRead:
    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("<html><body><h1>Maintenance</h1></body></html>", id="a page that is no JSON"),
            pytest.param('{"message": "Service Unavailable"}', id="JSON that is no project"),
            pytest.param('{"info": {"name": "axmem"}, "releases": []}', id="releases that is no object"),
            pytest.param(fakes.pypi_json({names("0.3.9")[0]: "a" * 64}, "other"), id="the project of another name"),
        ],
    )
    def test_a_document_that_is_not_the_json_of_the_package_is_exit_two_not_an_empty_project(
        self, world, capsys, raw,
    ):
        world.version("0.3.9")

        assert world.run(raw=raw) == 2

        assert "ERROR:" in capsys.readouterr().err

    def test_a_file_without_a_digest_is_exit_two(self, world, capsys):
        world.version("0.3.9")
        document = json.loads(fakes.pypi_json(world.page_files, PKG))
        document["releases"]["0.3.9"][0]["digests"] = {"sha256": None}

        assert world.run(raw=json.dumps(document)) == 2

        assert "has no sha256 digest" in capsys.readouterr().err

    def test_a_file_linked_at_another_host_is_exit_two(self, world, capsys):
        world.version("0.3.9")

        assert world.run(raw=fakes.pypi_json(world.page_files, PKG, host="example.invalid")) == 2

        assert "is linked at a URL that is not https://files.pythonhosted.org/" in capsys.readouterr().err

    def test_a_malformed_superseded_file_is_exit_two_and_names_the_file(self, world, capsys):
        world.version("0.3.9")
        argv = world.args()
        Path(argv[argv.index("--superseded") + 1]).write_text(
            '[{"version": "0.4.0"}]', encoding="utf-8",
        )

        assert integrity.main(argv) == 2

        assert "release-superseded.json: [0]: missing" in capsys.readouterr().err

    def test_a_missing_superseded_file_is_exit_two(self, world, capsys):
        argv = world.args()
        Path(argv[argv.index("--superseded") + 1]).unlink()

        assert integrity.main(argv) == 2

        assert "cannot read the file" in capsys.readouterr().err

    def test_github_not_answering_is_exit_two(self, world, capsys):
        world.version("0.3.9")
        argv = world.args()
        fakes.write_scenario(world.state, {"fail": "HTTP 502"})

        assert integrity.main(argv) == 2

        assert "HTTP 502" in capsys.readouterr().err

    def test_a_json_file_that_cannot_be_read_is_exit_two(self, world, capsys):
        argv = world.args()
        Path(argv[argv.index("--json") + 1]).unlink()

        assert integrity.main(argv) == 2

        assert "ERROR:" in capsys.readouterr().err

    def test_an_origin_that_cannot_be_listed_is_exit_two(self, world, capsys):
        argv = world.args()
        world.git("remote", "set-url", "origin", str(world.base / "gone.git"))

        assert integrity.main(argv) == 2

        assert "ls-remote" in capsys.readouterr().err
