# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``guard pypi-missing``: what ``publish.yml`` takes out of ``dist`` before the upload.

PyPI refuses a file name it has used, even for the same bytes, and the upload action stops at the first refusal. After a
half upload (the wheel there, the sdist not) the run must send the sdist alone; a file PyPI holds with other bytes is a spent
version and fails the run. The command only looks at the entries of ``dist`` by their own names.
"""

import hashlib

from scripts.release import guard
from tests.release_support import fakes

WHEEL, SDIST = "axmem-0.1.1-py3-none-any.whl", "axmem-0.1.1.tar.gz"
CONTENT = {WHEEL: b"the wheel", SDIST: b"the sdist"}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name, data in CONTENT.items():
        (dist / name).write_bytes(data)
    return dist


def _missing(tmp_path, dist, held, version="0.1.1"):
    document = tmp_path / "pypi.json"
    document.write_text(fakes.pypi_json(held) if held else "", encoding="utf-8")
    return guard.main(
        ["pypi-missing", "--json", str(document), "--package", "axmem", "--version", version, "--dist", str(dist)],
    )


def test_a_version_that_pypi_does_not_hold_leaves_dist_as_it_is(tmp_path, capsys):
    dist = _dist(tmp_path)

    assert _missing(tmp_path, dist, {}) == 0
    assert sorted(path.name for path in dist.iterdir()) == sorted(CONTENT)
    assert capsys.readouterr().out == ""


def test_the_file_that_pypi_holds_with_the_same_bytes_is_taken_out_and_the_missing_one_stays(tmp_path, capsys):
    dist = _dist(tmp_path)

    assert _missing(tmp_path, dist, {WHEEL: _sha(CONTENT[WHEEL])}) == 0
    assert [path.name for path in dist.iterdir()] == [SDIST]
    assert capsys.readouterr().out == f"{WHEEL}: PyPI holds it with the same sha256, not uploaded again\n"


def test_a_version_that_pypi_holds_whole_leaves_nothing_to_upload(tmp_path):
    dist = _dist(tmp_path)

    assert _missing(tmp_path, dist, {name: _sha(data) for name, data in CONTENT.items()}) == 0
    assert list(dist.iterdir()) == []


def test_a_file_that_pypi_holds_with_other_bytes_fails_and_names_both_digests(tmp_path, capsys):
    dist = _dist(tmp_path)

    assert _missing(tmp_path, dist, {WHEEL: _sha(b"other bytes")}) == 1
    err = capsys.readouterr().err
    assert f"ERROR: {WHEEL}: PyPI holds sha256 {_sha(b'other bytes')}, this run has {_sha(CONTENT[WHEEL])}" in err
    assert (dist / WHEEL).exists()  # nothing is removed on the way to a refusal that matters more


def test_a_file_of_pypi_that_dist_does_not_carry_is_not_judged(tmp_path):
    dist = _dist(tmp_path)
    (dist / WHEEL).unlink()

    assert _missing(tmp_path, dist, {WHEEL: _sha(b"bytes that this run does not have")}) == 0
    assert [path.name for path in dist.iterdir()] == [SDIST]
