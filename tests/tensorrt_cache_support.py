# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Doubles and helpers for the tests of the TensorRT engine cache: the names of an entry, and the recorders that stand in for a build."""
import json
from pathlib import Path

from app.repositories.embeddings import tensorrt_cache
from app.repositories.embeddings.tensorrt_cache import (
    FINGERPRINT_NAME,
    PLAN_NAME,
    OpenedEngine,
    fingerprint,
    fingerprint_key,
    open_engine,
)
from app.repositories.embeddings.tensorrt_reranker import PROFILE
from app.repositories.embeddings.tensorrt_runtime import GpuInfo, TensorRTEngine

LOGGER = "app.repositories.embeddings.tensorrt_cache"
GPU = GpuInfo(name="NVIDIA Test GPU", compute_capability="8.6", driver_version="580.65.06", total_bytes=24 * 1024**3)
SHA = "ab" * 32
ONNX = Path("/models/model.onnx")
GROUP = "group"
CURRENT = {"model_sha256": SHA, "tensorrt": "11.4", "gpu": "NVIDIA Test GPU", "tf32": False, "profile": {"min": [1, 2]}}
OLD, NEW, OLDER = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"


class Builds:
    """``build_plan`` replaced by a recorder that hands out an intact plan: the build is not under test here."""

    def __init__(self, monkeypatch, plan, during=None):
        self.calls, self.plan, self.during = [], plan, during
        monkeypatch.setattr(tensorrt_cache, "build_plan", self)

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.during is not None:
            self.during()
        return self.plan


class Engines:
    """The real ``TensorRTEngine`` class, noting every engine the cache creates."""

    def __init__(self, monkeypatch):
        self.created: list[TensorRTEngine] = []
        created = self.created

        class Recording(TensorRTEngine):
            def __init__(self, plan, profile, *, contexts=1):
                super().__init__(plan, profile, contexts=contexts)
                created.append(self)

        monkeypatch.setattr(tensorrt_cache, "TensorRTEngine", Recording)


def open_cached(cache_dir, **overrides) -> OpenedEngine:
    arguments = {
        "onnx_path": ONNX, "model_sha256": SHA, "group_name": GROUP, "cache_dir": cache_dir, "gpu": GPU, "tf32": False,
        "profile": PROFILE, "contexts": 1,
    }
    return open_engine(**{**arguments, **overrides})


def current(**changes):
    return fingerprint(**{"model_sha256": SHA, "gpu": GPU, "tf32": False, "profile": PROFILE, **changes})


def seed(cache_dir, plan_bytes, **changes):
    """The entry that ``open_cached`` finds, holding ``plan_bytes``."""
    values = current(**changes)
    directory = cache_dir / GROUP / fingerprint_key(values)
    directory.mkdir(parents=True)
    (directory / PLAN_NAME).write_bytes(plan_bytes)
    (directory / FINGERPRINT_NAME).write_text(json.dumps(values, sort_keys=True, indent=2))
    return directory
