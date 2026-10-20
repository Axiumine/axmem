"""Engine cache on the real GPU: build, cache hit, fingerprint change, pruning and damaged entries.

Every engine here is built from the real model by TensorRT into a temporary cache directory.
The first cold build is shared by the tests of the module; the others copy that cache first.
"""
import json
import logging
import shutil

import pytest

from app.config.settings import settings
from app.repositories.embeddings.reranker_model import fetch_model
from app.repositories.embeddings.tensorrt_cache import (
    FINGERPRINT_NAME,
    LOCK_NAME,
    PLAN_NAME,
    fingerprint,
    fingerprint_key,
    open_engine,
)
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import OptimizationProfile, probe_gpu

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
    assert opened.reason == "the cached engine was damaged or rejected by TensorRT"
    assert "Discarding the cached TensorRT engine" in caplog.text
    assert json.loads((entry / FINGERPRINT_NAME).read_text())["model_sha256"] == model.onnx_sha256


def test_a_plan_that_tensorrt_rejects_is_replaced_by_a_fresh_build(model, cache_copy):
    entry = cache_copy / GROUP / current_key(model)
    plan = entry / PLAN_NAME
    plan.write_bytes(plan.read_bytes()[:4096])

    opened = open_cached(model, cache_copy)
    opened.engine.close()

    assert not opened.cache_hit
    assert "damaged or rejected" in opened.reason
    assert plan.stat().st_size > 1_000_000
    again = open_cached(model, cache_copy)
    again.engine.close()
    assert again.cache_hit
