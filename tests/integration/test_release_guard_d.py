"""The reads the release flow makes through the guard: ``find-release``, and the entry points.

``find-release`` names the release commit that a re-run resumes from, only when the manifest committed there is this
version and tag; PyPI has one index, so it takes no index. The reads of PyPI are in ``test_release_guard_pypi_state.py``
and the publication in ``test_release_guard_publish_*.py``. The last cases cover the exit code of a fact that cannot be
read, the missing command, a command or option the guard no longer has, and the module run as a program.
"""

import runpy
import subprocess
import sys

import pytest

from scripts.release import guard
from tests.integration.release_guard_world import REPO, TAG, World


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


class TestFindRelease:
    def find(self, world, version="0.1.0"):
        return int(guard.main(["find-release", "--version", version, "--root", str(world.repo)]))

    def test_it_prints_the_last_commit_that_touched_the_manifest(self, world, capsys):
        (world.repo / "other.txt").write_text("later work\n", encoding="utf-8")
        world.git("add", "-A")
        world.git("commit", "-m", "later work")

        assert self.find(world) == 0

        assert capsys.readouterr().out.strip() == world.sha

    @pytest.mark.parametrize("version", ["0.1.1", "0.1.0rc1", "0.1"])
    def test_a_manifest_of_another_version_is_not_this_release(self, world, version):
        assert self.find(world, version) == 4

    def test_a_manifest_whose_tag_is_not_this_releases_is_not_this_release(self, world):
        world.write_manifest(tag="v0.1.0-odd")
        world.git("commit", "-am", "odd tag")

        assert self.find(world) == 4

    def test_a_history_without_a_manifest_is_not_a_release(self, world):
        empty = world.repo.parent / "empty"
        subprocess.run(  # noqa: S603
            ["git", "init", "-b", "main", str(empty)],  # noqa: S607
            env=world.env,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603
            ["git", "-C", str(empty), "commit", "--allow-empty", "-m", "x"],  # noqa: S607
            env=world.env,
            check=True,
            capture_output=True,
        )
        argv = ["find-release", "--version", "0.1.0", "--root", str(empty)]

        assert int(guard.main(argv)) == 4

    def test_a_manifest_that_a_later_commit_deleted_is_not_a_release(self, world):
        world.git("rm", "-q", "release-digests.json")
        world.git("commit", "-m", "delete the manifest")

        assert self.find(world) == 4


class TestMain:
    def test_a_facts_error_is_exit_two(self, world, monkeypatch, capsys):
        monkeypatch.setenv("PATH", "/nonexistent")

        code = guard.main(["find-release", "--version", "1", "--root", str(world.repo)])

        assert code == 2
        assert "ERROR: cannot run git" in capsys.readouterr().err

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit) as stop:
            guard.main([])
        assert stop.value.code == 2

    @pytest.mark.parametrize(
        "argv",
        [
            ["find-release", "--version", "0.1.0", "--index", "dev"],
            ["pypi-state", "--page", "p", "--package", "axmem", "--version", "0.1.0"],
            ["pypi-urls", "--json", "p", "--package", "axmem", "--version", "0.1.0", "--simple-url", "u"],
            ["publish-wait", "--repo", REPO, "--sha", "s", "--tag", TAG, "--run", "x1", "--deadline", "1"],
            ["publish-state", "--repo", REPO, "--sha", "s", "--tag", TAG, "--lag", "inf"],
        ],
    )
    def test_an_option_the_guard_no_longer_has_or_cannot_use_is_refused_by_the_parser(self, argv):
        with pytest.raises(SystemExit) as stop:
            guard.main(argv)
        assert stop.value.code == 2


@pytest.mark.filterwarnings("ignore:'scripts.release.guard' found in sys.modules:RuntimeWarning")
def test_the_module_runs_as_a_plain_program(world, monkeypatch, capsys):
    argv = ["guard", "check", "--repo", REPO, "--sha", world.sha, "--tag", TAG]
    monkeypatch.setattr(sys, "argv", [*argv, "--root", str(world.repo)])

    with pytest.raises(SystemExit) as stop:
        runpy.run_module("scripts.release.guard", run_name="__main__")

    assert stop.value.code == 0
    assert "all 5 conditions hold" in capsys.readouterr().out
