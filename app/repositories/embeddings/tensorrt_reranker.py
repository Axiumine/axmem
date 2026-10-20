"""Cross-encoder reranker that runs the model directly on TensorRT (strict fp32, NVIDIA GPU only).

Construction is the start-up warm-up: it probes the GPU, fetches the model once, builds or loads
the engine for the current fingerprint, scores one pair, logs how long that took and why the
engine was built or reused, and raises ``RerankerStartupError`` when any step fails. Nothing
falls back to another device or provider.

The engine is built for batches of 1 to 64 pairs and sequences of 2 to 512 tokens. Larger
requests are split into chunks of 64; a batch is padded to its longest pair, and never to fewer
than two tokens, so no call falls outside that profile.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config.settings import settings
from app.exceptions import RerankerError
from app.repositories.embeddings.reranker_model import (
    fetch_model,
    load_tokenizer,
    model_slug,
)
from app.repositories.embeddings.tensorrt_cache import open_engine
from app.repositories.embeddings.tensorrt_runtime import (
    GpuInfo,
    OptimizationProfile,
    probe_gpu,
    tensorrt_version,
)

logger = logging.getLogger(__name__)

PROFILE = OptimizationProfile(min_shape=(1, 2), opt_shape=(20, 336), max_shape=(64, 512))
MAX_BATCH = PROFILE.max_shape[0]
MIN_SEQUENCE = PROFILE.min_shape[1]


@dataclass(frozen=True)
class StartupReport:
    """What the start-up warm-up did."""

    seconds: float
    cache_hit: bool
    reason: str
    engine_dir: Path
    gpu: GpuInfo
    tf32: bool
    workers: int

    def summary(self) -> str:
        source = "cache hit" if self.cache_hit else f"rebuilt ({self.reason})"
        precision = "TF32" if self.tf32 else "strict fp32"
        gpu = (
            f"{self.gpu.name} ({self.gpu.total_bytes / 1024**3:.0f} GiB), "
            f"compute capability {self.gpu.compute_capability}, driver {self.gpu.driver_version}"
        )
        return (
            f"TensorRT {tensorrt_version()} reranker ready in {self.seconds:.2f}s: engine {source}; "
            f"{gpu}; {precision}; {self.workers} worker(s); {self.engine_dir}"
        )


def ensure_finite(scores: np.ndarray) -> None:
    """Refuse scores that are not numbers: a healthy fp32 model never produces them."""
    if not np.isfinite(scores).all():
        raise RerankerError("the TensorRT reranker produced non-finite scores")


class TensorRTRerankAdapter:
    """Score (query, document) pairs with a cross-encoder running on TensorRT."""

    def __init__(
        self,
        model: str | None = None,
        workers: int | None = None,
        tf32: bool | None = None,
        model_dir: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        started = time.perf_counter()
        model = model or settings.RERANKING_MODEL
        self.workers = workers or settings.RERANKING_WORKERS
        self.tf32 = settings.RERANKING_TF32 if tf32 is None else tf32
        gpu = probe_gpu()
        files = fetch_model(model, model_dir or settings.RERANKING_MODEL_DIR)
        self._tokenizer, self._pad_id = load_tokenizer(files.tokenizer)
        opened = open_engine(
            onnx_path=files.onnx,
            model_sha256=files.onnx_sha256,
            group_name=model_slug(model),
            cache_dir=Path(cache_dir or settings.RERANKING_ENGINE_CACHE_DIR),
            gpu=gpu,
            tf32=self.tf32,
            profile=PROFILE,
            contexts=self.workers,
        )
        self._engine = opened.engine
        self._executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="tensorrt-rerank")
        self.score("warm-up query", ["warm-up document"])
        self.startup = StartupReport(
            seconds=time.perf_counter() - started,
            cache_hit=opened.cache_hit,
            reason=opened.reason,
            engine_dir=opened.directory,
            gpu=gpu,
            tf32=self.tf32,
            workers=self.workers,
        )
        logger.info(self.startup.summary())

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Score documents by relevance to query, best first."""
        if not documents:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._rerank_sync, query, documents)

    def _rerank_sync(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        scores = self.score(query, documents)
        return sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)

    def score(self, query: str, documents: list[str]) -> list[float]:
        """The raw relevance logit of each document, in input order."""
        chunks = [
            self._score_chunk(query, documents[start:start + MAX_BATCH])
            for start in range(0, len(documents), MAX_BATCH)
        ]
        scores = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
        self._engine.verify()
        ensure_finite(scores)
        return scores.tolist()

    def _score_chunk(self, query: str, documents: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch([(query, document) for document in documents])
        width = max(MIN_SEQUENCE, *(len(encoding.ids) for encoding in encodings))
        shape = (len(encodings), width)
        features = {
            "input_ids": np.full(shape, self._pad_id, dtype=np.int64),
            "attention_mask": np.zeros(shape, dtype=np.int64),
            "token_type_ids": np.zeros(shape, dtype=np.int64),
        }
        for row, encoding in enumerate(encodings):
            length = len(encoding.ids)
            features["input_ids"][row, :length] = encoding.ids
            features["attention_mask"][row, :length] = encoding.attention_mask
            features["token_type_ids"][row, :length] = encoding.type_ids
        logits = self._engine.infer({name: features[name] for name in self._engine.input_names if name in features})
        return logits.reshape(len(encodings), -1)[:, 0].astype(np.float32)

    def close(self) -> None:
        """Wait for running jobs, then release the engine and its device memory."""
        self._executor.shutdown(wait=True)
        self._engine.close()
