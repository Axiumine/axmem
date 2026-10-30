# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""An sdist may hold only the files that git tracks, so that a clean checkout of the tag rebuilds the same bytes.

hatchling's sdist target walks the working tree, so an untracked file of the release machine (a nested ignored
directory, an editor folder) can slip into the tarball and make the CI rebuild differ. ``scripts.release.sdist``
compares the members of the built sdist with ``git ls-files`` and refuses the difference. These cases build a
scratch git repository and a hand-made tarball for each defect, and prove that the check names it.
"""

import io
import os
import runpy
import shutil
import subprocess
import sys
import tarfile

import pytest

from scripts.release import sdist

ROOT_DIR = "axmem-0.1.0"
SDIST_NAME = "axmem-0.1.0.tar.gz"
TRACKED = {
    "app/__init__.py": b"",
    "main.py": b"print('axmem')\n",
    "pyproject.toml": b"[project]\n",
    "README.md": b"Axmem\n",
}
GIT = shutil.which("git")


@pytest.fixture(autouse=True)
def _no_inherited_git_environment(monkeypatch):
    """A Git hook exports GIT_DIR/GIT_INDEX_FILE; tests that init repos in tmp dirs must not inherit them."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)


def git(cwd, *args):
    config = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "protocol.file.allow=always"]
    subprocess.run(  # noqa: S603 - the git found on PATH with fixed options
        [GIT, *config, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
    )


def a_repo(path, files=None):
    """A git repository at ``path`` that tracks ``files`` (``TRACKED`` by default)."""
    path.mkdir(parents=True)
    git(path, "init", "--quiet")
    for name, data in (TRACKED if files is None else files).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_bytes(data)
    git(path, "add", "--all")
    return path


def an_sdist(dist, members, name=SDIST_NAME):
    """A tarball of ``members`` (name to bytes, or to None for a directory) under the ``axmem-0.1.0/`` root."""
    dist.mkdir(exist_ok=True)
    path = dist / name
    with tarfile.open(path, "w:gz") as archive:
        for member, data in members.items():
            if data is None:
                entry = tarfile.TarInfo(f"{ROOT_DIR}/{member}")
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
                continue
            entry = tarfile.TarInfo(f"{ROOT_DIR}/{member}")
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return path


def check(capsys, repo, dist):
    code = sdist.main(["--dist", str(dist), "--repo", str(repo)])
    seen = capsys.readouterr()
    return code, seen.out, seen.err


@pytest.fixture
def repo(tmp_path):
    return a_repo(tmp_path / "repo")


@pytest.fixture
def dist(tmp_path):
    return tmp_path / "dist"


# --- what passes ----------------------------------------------------------------------------------------------


def test_an_sdist_of_tracked_files_passes(capsys, repo, dist):
    an_sdist(dist, TRACKED)

    assert check(capsys, repo, dist) == (0, f"OK: every file in {SDIST_NAME} is tracked by git.\n", "")


def test_the_files_that_the_build_writes_need_no_tracking(capsys, repo, dist):
    """``PKG-INFO`` and the version file that hatch-vcs writes are in the sdist and never in git."""
    assert {"PKG-INFO", "app/_version.py"} == sdist.GENERATED
    an_sdist(dist, TRACKED | {"PKG-INFO": b"Name: axmem\n", "app/_version.py": b"version = '0.1.0'\n"})

    assert check(capsys, repo, dist)[0] == 0


def test_an_sdist_that_holds_less_than_git_tracks_passes(capsys, repo, dist):
    an_sdist(dist, {"main.py": TRACKED["main.py"]})

    assert check(capsys, repo, dist)[0] == 0


def test_directory_entries_are_not_files_and_are_not_checked(capsys, repo, dist):
    an_sdist(dist, TRACKED | {"scratch": None})

    assert check(capsys, repo, dist)[0] == 0
    assert sdist.sdist_members(dist / SDIST_NAME) == set(TRACKED)


def test_a_wheel_next_to_the_sdist_is_not_read(capsys, repo, dist):
    an_sdist(dist, TRACKED)
    (dist / "axmem-0.1.0-py3-none-any.whl").write_bytes(b"not a zip")

    assert check(capsys, repo, dist)[0] == 0


def test_the_repository_defaults_to_the_working_directory(capsys, repo, dist, monkeypatch):
    an_sdist(dist, TRACKED)
    monkeypatch.chdir(repo)

    assert sdist.main(["--dist", str(dist)]) == 0
    assert "OK:" in capsys.readouterr().out


# --- what is refused ------------------------------------------------------------------------------------------


def test_an_untracked_file_is_refused_and_named(capsys, repo, dist):
    an_sdist(dist, TRACKED | {"notes/draft.txt": b"private"})

    code, out, err = check(capsys, repo, dist)

    assert (code, out) == (1, "")
    assert f"ERROR: {SDIST_NAME} contains 1 file(s) git does not track." in err
    assert "    notes/draft.txt\n" in err
    assert "the signing job will refuse the release" in err
    assert "Add them to [tool.hatch.build.targets.sdist] in pyproject.toml." in err


def test_a_file_that_a_nested_gitignore_hides_from_git_is_still_refused(capsys, repo, dist):
    """hatchling honours only the root ``.gitignore``, so it packs what git ignores: that is the case to catch."""
    (repo / "tools").mkdir()
    (repo / "tools" / ".gitignore").write_text("cache/\n", encoding="utf-8")
    (repo / "tools" / "cache").mkdir()
    (repo / "tools" / "cache" / "blob.bin").write_bytes(b"x")
    git(repo, "add", "--all")
    an_sdist(dist, TRACKED | {"tools/cache/blob.bin": b"x"})

    code, _, err = check(capsys, repo, dist)

    assert code == 1
    assert "    tools/cache/blob.bin\n" in err


def test_every_untracked_file_is_listed_in_order(capsys, repo, dist):
    an_sdist(dist, TRACKED | {"b.txt": b"", "a.txt": b""})

    err = check(capsys, repo, dist)[2]

    assert err.index("    a.txt\n") < err.index("    b.txt\n")
    assert "contains 2 file(s)" in err


def test_a_long_list_of_offenders_is_cut_with_a_count(capsys, repo, dist):
    extra = {f"junk/{n:03d}.txt": b"" for n in range(sdist.MAX_LISTED + 5)}
    an_sdist(dist, TRACKED | extra)

    err = check(capsys, repo, dist)[2]

    assert err.count("    junk/") == sdist.MAX_LISTED
    assert "… and 5 more" in err
    assert f"junk/{sdist.MAX_LISTED:03d}.txt" not in err


def test_the_digest_manifest_is_refused_even_when_git_tracks_it(capsys, tmp_path, dist):
    """The release script rewrites it after the build, so a packed copy is the previous release's."""
    repo = a_repo(tmp_path / "tracked", TRACKED | {"release-digests.json": b"{}"})
    an_sdist(dist, TRACKED | {"release-digests.json": b"{}"})

    code, out, err = check(capsys, repo, dist)

    assert (code, out) == (1, "")
    assert f"ERROR: {SDIST_NAME} packs release-digests.json, which the release rewrites" in err
    assert "Add it to [tool.hatch.build.targets.sdist] in pyproject.toml." in err


def test_a_directory_that_is_not_a_repository_tracks_nothing(capsys, tmp_path, dist):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    an_sdist(dist, {"main.py": b""})

    assert sdist.tracked_files(not_a_repo) == set()
    code, _, err = check(capsys, not_a_repo, dist)

    assert code == 1
    assert "    main.py\n" in err


# --- the sdist itself -----------------------------------------------------------------------------------------


def test_two_sdists_are_an_error_that_names_both(repo, dist):
    an_sdist(dist, TRACKED)
    an_sdist(dist, TRACKED, name="axmem-0.1.1.tar.gz")

    with pytest.raises(SystemExit) as raised:
        sdist.main(["--dist", str(dist), "--repo", str(repo)])

    assert raised.value.code == f"ERROR: expected exactly one sdist in {dist}, found: {SDIST_NAME}, axmem-0.1.1.tar.gz"


def test_no_sdist_is_an_error(repo, dist):
    dist.mkdir()

    with pytest.raises(SystemExit) as raised:
        sdist.main(["--dist", str(dist), "--repo", str(repo)])

    assert raised.value.code == f"ERROR: expected exactly one sdist in {dist}, found: none"


def test_the_members_are_read_without_the_root_directory(dist):
    path = an_sdist(dist, {"app/a.py": b"", "b.py": b""})

    assert sdist.sdist_members(path) == {"app/a.py", "b.py"}


def test_a_file_outside_the_root_directory_has_no_member_name(dist):
    path = dist / SDIST_NAME
    dist.mkdir()
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo("stray")
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))

    assert sdist.sdist_members(path) == set()


# --- the files that git tracks --------------------------------------------------------------------------------


def test_the_tracked_files_of_a_repository_without_submodules_are_its_own(repo):
    (repo / "untracked.txt").write_text("x", encoding="utf-8")

    assert sdist.tracked_files(repo) == set(TRACKED)


def test_the_files_of_a_submodule_are_tracked_under_its_path(capsys, tmp_path, dist):
    library = a_repo(tmp_path / "library", {"lib.py": b"x = 1\n"})
    git(library, "commit", "--quiet", "--message", "library")
    repo = a_repo(tmp_path / "repo")
    git(repo, "submodule", "--quiet", "add", str(library), "vendor/library")
    an_sdist(dist, TRACKED | {"vendor/library/lib.py": b"x = 1\n"})

    assert "vendor/library/lib.py" in sdist.tracked_files(repo)
    assert check(capsys, repo, dist)[0] == 0


# --- the command line -----------------------------------------------------------------------------------------


def test_the_module_runs_as_a_plain_program(monkeypatch, capsys, repo, dist):
    an_sdist(dist, TRACKED)
    monkeypatch.delitem(sys.modules, "scripts.release.sdist")
    monkeypatch.setattr(sys, "argv", ["sdist", "--dist", str(dist), "--repo", str(repo)])

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("scripts.release.sdist", run_name="__main__")

    assert raised.value.code == 0
    assert "OK:" in capsys.readouterr().out
