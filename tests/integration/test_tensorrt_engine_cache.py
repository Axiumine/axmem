"""Engine cache on the real GPU: build, cache hit, fingerprint change, pruning and damaged entries.

Every engine here is built from the real model by TensorRT into a temporary cache directory.
The first cold build is shared by the tests of the module; the others copy that cache first.
"""
import json
import logging
import shutil

import pytest

from app.config.settings import settings
from app.exceptions import TensorRTStartupError
from app.repositories.embeddings import tensorrt_cache
from app.repositories.embeddings.tensorrt_cache import (
    FINGERPRINT_NAME,
    LOCK_NAME,
    PLAN_NAME,
    fingerprint,
    fingerprint_key,
    open_engine,
)
from app.repositories.embeddings.tensorrt_model import fetch_model
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import (
    OptimizationProfile,
    PlanRejectedError,
    TensorRTEngine,
    probe_gpu,
)

GROUP = "model-group"


@pytest.fixture(scope="module")
def model(tensorrt_reranker):
    return fetch_model(settings.RERANKING_MODEL, settings.RERANKING_MODEL_DIR)


def open_cached(model, cache_dir, *, tf32=False, profile=PROFILE):
    return open_engine(
        onnx_path=model.onnx,
        model_sha256=model.onnx_sha256,
        group_name=GROUP,
        cache_dir=cache_dir,
        gpu=probe_gpu(),
        tf32=tf32,
        profile=profile,
        contexts=1,
    )


@pytest.fixture(scope="module")
def cold_build(model, tmp_path_factory):
    """The first open of an empty cache: a real build. Returns (cache_dir, what open_engine reported)."""
    cache_dir = tmp_path_factory.mktemp("engines")
    opened = open_cached(model, cache_dir)
    opened.engine.close()
    return cache_dir, opened


@pytest.fixture
def cache_copy(cold_build, tmp_path):
    """A private copy of the cold build, free to be damaged or extended."""
    cache_dir = tmp_path / "engines"
    shutil.copytree(cold_build[0], cache_dir)
    return cache_dir


def current_key(model, *, tf32=False):
    values = fingerprint(model_sha256=model.onnx_sha256, gpu=probe_gpu(), tf32=tf32, profile=PROFILE)
    return fingerprint_key(values)


def test_a_cold_cache_builds_into_a_directory_named_by_the_fingerprint(model, cold_build):
    cache_dir, opened = cold_build

    assert not opened.cache_hit
    assert opened.reason == "no cached engine"
    assert opened.directory == cache_dir / GROUP / current_key(model)
    assert (opened.directory / PLAN_NAME).stat().st_size > 1_000_000
    assert (cache_dir / GROUP / LOCK_NAME).is_file()


def test_the_stored_fingerprint_records_what_the_engine_depends_on(model, cold_build):
    _cache_dir, opened = cold_build
    stored = json.loads((opened.directory / FINGERPRINT_NAME).read_text())
    gpu = probe_gpu()
    assert stored["model_sha256"] == model.onnx_sha256
    assert stored["gpu"] == gpu.name
    assert stored["compute_capability"] == gpu.compute_capability
    assert stored["driver_major"] == gpu.driver_major
    assert stored["tf32"] is False
    assert stored["profile"] == PROFILE.describe()


def test_the_second_open_is_a_cache_hit_that_does_not_build(model, cold_build, caplog):
    cache_dir, cold = cold_build
    caplog.set_level(logging.INFO)

    opened = open_cached(model, cache_dir)
    opened.engine.close()

    assert opened.cache_hit
    assert opened.reason == "cache hit"
    assert opened.directory == cold.directory
    assert opened.seconds < cold.seconds
    assert "Building the TensorRT engine" not in caplog.text


def test_a_changed_fingerprint_builds_a_new_directory_and_prunes_the_old_one(model, cache_copy, caplog):
    caplog.set_level(logging.INFO)
    old_key = current_key(model)

    opened = open_cached(model, cache_copy, tf32=True)
    opened.engine.close()

    new_key = current_key(model, tf32=True)
    assert new_key != old_key
    assert not opened.cache_hit
    assert opened.directory == cache_copy / GROUP / new_key
    assert "fingerprint changed: tf32 (False -> True)" in opened.reason
    assert sorted(p.name for p in (cache_copy / GROUP).iterdir() if p.is_dir()) == [new_key]
    assert f"Pruned stale TensorRT engine directories: {old_key}" in caplog.text


def test_every_component_of_the_fingerprint_changes_the_directory(model):
    gpu = probe_gpu()
    base = fingerprint(model_sha256=model.onnx_sha256, gpu=gpu, tf32=False, profile=PROFILE)
    variants = {
        "model_sha256": fingerprint(model_sha256="0" * 64, gpu=gpu, tf32=False, profile=PROFILE),
        "tf32": fingerprint(model_sha256=model.onnx_sha256, gpu=gpu, tf32=True, profile=PROFILE),
        "profile": fingerprint(
            model_sha256=model.onnx_sha256, gpu=gpu, tf32=False,
            profile=OptimizationProfile((1, 2), (20, 336), (32, 512)),
        ),
    }

    for name, other in variants.items():
        assert other[name] != base[name]
        assert fingerprint_key(other) != fingerprint_key(base)
    assert set(base) == {
        "model_sha256", "tensorrt", "gpu", "compute_capability", "driver_major", "tf32", "profile",
    }


def test_pruning_removes_other_engines_and_abandoned_builds_but_leaves_foreign_files(
    model, cache_copy, caplog,
):
    group = cache_copy / GROUP
    stale = group / "fedcba9876543210"
    stale.mkdir()
    (stale / FINGERPRINT_NAME).write_text("{}")
    abandoned = group / ".build-abandoned"
    abandoned.mkdir()
    (abandoned / PLAN_NAME).write_bytes(b"partial")
    unfinished = group / "0123456789abcdef"  # looks like an engine directory but has no fingerprint
    unfinished.mkdir()
    foreign = group / "notes"
    foreign.mkdir()
    (group / "readme.txt").write_text("kept")
    caplog.set_level(logging.INFO)

    opened = open_cached(model, cache_copy)
    opened.engine.close()

    assert opened.cache_hit
    assert not stale.exists()
    assert not abandoned.exists()
    assert unfinished.is_dir()
    assert foreign.is_dir()
    assert (group / "readme.txt").read_text() == "kept"
    assert "Pruned stale TensorRT engine directories: .build-abandoned, fedcba9876543210" in caplog.text


def test_an_unreadable_fingerprint_rebuilds_the_engine(model, cache_copy, caplog):
    entry = cache_copy / GROUP / current_key(model)
    (entry / FINGERPRINT_NAME).write_text("{ this is not json")
    caplog.set_level(logging.INFO)

    opened = open_cached(model, cache_copy)
    opened.engine.close()

    assert not opened.cache_hit
    assert opened.reason == "the cached engine was damaged"
    assert "Discarding the cached TensorRT engine" in caplog.text
    assert json.loads((entry / FINGERPRINT_NAME).read_text())["model_sha256"] == model.onnx_sha256


def test_an_entry_without_its_plan_is_damaged_and_rebuilt(model, cache_copy, caplog):
    entry = cache_copy / GROUP / current_key(model)
    (entry / PLAN_NAME).unlink()
    caplog.set_level(logging.INFO)

    opened = open_cached(model, cache_copy)
    opened.engine.close()

    assert not opened.cache_hit
    assert opened.reason == "the cached engine was damaged"
    assert "its fingerprint or its plan is missing or different" in caplog.text
    assert (entry / PLAN_NAME).stat().st_size > 1_000_000


def test_a_plan_that_tensorrt_rejects_is_replaced_once_a_fresh_build_loads(model, cache_copy, caplog):
    """A truncated plan: a plan built a moment ago from the same model loads here, so the stored file is the problem."""
    entry = cache_copy / GROUP / current_key(model)
    plan = entry / PLAN_NAME
    plan.write_bytes(plan.read_bytes()[:4096])
    caplog.set_level(logging.INFO)

    opened = open_cached(model, cache_copy)
    opened.engine.close()

    assert not opened.cache_hit
    assert opened.reason == "TensorRT rejected the cached engine"
    assert "TensorRT rejected the cached engine" in caplog.text
    assert "a plan built a moment ago from the same model loads" in caplog.text
    assert plan.stat().st_size > 1_000_000
    again = open_cached(model, cache_copy)
    again.engine.close()
    assert again.cache_hit


class RecordedEngines:
    """The real ``TensorRTEngine`` class, noting every engine the cache creates so that a test can see what became of it."""

    def __init__(self, monkeypatch, *, refuse=False):
        self.created: list[TensorRTEngine] = []
        outer = self

        class Recording(TensorRTEngine):
            def __init__(self, plan, profile, *, contexts=1):
                if refuse:
                    raise PlanRejectedError(
                        "engine-unusable",
                        "TensorRT cannot deserialize the plan: Cuda Error in allocate: 2 (out of memory)",
                    )
                super().__init__(plan, profile, contexts=contexts)
                outer.created.append(self)

        monkeypatch.setattr(tensorrt_cache, "TensorRTEngine", Recording)


def test_a_cache_directory_that_cannot_be_created_is_a_startup_error(model, tmp_path):
    """The cache setting points below a file: say so, with the cause and the setting to change."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where the cache directory should be")

    with pytest.raises(TensorRTStartupError, match=r"the engine cache .* cannot be used .*cache\s+directory setting") as raised:
        open_cached(model, blocker / "engines")

    assert raised.value.cause == "engine-unusable"


def test_a_cache_directory_that_cannot_be_written_is_a_startup_error(model, cache_copy, monkeypatch):
    """A cache that holds the engine reads it, but removing a stale sibling fails: still a cause, not a traceback."""
    group = cache_copy / GROUP
    (group / ("0" * 16)).mkdir()
    (group / ("0" * 16) / FINGERPRINT_NAME).write_text("{}")

    def refuse(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("app.repositories.embeddings.tensorrt_cache.shutil.rmtree", refuse)
    engines = RecordedEngines(monkeypatch)

    with pytest.raises(TensorRTStartupError, match="cannot be used.*Permission denied") as raised:
        open_cached(model, cache_copy)

    assert raised.value.cause == "engine-unusable"
    assert [engine._closed for engine in engines.created] == [True]  # the engine it had loaded is released


def test_a_plan_the_gpu_cannot_hold_is_not_a_damaged_file_and_stays_in_the_cache(model, cache_copy, monkeypatch, caplog):
    """The GPU is too full for any engine: TensorRT refuses the intact plan and one built a moment ago alike."""
    entry = cache_copy / GROUP / current_key(model)
    plan = entry / PLAN_NAME
    intact = plan.read_bytes()
    monkeypatch.setattr(tensorrt_cache, "build_plan", lambda *args, **kwargs: intact)  # the build is not under test
    RecordedEngines(monkeypatch, refuse=True)
    caplog.set_level(logging.INFO)

    with pytest.raises(TensorRTStartupError, match=r"the cached file is not at fault and was kept.*free memory") as raised:
        open_cached(model, cache_copy)

    assert raised.value.cause == "engine-unusable"
    assert "out of memory" in str(raised.value)
    assert plan.read_bytes() == intact
    assert (entry / FINGERPRINT_NAME).is_file()
    assert "Discarding the cached TensorRT engine" not in caplog.text
    assert "TensorRT rejected the cached engine" in caplog.text


def test_a_plan_built_just_now_that_tensorrt_rejects_is_not_cached(model, tmp_path, monkeypatch):
    monkeypatch.setattr(tensorrt_cache, "build_plan", lambda *args, **kwargs: b"not a plan")

    with pytest.raises(PlanRejectedError) as raised:
        open_cached(model, tmp_path / "engines")

    assert raised.value.cause == "engine-unusable"
    assert not list((tmp_path / "engines" / GROUP).glob("*/" + PLAN_NAME))  # nothing was stored that cannot load


def test_a_plan_that_cannot_be_read_is_not_judged_damaged(model, cache_copy, monkeypatch):
    """A read that fails (permission, I/O error) says nothing about the file: the entry stays and the cache is the cause."""
    entry = cache_copy / GROUP / current_key(model)
    intact = (entry / PLAN_NAME).read_bytes()
    original = tensorrt_cache.Path.read_bytes

    def unreadable(path):
        if path.name == PLAN_NAME:
            raise PermissionError(13, "Permission denied")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(tensorrt_cache.Path, "read_bytes", unreadable)
        with pytest.raises(TensorRTStartupError, match="cannot be used.*Permission denied") as raised:
            open_cached(model, cache_copy)

    assert raised.value.cause == "engine-unusable"
    assert (entry / PLAN_NAME).read_bytes() == intact


def test_an_engine_is_released_when_the_cache_cannot_store_it(model, cold_build, tmp_path, monkeypatch):
    """The plan loads but cannot be written: the start is refused and the engine does not stay in GPU memory."""
    plan = (cold_build[1].directory / PLAN_NAME).read_bytes()
    monkeypatch.setattr(tensorrt_cache, "build_plan", lambda *args, **kwargs: plan)  # the build is not under test

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tensorrt_cache, "_store", disk_full)
    engines = RecordedEngines(monkeypatch)

    with pytest.raises(TensorRTStartupError, match="cannot be used.*No space left on device"):
        open_cached(model, tmp_path / "engines")

    assert len(engines.created) == 1
    assert engines.created[0]._closed
