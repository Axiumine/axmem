"""How the engine cache judges, discards, stores and prunes its entries, and what the operator reads in the log about it."""
import json
import logging
import os

from app.repositories.embeddings import tensorrt_cache
from app.repositories.embeddings.tensorrt_cache import FINGERPRINT_NAME, PLAN_NAME
from tests.tensorrt_cache_support import CURRENT, LOGGER, NEW, OLD, OLDER


def entry(group, name, content, age):
    """An entry of the cache, ``age`` seconds after the epoch (the newest is the one the rebuild is judged against)."""
    directory = group / name
    directory.mkdir()
    (directory / FINGERPRINT_NAME).write_text(content if isinstance(content, str) else json.dumps(content))
    os.utime(directory, (age, age))
    return directory


def test_a_rebuild_with_nothing_to_compare_with_says_so(tmp_path):
    (tmp_path / ".build-1").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / FINGERPRINT_NAME).write_text(json.dumps(CURRENT))
    entry(tmp_path, OLD, "[1]", 10)  # a fingerprint that is not an object tells nothing
    entry(tmp_path, NEW, "{ no json", 20)

    assert tensorrt_cache._describe_change(tmp_path, CURRENT) == "no cached engine"


def test_a_rebuild_names_each_part_of_the_fingerprint_that_changed_in_its_order(tmp_path):
    previous = {**CURRENT, "gpu": "Another GPU", "tf32": True, "profile": {"min": [1, 4]}}
    del previous["tensorrt"]
    entry(tmp_path, OLD, previous, 10)

    assert tensorrt_cache._describe_change(tmp_path, CURRENT) == (
        "fingerprint changed: tensorrt (None -> 11.4), gpu (Another GPU -> NVIDIA Test GPU), tf32 (True -> False), "
        "profile ({'min': [1, 4]} -> {'min': [1, 2]})"
    )


def test_a_rebuild_shortens_what_it_quotes_to_forty_characters(tmp_path):
    entry(tmp_path, OLD, {**CURRENT, "model_sha256": "1" * 64}, 10)

    assert tensorrt_cache._describe_change(tmp_path, {**CURRENT, "model_sha256": "2" * 64}) == (
        f"fingerprint changed: model_sha256 ({'1' * 40} -> {'2' * 40})"
    )


def test_a_rebuild_is_judged_against_the_newest_entry_that_can_be_read(tmp_path):
    entry(tmp_path, NEW, {**CURRENT, "tf32": True}, 20)  # sorts last by name, but is the middle one by age
    entry(tmp_path, OLD, {**CURRENT, "gpu": "Oldest GPU"}, 10)
    entry(tmp_path, OLDER, "[1]", 30)  # the newest, but it says nothing

    assert tensorrt_cache._describe_change(tmp_path, CURRENT) == "fingerprint changed: tf32 (True -> False)"


def test_a_rebuild_is_judged_against_the_newest_entry_by_age_not_by_name(tmp_path):
    entry(tmp_path, OLD, {**CURRENT, "gpu": "Newest GPU"}, 20)
    entry(tmp_path, NEW, {**CURRENT, "gpu": "Oldest GPU"}, 10)

    assert tensorrt_cache._describe_change(tmp_path, CURRENT) == "fingerprint changed: gpu (Newest GPU -> NVIDIA Test GPU)"


def test_an_entry_is_discarded_with_a_warning_that_names_it_and_why(tmp_path, caplog):
    directory = entry(tmp_path, OLD, "{}", 10)
    (directory / PLAN_NAME).write_bytes(b"plan")

    tensorrt_cache._discard(directory, ValueError("because"))

    assert not directory.exists()
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [
        (logging.WARNING, f"Discarding the cached TensorRT engine {OLD}: because"),
    ]


def test_an_entry_is_stored_in_a_scratch_directory_that_is_moved_into_place(tmp_path, monkeypatch):
    moves = []
    replace = os.replace

    def noting(source, target):
        moves.append((source, target))
        replace(source, target)

    monkeypatch.setattr(tensorrt_cache.os, "replace", noting)

    final = tensorrt_cache._store(tmp_path, NEW, {"tf32": True, "gpu": "G"}, b"plan bytes")

    assert final == tmp_path / NEW
    assert len(moves) == 1
    assert moves[0][0].parent == tmp_path
    assert moves[0][0].name.startswith(".build-")
    assert moves[0][1] == final
    assert sorted(path.name for path in tmp_path.iterdir()) == [NEW]  # the scratch directory is gone
    assert (final / PLAN_NAME).read_bytes() == b"plan bytes"
    assert (final / FINGERPRINT_NAME).read_text() == '{\n  "gpu": "G",\n  "tf32": true\n}'


def test_pruning_removes_the_other_engines_and_the_abandoned_builds_and_says_which_in_order(tmp_path, caplog):
    kept = entry(tmp_path, NEW, "{}", 10)
    entry(tmp_path, OLDER, "{}", 20)
    entry(tmp_path, OLD, "{}", 30)
    (tmp_path / ".build-b").mkdir()
    (tmp_path / ".build-a").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / ".lock").write_text("")
    caplog.set_level(logging.INFO, logger=LOGGER)

    removed = tensorrt_cache._prune(tmp_path, keep=NEW)

    assert removed == [".build-a", ".build-b", OLD, OLDER]
    assert sorted(path.name for path in tmp_path.iterdir()) == [".lock", NEW, "notes"]
    assert kept.is_dir()
    assert f"Pruned stale TensorRT engine directories: .build-a, .build-b, {OLD}, {OLDER}" in caplog.messages


def test_pruning_with_nothing_to_remove_says_nothing(tmp_path, caplog):
    entry(tmp_path, NEW, "{}", 10)
    caplog.set_level(logging.INFO, logger=LOGGER)

    assert tensorrt_cache._prune(tmp_path, keep=NEW) == []
    assert [r for r in caplog.records if r.name == LOGGER] == []
