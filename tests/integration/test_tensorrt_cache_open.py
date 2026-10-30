# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""``open_engine`` with the real engine class on the real GPU: a cold and a warm cache, the lock, a plan that is rejected,
and a cache directory that cannot be used. Only the build is replaced (by ``Builds``), because a build costs half a minute.
"""
import fcntl
import json
import logging
import re
import time
from types import SimpleNamespace

import pytest

from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_cache
from app.repositories.embeddings.tensorrt_cache import (
    FINGERPRINT_NAME,
    LOCK_NAME,
    PLAN_NAME,
    fingerprint_key,
)
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import (
    PlanRejectedError,
    TensorRTEngine,
)
from tests.tensorrt_cache_support import (
    GROUP,
    LOGGER,
    ONNX,
    Builds,
    Engines,
    current,
    open_cached,
    seed,
)


@pytest.fixture(scope="module")
def plan(tensorrt_reranker):
    """The plan of the session engine, which loads on this GPU for the profile of the reranker."""
    return (tensorrt_reranker.startup.engine_dir / PLAN_NAME).read_bytes()


@pytest.fixture
def cold_open(tmp_path, plan, monkeypatch, caplog):
    """A first start on a cache directory that is not there yet: what was built, what was opened, and how long the call took."""
    builds = Builds(monkeypatch, plan)
    engines = Engines(monkeypatch)
    caplog.set_level(logging.INFO, logger=LOGGER)
    cache_dir = tmp_path / "deeper" / "engines"
    started = time.perf_counter()

    opened = open_cached(cache_dir, tf32=True, contexts=2)

    elapsed = time.perf_counter() - started
    opened.engine.close()
    key = fingerprint_key(current(tf32=True))
    return SimpleNamespace(
        builds=builds, engines=engines, opened=opened, elapsed=elapsed, group=cache_dir / GROUP, key=key, messages=caplog.messages,
    )


def test_a_cold_cache_builds_once_and_reports_what_it_opened(cold_open):
    opened = cold_open.opened

    assert cold_open.builds.calls == [((ONNX, PROFILE), {"tf32": True})]
    assert opened.directory == cold_open.group / cold_open.key
    assert (opened.cache_hit, opened.reason) == (False, "no cached engine")
    assert 0 < opened.seconds <= cold_open.elapsed
    assert opened.engine is cold_open.engines.created[0]
    assert opened.engine._size == 2


def test_a_cold_cache_stores_the_plan_and_its_fingerprint_and_says_that_it_builds(cold_open, plan):
    directory = cold_open.opened.directory

    assert (directory / PLAN_NAME).read_bytes() == plan
    assert json.loads((directory / FINGERPRINT_NAME).read_text()) == current(tf32=True)
    assert sorted(path.name for path in cold_open.group.iterdir()) == [LOCK_NAME, cold_open.key]
    assert "Building the TensorRT engine (no cached engine); this takes several seconds" in cold_open.messages


def test_a_warm_cache_loads_the_stored_plan_without_building(tmp_path, plan, monkeypatch, caplog):
    builds = Builds(monkeypatch, plan)
    directory = seed(tmp_path, plan)
    caplog.set_level(logging.INFO, logger=LOGGER)

    opened = open_cached(tmp_path, contexts=3)
    opened.engine.close()

    assert builds.calls == []
    assert opened.directory == directory
    assert (opened.cache_hit, opened.reason) == (True, "cache hit")
    assert opened.seconds > 0
    assert opened.engine._size == 3
    assert [message for message in caplog.messages if message.startswith("Building")] == []


def test_the_group_is_locked_for_a_build_and_free_again_afterwards(tmp_path, plan, monkeypatch):
    lock = tmp_path / GROUP / LOCK_NAME
    states = []

    def try_the_lock():
        with lock.open("a") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                states.append("held")
            else:
                states.append("free")

    Builds(monkeypatch, plan, during=try_the_lock)

    open_cached(tmp_path).engine.close()
    try_the_lock()

    assert states == ["held", "free"]


def test_a_stored_plan_that_tensorrt_rejects_is_replaced_by_one_that_loads(tmp_path, plan, monkeypatch, caplog):
    builds = Builds(monkeypatch, plan)
    directory = seed(tmp_path, b"damaged")
    key = directory.name
    caplog.set_level(logging.INFO, logger=LOGGER)

    opened = open_cached(tmp_path)
    opened.engine.close()

    assert len(builds.calls) == 1
    assert (opened.cache_hit, opened.reason, opened.directory) == (False, "TensorRT rejected the cached engine", directory)
    assert (directory / PLAN_NAME).read_bytes() == plan
    rejected = re.compile(
        rf"TensorRT rejected the cached engine {key} \(TensorRT cannot deserialize the plan: .*\); building a new one to tell "
        "a damaged file from a GPU that cannot hold the engine",
        re.DOTALL,
    )
    discarded = re.compile(
        rf"Discarding the cached TensorRT engine {key}: TensorRT cannot deserialize the plan: .*; "
        "a plan built a moment ago from the same model loads",
        re.DOTALL,
    )
    assert [bool(rejected.fullmatch(m)) for m in caplog.messages if m.startswith("TensorRT rejected")] == [True]
    assert [bool(discarded.fullmatch(m)) for m in caplog.messages if m.startswith("Discarding")] == [True]
    assert "Building the TensorRT engine (TensorRT rejected the cached engine); this takes several seconds" in caplog.messages


def test_a_gpu_that_cannot_hold_any_engine_keeps_the_stored_plan_and_names_its_directory(tmp_path, plan, monkeypatch):
    """TensorRT refuses the stored plan and the one built a moment ago alike: the file is not at fault."""
    Builds(monkeypatch, plan)
    directory = seed(tmp_path, plan)

    class Refusing(TensorRTEngine):
        def __init__(self, *args, **kwargs):
            raise PlanRejectedError("engine-unusable", "TensorRT cannot deserialize the plan: out of memory")

    monkeypatch.setattr(tensorrt_cache, "TensorRTEngine", Refusing)

    with pytest.raises(TensorRTStartupError) as raised:
        open_cached(tmp_path)

    assert type(raised.value) is TensorRTStartupError
    assert raised.value.cause == "engine-unusable"
    assert str(raised.value) == (
        f"TensorRT rejects the cached engine {directory.name} and an engine built a moment ago from the same model alike "
        "(TensorRT cannot deserialize the plan: out of memory), so the cached file is not at fault and was kept. Check that "
        "the GPU has the free memory the engine needs and that the driver suits TensorRT (see docs/gpu-models.md)."
    )
    assert (directory / PLAN_NAME).read_bytes() == plan


def test_an_engine_is_released_when_the_start_is_interrupted_after_it_loaded(tmp_path, plan, monkeypatch):
    """``KeyboardInterrupt`` is not an ``Exception``: the release must not depend on the kind of failure."""
    Builds(monkeypatch, plan)
    engines = Engines(monkeypatch)

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(tensorrt_cache, "_store", interrupted)

    with pytest.raises(KeyboardInterrupt):
        open_cached(tmp_path)

    assert [engine._closed for engine in engines.created] == [True]


def test_a_cache_directory_that_cannot_be_used_is_named_with_the_cause_and_the_setting_to_change(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("a file where a directory should be")

    with pytest.raises(TensorRTStartupError) as raised:
        open_cached(blocker / "engines")

    cause = raised.value.__cause__
    assert isinstance(cause, NotADirectoryError)
    assert raised.value.cause == "engine-unusable"
    assert str(raised.value) == (
        f"the engine cache {blocker / 'engines' / GROUP} cannot be used ({type(cause).__name__}: {cause}). "
        "Point the engine cache directory setting of this model at a directory the process can write."
    )
