# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Condition 5 of the release guard accepts the ``.gitignore`` that uv writes into ``dist/`` and nothing else.

``uv build`` writes ``dist/.gitignore`` (one byte, ``*``) on every build, and the guard refuses every entry of ``dist/``
that the manifest does not name, so a release stopped at condition 5 until that one file was accepted. The rule: it is
accepted when it is a regular file holding exactly ``*``; any other entry (other bytes, another dotfile, another
artefact, a directory, a link) is named and refused. ``scripts.release.digests verify`` is not changed: it reads only the
manifest's artefacts through ``*.whl`` and ``*.tar.gz``. The directory is the one ``--dist`` names: in ``publish.yml``
the signed files downloaded from the GitHub release, never a local rebuild. ``test_release_guard_links.py`` covers the
entries that are links.
"""

import hashlib
import json
import os

import pytest

from scripts.release import guard

NAMES = ["axmem-0.1.0-py3-none-any.whl", "axmem-0.1.0.tar.gz"]
TAG = "v0.1.0"
SHA = "a" * 40
NOT_AN_ARTEFACT = "is not an artefact of the manifest"


def _manifest(root, dist):
    """A manifest naming the two artefacts of ``dist`` with their real digests."""
    artifacts = [
        {"name": name, "sha256": hashlib.sha256((dist / name).read_bytes()).hexdigest()}
        for name in NAMES
    ]
    path = root / "release-digests.json"
    path.write_text(
        json.dumps({"schema": 1, "tag": TAG, "artifacts": artifacts}),
        encoding="utf-8",
    )
    return path


@pytest.fixture(name="built")
def _built(tmp_path):
    """``(manifest, dist)`` of a ``dist/`` as a real ``uv build`` leaves it: the artefacts and ``.gitignore``."""
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in NAMES:
        (dist / name).write_bytes(f"bytes of {name}\n".encode())
    (dist / ".gitignore").write_bytes(b"*")
    return _manifest(tmp_path, dist), dist


# --- what uv leaves is accepted -------------------------------------------------------------------


def test_the_artefacts_plus_the_exact_gitignore_hold_nothing_stray(built):
    manifest, dist = built

    assert guard.dist_problems(manifest, dist) == []


def test_a_dist_without_the_marker_is_accepted_too(built):
    manifest, dist = built
    (dist / ".gitignore").unlink()

    assert guard.dist_problems(manifest, dist) == []


def test_a_dist_that_does_not_exist_is_the_digest_checks_to_name(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in NAMES:
        (dist / name).write_bytes(b"x")
    manifest = _manifest(tmp_path, dist)
    for entry in dist.iterdir():
        entry.unlink()
    dist.rmdir()

    problems = guard.dist_problems(manifest, dist)

    assert problems
    assert not [p for p in problems if NOT_AN_ARTEFACT in p]


# --- every other entry is named and refused -------------------------------------------------------


@pytest.mark.parametrize("text", ["", "*\n", "**", "* ", "\n*", "dist/\n", "*\r\n"])
def test_a_gitignore_with_other_bytes_is_refused(built, text):
    manifest, dist = built
    (dist / ".gitignore").write_bytes(text.encode())

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/.gitignore {NOT_AN_ARTEFACT}")
    assert "holding exactly '*'" in problems[0]


@pytest.mark.parametrize("name", [".DS_Store", ".gitattributes", ".gitignore.orig", ".env"])
def test_a_dotfile_other_than_gitignore_is_refused(built, name):
    manifest, dist = built
    (dist / name).write_bytes(b"*")

    assert guard.dist_problems(manifest, dist) == [f"dist/{name} {NOT_AN_ARTEFACT}"]


@pytest.mark.parametrize(
    "name", ["other-1.0.0-py3-none-any.whl", "other-1.0.0.tar.gz", "stray-1.0.0.zip"],
)
def test_an_extra_file_is_refused_whether_or_not_it_matches_an_artefact_glob(built, name):
    manifest, dist = built
    (dist / name).write_bytes(b"x")

    problems = guard.dist_problems(manifest, dist)

    assert f"dist/{name} {NOT_AN_ARTEFACT}" in problems
    assert not [p for p in problems if ".gitignore" in p]


def test_a_directory_named_gitignore_is_refused(built):
    manifest, dist = built
    (dist / ".gitignore").unlink()
    (dist / ".gitignore").mkdir()

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/.gitignore {NOT_AN_ARTEFACT}")


def test_a_directory_with_another_name_is_refused(built):
    manifest, dist = built
    (dist / "build").mkdir()

    assert guard.dist_problems(manifest, dist) == [f"dist/build {NOT_AN_ARTEFACT}"]


def test_a_symlink_named_gitignore_to_a_file_that_holds_the_byte_is_refused(built, tmp_path):
    manifest, dist = built
    (dist / ".gitignore").unlink()
    target = tmp_path / "marker"
    target.write_bytes(b"*")
    (dist / ".gitignore").symlink_to(target)

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/.gitignore {NOT_AN_ARTEFACT}")


def test_a_dangling_symlink_named_gitignore_is_refused(built, tmp_path):
    manifest, dist = built
    (dist / ".gitignore").unlink()
    (dist / ".gitignore").symlink_to(tmp_path / "nowhere")

    assert len(guard.dist_problems(manifest, dist)) == 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_fifo_named_gitignore_is_refused_and_never_opened(built):
    manifest, dist = built
    (dist / ".gitignore").unlink()
    os.mkfifo(dist / ".gitignore")

    assert len(guard.dist_problems(manifest, dist)) == 1


def test_a_gitignore_that_cannot_be_read_is_refused(built, monkeypatch):
    manifest, dist = built
    real = guard.Path.read_bytes

    def unreadable(self):
        if self.name == ".gitignore":
            raise PermissionError(13, "Permission denied")
        return bytes(real(self))

    monkeypatch.setattr(guard.Path, "read_bytes", unreadable)

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/.gitignore {NOT_AN_ARTEFACT}")


def test_every_stray_is_named_in_one_pass(built):
    manifest, dist = built
    (dist / ".gitignore").write_bytes(b"**")
    (dist / ".DS_Store").write_bytes(b"x")
    (dist / "extra-1.0.0.tar.gz").write_bytes(b"x")

    named = sorted(p.split(" ")[0] for p in guard.dist_problems(manifest, dist) if "dist/" in p)

    assert named == ["dist/.DS_Store", "dist/.gitignore", "dist/extra-1.0.0.tar.gz"]


# --- the marker never excuses the artefacts -------------------------------------------------------


def test_a_missing_artefact_still_fails_with_the_marker_present(built):
    manifest, dist = built
    (dist / NAMES[0]).unlink()

    problems = guard.dist_problems(manifest, dist)

    assert any(NAMES[0] in p for p in problems)
    assert not [p for p in problems if NOT_AN_ARTEFACT in p]


def test_a_differing_artefact_still_fails_with_the_marker_present(built):
    manifest, dist = built
    (dist / NAMES[1]).write_bytes(b"other bytes")

    problems = guard.dist_problems(manifest, dist)

    assert any(NAMES[1] in p for p in problems)
    assert not [p for p in problems if NOT_AN_ARTEFACT in p]


# --- check reports condition five -----------------------------------------------------------------


@pytest.fixture(name="check")
def _check(built, monkeypatch):
    """``check`` with conditions 1 to 4 held, so that only the content of ``dist/`` decides; returns ``run``."""
    manifest, dist = built
    for name in ("commit_problems", "ci_problems", "tag_problems", "signing_problems"):
        monkeypatch.setattr(guard, name, lambda *args, **kwargs: [])

    def run():
        argv = [
            "check",
            "--repo",
            "Axiumine/axmem",
            "--sha",
            SHA,
            "--tag",
            TAG,
            "--remote",
            "axiumine",
        ]
        return guard.main([*argv, "--root", str(manifest.parent), "--dist", str(dist)])

    return run


def test_check_passes_with_the_marker(check, capsys):
    assert check() == 0

    out = capsys.readouterr().out
    assert "[5] the files to publish are byte-identical to the manifest and dist holds nothing else: ok" in out
    assert "release guard: all 5 conditions hold" in out


def _other_bytes(dist, _tmp):
    (dist / ".gitignore").write_bytes(b"other")


def _hidden_dotfile(dist, _tmp):
    (dist / ".hidden").write_bytes(b"x")


def _other_wheel(dist, _tmp):
    (dist / "other-1.0.0-py3-none-any.whl").write_bytes(b"x")


def _directory(dist, _tmp):
    (dist / ".gitignore").unlink()
    (dist / ".gitignore").mkdir()


def _symlink(dist, tmp):
    (dist / ".gitignore").unlink()
    (tmp / "marker").write_bytes(b"*")
    (dist / ".gitignore").symlink_to(tmp / "marker")


@pytest.mark.parametrize(
    ("make", "named"),
    [
        (_other_bytes, ".gitignore"),
        (_hidden_dotfile, ".hidden"),
        (_other_wheel, "other-1.0.0-py3-none-any.whl"),
        (_directory, ".gitignore"),
        (_symlink, ".gitignore"),
    ],
    ids=["other bytes", "dotfile", "wheel", "directory", "symlink"],
)
def test_check_refuses_anything_else_and_names_it(check, built, capsys, tmp_path, make, named):
    _, dist = built
    make(dist, tmp_path)

    assert check() == 1

    out = capsys.readouterr().out
    assert "[5] the files to publish are byte-identical to the manifest and dist holds nothing else: FAIL" in out
    assert f"dist/{named} {NOT_AN_ARTEFACT}" in out
    assert "release guard: REFUSED, 1 of 5 conditions unmet" in out
