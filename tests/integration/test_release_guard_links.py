"""Condition 5 of the release guard takes only plain files: no link, of either kind, stands in for an artefact.

A ``dist/`` of signed files whose wheel is a symlink, or a second name of the same inode, to a copy with the manifest's
bytes outside ``dist/`` passes the digest check (it reads through the link) and the stray check (the name is the
manifest's), and the upload would go through the link; only a swap of the target between the guard's read and the
upload's would go unseen until the read-back of PyPI, after the irreversible step. ``dist/.gitignore`` as a hard link
to a file holding ``*`` would pass too (``lstat`` says regular file, one byte, ``*``), which does no harm but is not what
``uv build`` writes. So the guard asks for a regular file with a single link, for the artefacts and for the marker, and
names the entry that is not one. The job of ``publish.yml`` that uploads also re-checks the sums of the files it is
given; this is the first of the two nets.
"""

import hashlib
import json
import os

import pytest

from scripts.release import guard

NAMES = ["axmem-0.1.0-py3-none-any.whl", "axmem-0.1.0.tar.gz"]
WHEEL, SDIST = NAMES
NOT_AN_ARTEFACT = "is not an artefact of the manifest"


@pytest.fixture(name="built")
def _built(tmp_path):
    """``(manifest, dist)`` of a ``dist/`` as a real ``uv build`` leaves it: the artefacts and ``.gitignore``."""
    dist = tmp_path / "dist"
    dist.mkdir()
    artifacts = []
    for name in NAMES:
        (dist / name).write_bytes(f"bytes of {name}\n".encode())
        artifacts.append(
            {"name": name, "sha256": hashlib.sha256((dist / name).read_bytes()).hexdigest()},
        )
    (dist / ".gitignore").write_bytes(b"*")
    manifest = tmp_path / "release-digests.json"
    manifest.write_text(
        json.dumps({"schema": 1, "tag": "v0.1.0", "artifacts": artifacts}),
        encoding="utf-8",
    )
    return manifest, dist


def _replace_with_a_symlink(entry, outside):
    """``entry`` becomes a symlink to a copy of its own bytes outside ``dist/``."""
    outside.write_bytes(entry.read_bytes())
    entry.unlink()
    entry.symlink_to(outside)


def _named_with_a_single_link(problems, name):
    return [p for p in problems if p.startswith(f"dist/{name} ") and "single link" in p]


# --- an artefact that is a link -------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_a_symlink_with_the_manifests_bytes_is_named_and_refused(built, tmp_path, name):
    manifest, dist = built
    _replace_with_a_symlink(dist / name, tmp_path / f"elsewhere-{name}")

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/{name} ")
    assert "single link" in problems[0]


@pytest.mark.parametrize("name", NAMES)
def test_a_second_hard_link_with_the_manifests_bytes_is_named_and_refused(built, tmp_path, name):
    manifest, dist = built
    os.link(dist / name, tmp_path / f"second-name-{name}")

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/{name} ")
    assert "single link" in problems[0]


def test_a_symlink_that_points_nowhere_is_named_too(built):
    manifest, dist = built
    (dist / SDIST).unlink()
    (dist / SDIST).symlink_to(dist / "missing")

    assert _named_with_a_single_link(guard.dist_problems(manifest, dist), SDIST)


def test_a_directory_in_place_of_an_artefact_is_named_too(built):
    manifest, dist = built
    (dist / SDIST).unlink()
    (dist / SDIST).mkdir()

    assert _named_with_a_single_link(guard.dist_problems(manifest, dist), SDIST)


def test_plain_files_are_not_named(built):
    manifest, dist = built

    assert guard.dist_problems(manifest, dist) == []


def test_a_second_name_that_is_removed_again_leaves_a_plain_file(built, tmp_path):
    manifest, dist = built
    os.link(dist / WHEEL, tmp_path / "second-name")
    (tmp_path / "second-name").unlink()

    assert guard.dist_problems(manifest, dist) == []


# --- the marker that is a link --------------------------------------------------------------------


def test_a_hard_link_to_a_file_holding_the_one_byte_is_not_what_uv_writes(built, tmp_path):
    manifest, dist = built
    (tmp_path / "star").write_bytes(b"*")
    (dist / ".gitignore").unlink()
    os.link(tmp_path / "star", dist / ".gitignore")

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/.gitignore {NOT_AN_ARTEFACT}")
    assert "single link" in problems[0]


def test_a_symlink_to_a_file_holding_the_one_byte_is_still_refused(built, tmp_path):
    manifest, dist = built
    (dist / ".gitignore").unlink()
    (tmp_path / "star").write_bytes(b"*")
    (dist / ".gitignore").symlink_to(tmp_path / "star")

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/.gitignore {NOT_AN_ARTEFACT}")


def test_a_marker_that_vanishes_between_the_listing_and_the_read_is_refused(built, monkeypatch):
    manifest, dist = built
    real = guard.Path.lstat

    def vanished(self, *args, **kwargs):
        if self.name == ".gitignore":
            raise FileNotFoundError(2, "No such file or directory")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(guard.Path, "lstat", vanished)

    problems = guard.dist_problems(manifest, dist)

    assert len(problems) == 1
    assert problems[0].startswith(f"dist/.gitignore {NOT_AN_ARTEFACT}")
