"""Engine cache: one directory per fingerprint of everything a TensorRT plan depends on.

A plan is only valid for the model file, TensorRT version, GPU model and compute capability,
driver major version, TF32 flag and optimisation profile it was built with, so all of them go
into the fingerprint and the fingerprint names the directory. Plans are not portable between
machines: the cache directory is local state, never something to copy or share.

The cache is also self-cleaning. After an engine is ready, every sibling directory that holds an
engine for another fingerprint (and every abandoned build directory) is removed and the removal
is logged.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.repositories.embeddings.tensorrt_runtime import (
    GpuInfo,
    OptimizationProfile,
    PlanRejectedError,
    TensorRTEngine,
    build_plan,
    tensorrt_version,
)

logger = logging.getLogger(__name__)

PLAN_NAME = "engine.plan"
FINGERPRINT_NAME = "fingerprint.json"
LOCK_NAME = ".lock"
_BUILD_PREFIX = ".build-"
_KEY = re.compile(r"[0-9a-f]{16}")


@dataclass(frozen=True)
class OpenedEngine:
    """A ready engine and how it came to be."""

    engine: TensorRTEngine
    directory: Path
    cache_hit: bool
    reason: str
    seconds: float


def fingerprint(*, model_sha256: str, gpu: GpuInfo, tf32: bool, profile: OptimizationProfile) -> dict[str, object]:
    """Everything the plan depends on."""
    return {
        "model_sha256": model_sha256,
        "tensorrt": tensorrt_version(),
        "gpu": gpu.name,
        "compute_capability": gpu.compute_capability,
        "driver_major": gpu.driver_major,
        "tf32": tf32,
        "profile": profile.describe(),
    }


def fingerprint_key(values: dict[str, object]) -> str:
    """The directory name of a fingerprint."""
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock so that processes sharing a cache build and prune one at a time."""
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _stored_fingerprint(directory: Path) -> object:
    try:
        return json.loads((directory / FINGERPRINT_NAME).read_text())
    except (OSError, ValueError):
        return None


def _is_build_dir(path: Path) -> bool:
    return path.is_dir() and path.name.startswith(_BUILD_PREFIX)


def _is_engine_dir(path: Path) -> bool:
    return path.is_dir() and _KEY.fullmatch(path.name) is not None and (path / FINGERPRINT_NAME).is_file()


def _describe_change(group: Path, current: dict[str, object]) -> str:
    """Why a rebuild is needed, judged from the newest engine already in the group."""
    entries = sorted((p for p in group.iterdir() if _is_engine_dir(p)), key=lambda p: p.stat().st_mtime)
    stored = [s for s in map(_stored_fingerprint, entries) if isinstance(s, dict)]
    if not stored:
        return "no cached engine"
    previous = stored[-1]
    changed = [
        f"{name} ({str(previous.get(name))[:40]} -> {str(value)[:40]})"
        for name, value in current.items()
        if previous.get(name) != value
    ]
    return "fingerprint changed: " + ", ".join(changed)


def _cached_engine(
    directory: Path, current: dict[str, object], profile: OptimizationProfile, contexts: int,
) -> tuple[TensorRTEngine | None, str]:
    """The engine stored in ``directory`` and the reason there is none; a damaged entry is removed."""
    if not directory.exists():
        return None, ""
    try:
        if _stored_fingerprint(directory) != current:
            raise ValueError("the stored fingerprint is missing or different")
        return TensorRTEngine((directory / PLAN_NAME).read_bytes(), profile, contexts=contexts), ""
    except (OSError, ValueError, PlanRejectedError) as exc:
        logger.warning(f"Discarding the cached TensorRT engine {directory.name}: {exc}")
        shutil.rmtree(directory)
        return None, "the cached engine was damaged or rejected by TensorRT"


def _store(group: Path, key: str, current: dict[str, object], plan: bytes) -> Path:
    """Write the plan and its fingerprint into a scratch directory, then move it into place."""
    scratch = Path(tempfile.mkdtemp(prefix=_BUILD_PREFIX, dir=group))
    (scratch / PLAN_NAME).write_bytes(plan)
    (scratch / FINGERPRINT_NAME).write_text(json.dumps(current, sort_keys=True, indent=2))
    final = group / key
    os.replace(scratch, final)
    return final


def _prune(group: Path, keep: str) -> list[str]:
    """Remove the engines of other fingerprints and abandoned builds; return their names."""
    removed = []
    for entry in sorted(group.iterdir()):
        if entry.name != keep and (_is_engine_dir(entry) or _is_build_dir(entry)):
            shutil.rmtree(entry)
            removed.append(entry.name)
    if removed:
        logger.info(f"Pruned stale TensorRT engine directories: {', '.join(removed)}")
    return removed


def open_engine(
    *,
    onnx_path: Path,
    model_sha256: str,
    group_name: str,
    cache_dir: Path,
    gpu: GpuInfo,
    tf32: bool,
    profile: OptimizationProfile,
    contexts: int,
) -> OpenedEngine:
    """Load the engine cached for this fingerprint, or build it from ``onnx_path`` and cache it."""
    started = time.perf_counter()
    current = fingerprint(model_sha256=model_sha256, gpu=gpu, tf32=tf32, profile=profile)
    key = fingerprint_key(current)
    group = cache_dir / group_name
    group.mkdir(parents=True, exist_ok=True)
    with _locked(group / LOCK_NAME):
        directory = group / key
        engine, reason = _cached_engine(directory, current, profile, contexts)
        cache_hit = engine is not None
        if engine is None:
            reason = reason or _describe_change(group, current)
            logger.info(f"Building the TensorRT engine ({reason}); this takes several seconds")
            plan = build_plan(onnx_path, profile, tf32=tf32)
            directory = _store(group, key, current, plan)
            engine = TensorRTEngine(plan, profile, contexts=contexts)
        _prune(group, keep=key)
    return OpenedEngine(engine, directory, cache_hit, reason or "cache hit", time.perf_counter() - started)
