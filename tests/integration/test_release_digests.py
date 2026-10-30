# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""A signed file must be the file PyPI serves: the digest manifest, its checker and the order of the release script.

``scripts.release.digests`` records the sha256 of every file that the release script builds for PyPI, together with
the build clock, and ``release.yml`` refuses to sign a rebuild that is not byte-identical to it. These cases pin the
checker (a flipped byte, a missing, an extra or an unlisted file, an absent or foreign manifest), what the manifest
holds, the marker file that ``uv build`` leaves in ``dist/``, and the two orders in the release script that make the
manifest mean something: the clock is pinned before the build, and the manifest is written and committed before any
upload. The structure of the signing workflow is pinned in ``tests/architecture/test_release_workflow.py``.
"""

import json
import re
import runpy
import shutil
import sys
from pathlib import Path

import pytest

from scripts.release import digests

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release-pypi.sh"
MANIFEST_NAME = "release-digests.json"
WHEEL = "pkg-1.0.0-py3-none-any.whl"
SDIST = "pkg-1.0.0.tar.gz"


def write_argv(manifest, dist):
    return [
        "write",
        "--manifest", str(manifest),
        "--dist", str(dist),
        "--package", "pkg",
        "--version", "1.0.0",
        "--tag", "v1.0.0",
        "--epoch", "1700000000",
    ]  # fmt: skip


def verify(manifest, dist):
    return digests.main(["verify", "--manifest", str(manifest), "--dist", str(dist)])


@pytest.fixture
def published(tmp_path):
    """Two files and their manifest: what PyPI is given by a release."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(b"wheel bytes")
    (dist / SDIST).write_bytes(b"sdist bytes")
    manifest = tmp_path / MANIFEST_NAME

    assert digests.main(write_argv(manifest, dist)) == 0
    return manifest, dist


# --- the checker ----------------------------------------------------------------------------------------------


def test_a_matching_rebuild_passes(published, capsys):
    manifest, dist = published

    assert verify(manifest, dist) == 0
    assert f"OK: 2 artefact(s) match {MANIFEST_NAME} byte for byte." in capsys.readouterr().out


def test_one_flipped_byte_fails_and_names_both_digests(published, capsys):
    manifest, dist = published
    (dist / SDIST).write_bytes(b"sdist byteS")

    assert verify(manifest, dist) == 1

    err = capsys.readouterr().err
    assert f"DIFFERS  {SDIST}" in err
    assert "manifest: " in err
    assert "here: " in err
    assert "this build is NOT the build in the manifest" in err


def test_a_differing_digest_names_the_build_tools_of_both_machines(published, capsys):
    manifest, dist = published
    (dist / WHEEL).write_bytes(b"wheel byteS")

    verify(manifest, dist)

    err = capsys.readouterr().err
    assert "release machine:" in err
    assert "this runner:" in err
    assert json.dumps(json.loads(manifest.read_text(encoding="utf-8"))["build"], sort_keys=True) in err


def test_an_absent_manifest_fails_and_says_the_tag_was_not_cut_by_the_script(published, capsys):
    manifest, dist = published
    manifest.unlink()

    assert verify(manifest, dist) == 1
    assert "not found on this ref" in capsys.readouterr().err


def test_a_manifest_of_another_schema_fails(published, capsys):
    manifest, dist = published
    data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(data | {"schema": 2}), encoding="utf-8")

    assert verify(manifest, dist) == 1
    assert "has schema 2, expected 1" in capsys.readouterr().err


def test_an_unpublished_extra_file_fails_and_is_named(published, capsys):
    manifest, dist = published
    shutil.copy(dist / SDIST, dist / "pkg-1.0.1.tar.gz")

    assert verify(manifest, dist) == 1
    assert "EXTRA    pkg-1.0.1.tar.gz" in capsys.readouterr().err


def test_a_missing_file_fails_and_is_named(published, capsys):
    manifest, dist = published
    (dist / WHEEL).unlink()

    assert verify(manifest, dist) == 1
    assert f"MISSING  {WHEEL}" in capsys.readouterr().err


# --- the manifest ---------------------------------------------------------------------------------------------


def test_the_manifest_carries_the_epoch_and_every_digest(published):
    manifest, _ = published

    data = json.loads(manifest.read_text(encoding="utf-8"))

    assert data["source_date_epoch"] == 1700000000
    assert {entry["name"] for entry in data["artifacts"]} == {WHEEL, SDIST}
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) for entry in data["artifacts"])


def test_the_manifest_names_the_release_and_the_build_tools(published):
    manifest, _ = published

    data = json.loads(manifest.read_text(encoding="utf-8"))

    assert (data["schema"], data["package"], data["version"], data["tag"]) == (
        1, "pkg", "1.0.0", "v1.0.0",
    )  # fmt: skip
    assert "index" not in data
    assert set(data["build"]) == {"python", "uv", "platform"}


def test_the_manifest_is_written_the_same_way_each_time(published, tmp_path):
    manifest, dist = published
    again = tmp_path / "again.json"

    assert digests.main(write_argv(again, dist)) == 0
    assert again.read_text(encoding="utf-8") == manifest.read_text(encoding="utf-8")
    assert manifest.read_text(encoding="utf-8").endswith("}\n")


def test_nothing_to_record_is_an_error_and_writes_no_manifest(tmp_path, capsys):
    dist = tmp_path / "dist"
    dist.mkdir()
    manifest = tmp_path / MANIFEST_NAME

    assert digests.main(write_argv(manifest, dist)) == 1
    assert "nothing to record" in capsys.readouterr().err
    assert not manifest.exists()


# --- the marker that uv writes --------------------------------------------------------------------------------


def test_write_does_not_record_the_marker_uv_build_leaves_in_dist(published, tmp_path):
    """``uv build`` writes ``dist/.gitignore`` (one byte, ``*``); a file is an artefact only by ``*.whl`` or ``*.tar.gz``."""
    _, dist = published
    (dist / ".gitignore").write_bytes(b"*")
    again = tmp_path / "again.json"

    assert digests.main(write_argv(again, dist)) == 0

    written = json.loads(again.read_text(encoding="utf-8"))
    assert [e["name"] for e in written["artifacts"]] == [WHEEL, SDIST]


def test_a_matching_dist_passes_with_the_marker_present(published):
    manifest, dist = published
    (dist / ".gitignore").write_bytes(b"*")

    assert verify(manifest, dist) == 0


@pytest.mark.parametrize("change", ["flip", "missing", "extra"])
def test_a_real_difference_still_fails_with_the_marker_present(published, change):
    manifest, dist = published
    (dist / ".gitignore").write_bytes(b"*")
    if change == "flip":
        (dist / SDIST).write_bytes(b"sdist byteS")
    elif change == "missing":
        (dist / WHEEL).unlink()
    else:
        shutil.copy(dist / SDIST, dist / "pkg-1.0.1.tar.gz")

    assert verify(manifest, dist) == 1


# --- the command line -----------------------------------------------------------------------------------------


def test_a_missing_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as raised:
        digests.main([])

    assert raised.value.code == 2
    assert "the following arguments are required: command" in capsys.readouterr().err


def test_run_as_a_module_it_exits_with_the_result(published, monkeypatch, capsys):
    manifest, dist = published
    monkeypatch.delitem(sys.modules, "scripts.release.digests")
    monkeypatch.setattr(sys, "argv", ["digests", "verify", "--manifest", str(manifest), "--dist", str(dist)])

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("scripts.release.digests", run_name="__main__")

    assert raised.value.code == 0
    assert "OK: 2 artefact(s) match" in capsys.readouterr().out


# --- the order of the release script --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script():
    return RELEASE_SCRIPT.read_text(encoding="utf-8")


def command(script, pattern):
    """Offset of a real command line, not of a comment that names it."""
    match = re.search(pattern, script, re.MULTILINE)
    assert match is not None, f"no command line matching {pattern!r}"
    return match.start()


def test_the_epoch_is_pinned_before_the_build(script):
    epoch = command(script, r"^\s*SOURCE_DATE_EPOCH=")
    build = command(script, r"^\s*\( cd .* uv build")

    assert epoch < build
    assert "export SOURCE_DATE_EPOCH" in script


def test_the_manifest_is_written_before_publishing(script):
    write = command(script, r"^\s*rf_py digests write")
    publish = command(script, r"^publish_release$")

    assert write < publish, "a manifest written after the upload cannot describe what was uploaded"


def test_the_manifest_is_written_with_the_epoch_that_the_build_used(script):
    write = script[command(script, r"^\s*rf_py digests write") :]

    assert '--epoch "$SOURCE_DATE_EPOCH"' in write.split("commit_release")[0]


def test_the_manifest_joins_the_release_commit(script):
    assert 'commit_release "$CHANGELOG" "$MANIFEST"' in script, (
        "a manifest outside the commit is unreachable from the tag"
    )


def test_the_workflow_takes_the_epoch_from_the_manifest_not_from_a_commit_time():
    """The tagged commit is made after the build, so its time is another number than the build clock."""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "git log -1 --pretty=%ct" not in text
    assert MANIFEST_NAME in text


def test_a_toolchain_version_that_cannot_be_read_is_unknown(tmp_path):
    missing = str(tmp_path / "no-such-tool")

    assert digests._tool_version(missing, "--version") == "unknown"


def test_a_tool_that_prints_nothing_has_an_unknown_version():
    assert digests._tool_version(sys.executable, "-c", "pass") == "unknown"


def test_a_tool_version_is_the_first_line_of_its_output():
    out = digests._tool_version(sys.executable, "-c", "print('one'); print('two')")

    assert out == "one"
