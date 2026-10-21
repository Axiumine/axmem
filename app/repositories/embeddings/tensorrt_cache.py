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

from app.exceptions import TensorRTStartupError
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


def _discard(directory: Path, why: object) -> None:
    logger.warning(f"Discarding the cached TensorRT engine {directory.name}: {why}")
    shutil.rmtree(directory)


def _cached_engine(
    directory: Path, current: dict[str, object], profile: OptimizationProfile, contexts: int,
) -> tuple[TensorRTEngine | None, str, PlanRejectedError | None]:
    """The engine stored in ``directory``, why there is none, and TensorRT's refusal of the stored plan if it gave one.

    An entry with no plan, or whose stored fingerprint is missing or different from the one that names it, is
    damaged and removed. A plan that TensorRT refuses is not proof of damage: the same refusal comes from a GPU too
    full to hold the engine, or from a driver that does not suit it. It stays in place until ``open_engine`` has built
    a plan from the same model that this process can load, and is replaced only then. A file that cannot be read
    (permission, I/O error) is not judged at all: the ``OSError`` ends the start with the cache as the cause.
    """
    if not directory.exists():
        return None, "", None
    plan = directory / PLAN_NAME
    if _stored_fingerprint(directory) != current or not plan.is_file():
        _discard(directory, "its fingerprint or its plan is missing or different")
        return None, "the cached engine was damaged", None
    try:
        return TensorRTEngine(plan.read_bytes(), profile, contexts=contexts), "", None
    except PlanRejectedError as exc:
        logger.warning(
            f"TensorRT rejected the cached engine {directory.name} ({exc}); building a new one to tell a damaged file "
            "from a GPU that cannot hold the engine",
        )
        return None, "TensorRT rejected the cached engine", exc


def _load_built(
    plan: bytes, profile: OptimizationProfile, contexts: int, rejection: PlanRejectedError | None, key: str,
) -> TensorRTEngine:
    """Load a plan built a moment ago. When TensorRT refuses that too after refusing the cached one, the file is not at fault."""
    try:
        return TensorRTEngine(plan, profile, contexts=contexts)
    except PlanRejectedError as exc:
        if rejection is None:
            raise
        raise TensorRTStartupError(
            "engine-unusable",
            f"TensorRT rejects the cached engine {key} and an engine built a moment ago from the same model alike "
            f"({exc}), so the cached file is not at fault and was kept. Check that the GPU has the free memory "
            "the engine needs and that the driver suits TensorRT (see docs/gpu-models.md).",
        ) from exc


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
    try:
        group.mkdir(parents=True, exist_ok=True)
        with _locked(group / LOCK_NAME):
            directory = group / key
            engine, reason, rejection = _cached_engine(directory, current, profile, contexts)
            plan: bytes | None = None
            if engine is None:
                reason = reason or _describe_change(group, current)
                logger.info(f"Building the TensorRT engine ({reason}); this takes several seconds")
                plan = build_plan(onnx_path, profile, tf32=tf32)
                engine = _load_built(plan, profile, contexts, rejection, key)  # only a plan this process loads is cached
            try:
                if plan is not None:
                    if rejection is not None:  # a fresh build loads where the stored plan did not: replace it
                        _discard(directory, f"{rejection}; a plan built a moment ago from the same model loads")
                    directory = _store(group, key, current, plan)
                _prune(group, keep=key)
            except BaseException:
                engine.close()  # a refused start must not keep the engine in GPU memory
                raise
    except OSError as exc:
        raise TensorRTStartupError(
            "engine-unusable",
            f"the engine cache {group} cannot be used ({type(exc).__name__}: {exc}). Point the engine cache "
            "directory setting of this model at a directory the process can write.",
        ) from exc
    return OpenedEngine(engine, directory, plan is None, reason or "cache hit", time.perf_counter() - started)
