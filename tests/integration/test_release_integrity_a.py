# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""The release integrity check: a version on PyPI has a pushed tag and a signed release, or it is refused.

A version that reaches PyPI without a tag and a signature stays there, and PyPI refuses a file name for good, so the
release script reads PyPI before it builds anything. These cases give the check the PyPI project JSON, a bare remote
with tags, a scripted ``gh`` and a ``release-superseded.json``, and show which versions it passes and which it refuses
by name with the reason
(no tag, no release, a red or missing signing run, a missing bundle, no provenance). The remote can have any name (Axmem's
is ``axiumine``), and the module runs as a program. The proof that PyPI serves what was signed is in
``test_release_integrity_b.py``; the listed versions, the release being resumed and what cannot be read are in
``test_release_integrity_c.py``.
"""

import runpy
import sys

import pytest

from scripts.release import integrity
from tests.integration.release_integrity_world import PKG, Proof, World, names


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


class TestVersionsWithProof:
    def test_a_tagged_signed_version_passes(self, world, capsys):
        world.version("0.3.9")

        assert world.run() == 0

        out = capsys.readouterr().out
        assert f"ok  {PKG} 0.3.9 on PyPI" in out
        assert "release integrity: 1 version(s) on PyPI, 0 refused" in out

    def test_a_project_not_on_pypi_yet_passes(self, world, capsys):
        assert world.run() == 0

        assert "0 version(s) on PyPI, 0 refused" in capsys.readouterr().out

    def test_the_versions_are_judged_newest_first_by_number(self, world, capsys):
        for version in ("0.3.9", "0.3.10", "0.3.2"):
            world.version(version)

        assert world.run() == 0

        shown = [
            line.split()[2]
            for line in capsys.readouterr().out.splitlines()
            if line.strip().startswith("ok")
        ]
        assert shown == ["0.3.10", "0.3.9", "0.3.2"]

    def test_a_run_that_github_no_longer_records_is_accepted_with_the_provenance_file(self, world):
        world.version("0.2.2", Proof(run=None))

        assert world.run() == 0


class TestVersionsWithoutProof:
    def test_a_version_with_no_tag_is_refused_without_asking_github(self, world, capsys):
        world.version("0.1.0", Proof(tag=False))

        assert world.run() == 1

        out = capsys.readouterr()
        assert f"REFUSED  {PKG} 0.1.0 on PyPI" in out.out
        assert "- tag v0.1.0 is not on origin" in out.out
        assert world.gh_calls() == []

    def test_the_refusal_says_how_a_version_that_is_there_on_purpose_is_named(self, world, capsys):
        world.version("0.4.0", Proof(tag=False, run=None, assets=None))

        assert world.run() == 1

        err = capsys.readouterr().err
        assert "stops the next release" in err
        assert "release-superseded.json" in err

    def test_a_tag_without_a_release_is_refused(self, world, capsys):
        world.version("0.1.8", Proof(run=None, assets=None))

        assert world.run() == 1

        assert "tag v0.1.8 has no GitHub release" in capsys.readouterr().out

    def test_a_failed_signing_run_is_refused_with_its_url(self, world, capsys):
        world.version("0.3.6", Proof(run="failure", assets="unsigned"))

        assert world.run() == 1

        out = capsys.readouterr().out
        assert "release.yml concluded failure — https://github.example/run/Release" in out
        assert f"the release has no {names('0.3.6')[1]}.sigstore.json" in out

    def test_a_missing_bundle_is_refused_by_file(self, world, capsys):
        world.version("0.3.5", Proof(assets="unsigned"))

        assert world.run() == 1

        out = capsys.readouterr().out
        for name in names("0.3.5"):
            assert f"the release has no {name}.sigstore.json" in out

    def test_no_run_and_no_provenance_is_refused(self, world, capsys):
        world.version("0.2.0", Proof(run=None, assets="no-provenance"))

        assert world.run() == 1

        assert "carries no .intoto.jsonl provenance file" in capsys.readouterr().out

    def test_every_refused_version_is_named_and_counted(self, world, capsys):
        world.version("0.1.0", Proof(tag=False))
        world.version("0.1.1", Proof(assets="unsigned"))
        world.version("0.3.9")

        assert world.run() == 1

        out = capsys.readouterr().out
        assert "release integrity: 3 version(s) on PyPI, 2 refused" in out

    def test_a_yanked_version_is_judged_like_any_other(self, world, capsys):
        """A yanked file keeps its name for good, so it still has to be proven (or listed)."""
        world.version("0.3.9", Proof(pypi="yanked"))
        world.version("0.4.0", Proof(pypi="yanked", tag=False))

        assert world.run() == 1

        out = capsys.readouterr().out
        assert f"ok  {PKG} 0.3.9 on PyPI" in out
        assert f"REFUSED  {PKG} 0.4.0 on PyPI" in out


class TestTheRemoteHasAnyName:
    """Axmem's remote is ``axiumine``: the tags, the main and the messages follow ``--remote``, which defaults to ``origin``."""

    @pytest.fixture
    def world(self, tmp_path, monkeypatch):
        return World(tmp_path, monkeypatch, remote="axiumine")

    def test_a_tagged_signed_version_passes_on_a_remote_that_is_not_called_origin(
        self, world, capsys,
    ):
        world.version("0.3.9")

        assert world.run() == 0

        assert "release integrity: 1 version(s) on PyPI, 0 refused" in capsys.readouterr().out

    def test_the_messages_name_that_remote(self, world, capsys):
        world.version("0.1.0", Proof(tag=False))
        world.version("0.3.9", Proof(on_main=False))

        assert world.run() == 1

        out = capsys.readouterr().out
        assert "tag v0.1.0 is not on axiumine" in out
        assert "which is not on axiumine/main" in out

    def test_without_the_flag_the_check_asks_origin_which_this_clone_does_not_have(
        self, world, capsys,
    ):
        world.version("0.3.9")
        argv = [word for word in world.args() if word not in ("--remote", "axiumine")]

        assert integrity.main(argv) == 2

        assert "ERROR:" in capsys.readouterr().err

    def test_a_remote_that_cannot_be_listed_is_exit_two(self, world, capsys):
        argv = world.args()
        world.git("remote", "set-url", "axiumine", str(world.base / "gone.git"))

        assert integrity.main(argv) == 2

        assert "ls-remote" in capsys.readouterr().err


# The test runs the module a second time as __main__ on purpose (runpy), and runpy warns that it is already in sys.modules
@pytest.mark.filterwarnings("ignore:'scripts.release.integrity' found in sys.modules:RuntimeWarning")
def test_the_module_runs_as_a_plain_program(world, monkeypatch, capsys):
    world.version("0.3.9")
    monkeypatch.setattr(sys, "argv", ["integrity", *world.args()])

    with pytest.raises(SystemExit) as stop:
        runpy.run_module("scripts.release.integrity", run_name="__main__")

    assert stop.value.code == 0
    assert "release integrity: 1 version(s) on PyPI, 0 refused" in capsys.readouterr().out
