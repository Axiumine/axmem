"""The contract of the engine cache that a directory alone decides: the fingerprint and the directory it names, the lock,
and which directories are cache entries.
"""
import fcntl

import pytest

from app.repositories.embeddings import tensorrt_cache
from app.repositories.embeddings.tensorrt_cache import (
    FINGERPRINT_NAME,
    LOCK_NAME,
    PLAN_NAME,
    fingerprint,
    fingerprint_key,
)
from app.repositories.embeddings.tensorrt_runtime import (
    OptimizationProfile,
    tensorrt_version,
)
from tests.tensorrt_cache_support import GPU, NEW, OLD, SHA


def test_the_files_of_a_cache_entry_have_their_documented_names():
    assert (PLAN_NAME, FINGERPRINT_NAME, LOCK_NAME) == ("engine.plan", "fingerprint.json", ".lock")


def test_the_fingerprint_holds_everything_the_plan_depends_on():
    profile = OptimizationProfile((1, 2), (16, 128), (64, 512))

    assert fingerprint(model_sha256=SHA, gpu=GPU, tf32=True, profile=profile) == {
        "model_sha256": SHA,
        "tensorrt": tensorrt_version(),
        "gpu": "NVIDIA Test GPU",
        "compute_capability": "8.6",
        "driver_major": 580,
        "tf32": True,
        "profile": {"min": [1, 2], "opt": [16, 128], "max": [64, 512]},
    }


def test_the_directory_of_a_fingerprint_keeps_its_name_between_versions():
    """A cache directory is named once and found again by every later start: a change of the name drops every cache."""
    values = {"b": 1, "a": [1, 2], "tf32": False}

    assert fingerprint_key(values) == "646e6d9ecffdbf9f"
    assert fingerprint_key(dict(reversed(values.items()))) == "646e6d9ecffdbf9f"  # the order of the keys is not part of it
    assert fingerprint_key({**values, "tf32": True}) != "646e6d9ecffdbf9f"


def test_a_lock_is_exclusive_while_held_and_free_afterwards(tmp_path):
    path = tmp_path / LOCK_NAME

    with tensorrt_cache._locked(path), path.open("a") as other:
        for mode in (fcntl.LOCK_EX, fcntl.LOCK_SH):  # a reader waits for a builder too
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, mode | fcntl.LOCK_NB)

    with path.open("a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again


def test_a_lock_file_is_made_when_it_is_not_there_and_kept_when_it_is(tmp_path):
    path = tmp_path / LOCK_NAME
    with tensorrt_cache._locked(path):
        pass
    assert path.read_text() == ""
    path.write_text("kept")

    with tensorrt_cache._locked(path):
        pass

    assert path.read_text() == "kept"


def test_the_stored_fingerprint_is_whatever_json_the_file_holds(tmp_path):
    (tmp_path / FINGERPRINT_NAME).write_text('{"tf32": true}')

    assert tensorrt_cache._stored_fingerprint(tmp_path) == {"tf32": True}

    (tmp_path / FINGERPRINT_NAME).write_text("[1, 2]")

    assert tensorrt_cache._stored_fingerprint(tmp_path) == [1, 2]


@pytest.mark.parametrize("content", [None, "", "{ this is not json", "nul"])
def test_a_fingerprint_that_is_missing_or_not_json_is_none(tmp_path, content):
    if content is not None:
        (tmp_path / FINGERPRINT_NAME).write_text(content)

    assert tensorrt_cache._stored_fingerprint(tmp_path) is None


def test_a_fingerprint_that_cannot_be_read_is_not_taken_for_a_missing_one(tmp_path):
    (tmp_path / FINGERPRINT_NAME).mkdir()  # reading a directory fails with an OSError that is not "no such file"

    with pytest.raises(IsADirectoryError):
        tensorrt_cache._stored_fingerprint(tmp_path)
    assert tensorrt_cache._described_fingerprint(tmp_path) is None  # the pruning log only goes without a word about it


def test_a_fingerprint_that_is_there_is_described(tmp_path):
    (tmp_path / FINGERPRINT_NAME).write_text('{"tf32": true}')

    assert tensorrt_cache._described_fingerprint(tmp_path) == {"tf32": True}


def test_only_a_directory_with_the_prefix_of_a_build_is_an_abandoned_build(tmp_path):
    for name in (".build-1", ".build-"):
        (tmp_path / name).mkdir()
    for name in ("build-1", ".builds", "x.build-1", ".buil-1"):
        (tmp_path / name).mkdir()
    (tmp_path / ".build-file").write_text("a file")

    found = sorted(path.name for path in tmp_path.iterdir() if tensorrt_cache._is_build_dir(path))

    assert found == [".build-", ".build-1"]


@pytest.mark.parametrize(
    ("name", "fingerprinted", "is_engine"),
    [
        ("0123456789abcdef", True, True),
        ("fedcba9876543210", True, True),
        ("0123456789abcdef", False, False),  # no fingerprint: not an engine
        ("0123456789abcde", True, False),  # 15 characters
        ("0123456789abcdef0", True, False),  # 17 characters
        ("0123456789ABCDEF", True, False),  # upper case
        ("0123456789abcdeg", True, False),
        ("x0123456789abcdef", True, False),
        (".build-0123456789a", True, False),
    ],
)
def test_an_engine_directory_has_a_key_for_a_name_and_a_fingerprint(tmp_path, name, fingerprinted, is_engine):
    directory = tmp_path / name
    directory.mkdir()
    if fingerprinted:
        (directory / FINGERPRINT_NAME).write_text("{}")

    assert tensorrt_cache._is_engine_dir(directory) is is_engine


def test_a_file_is_not_an_engine_directory_and_a_fingerprint_directory_does_not_make_one(tmp_path):
    (tmp_path / OLD).write_text("a file")
    (tmp_path / NEW).mkdir()
    (tmp_path / NEW / FINGERPRINT_NAME).mkdir()

    assert not tensorrt_cache._is_engine_dir(tmp_path / OLD)
    assert not tensorrt_cache._is_engine_dir(tmp_path / NEW)
