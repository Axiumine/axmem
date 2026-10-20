"""Pieces the TensorRT embedder and the TensorRT reranker share: the start-up report, batch padding and the finite check.

Both adapters feed a BERT-style model (``input_ids``, ``attention_mask``, ``token_type_ids``) with a
batch padded to its longest member, never to fewer tokens than the optimisation profile allows,
and both log one report line when the warm-up has run the engine on the GPU.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.exceptions import TensorRTError
from app.repositories.embeddings.tensorrt_runtime import (
    GpuInfo,
    GpuMemory,
    tensorrt_version,
)


@dataclass(frozen=True)
class StartupReport:
    """What the start-up warm-up of one TensorRT adapter did."""

    role: str  # "embedder" or "reranker"
    seconds: float
    cache_hit: bool
    reason: str
    engine_dir: Path
    gpu: GpuInfo
    tf32: bool
    workers: int
    cuda_context: int  # address of the CUDA context the warm-up ran on: one per process, shared by all engines
    memory: GpuMemory  # GPU memory right after the warm-up

    def summary(self) -> str:
        source = "cache hit" if self.cache_hit else f"rebuilt ({self.reason})"
        precision = "TF32" if self.tf32 else "strict fp32"
        gpu = (
            f"{self.gpu.name} ({self.gpu.total_bytes / 1024**3:.0f} GiB), "
            f"compute capability {self.gpu.compute_capability}, driver {self.gpu.driver_version}"
        )
        return (
            f"TensorRT {tensorrt_version()} {self.role} ready in {self.seconds:.2f}s: engine {source}; "
            f"{gpu}; {precision}; {self.workers} worker(s); {self.engine_dir}; "
            f"CUDA context {self.cuda_context:#x}; {self.memory.describe()}"
        )


def ensure_finite(values: np.ndarray, role: str, what: str) -> None:
    """Refuse results that are not numbers: a healthy fp32 model never produces them."""
    if not np.isfinite(values).all():
        raise TensorRTError(f"the TensorRT {role} produced non-finite {what}")


def pad_batch(encodings: list, pad_id: int, minimum: int) -> dict[str, np.ndarray]:
    """The model inputs for ``encodings``, padded to the longest of them (at least ``minimum`` tokens)."""
    shape = (len(encodings), max(minimum, *(len(encoding.ids) for encoding in encodings)))
    features = {
        "input_ids": np.full(shape, pad_id, dtype=np.int64),
        "attention_mask": np.zeros(shape, dtype=np.int64),
        "token_type_ids": np.zeros(shape, dtype=np.int64),
    }
    for row, encoding in enumerate(encodings):
        length = len(encoding.ids)
        features["input_ids"][row, :length] = encoding.ids
        features["attention_mask"][row, :length] = encoding.attention_mask
        features["token_type_ids"][row, :length] = encoding.type_ids
    return features
