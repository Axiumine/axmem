"""Cross-encoder reranker that runs the model directly on TensorRT (strict fp32, NVIDIA GPU only).

Construction is the start-up warm-up: it probes the GPU, fetches the model once, builds or loads
the engine for the current fingerprint, scores one pair, logs how long that took and why the
engine was built or reused, and raises ``TensorRTStartupError`` when any step fails. Nothing
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
from pathlib import Path

import numpy as np

from app.config.settings import settings
from app.repositories.embeddings.tensorrt_cache import open_engine
from app.repositories.embeddings.tensorrt_common import (
    StartupReport,
    ensure_finite,
    pad_batch,
)
from app.repositories.embeddings.tensorrt_model import (
    fetch_model,
    load_tokenizer,
    model_slug,
)
from app.repositories.embeddings.tensorrt_runtime import (
    OptimizationProfile,
    cuda_context_handle,
    gpu_memory,
    probe_gpu,
)

logger = logging.getLogger(__name__)

PROFILE = OptimizationProfile(min_shape=(1, 2), opt_shape=(20, 336), max_shape=(64, 512))
MAX_BATCH = PROFILE.max_shape[0]
MIN_SEQUENCE = PROFILE.min_shape[1]


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
        files = fetch_model(model, model_dir or settings.RERANKING_MODEL_DIR, "RERANKING_MODEL")
        self._tokenizer, self._pad_id = load_tokenizer(files.tokenizer)
        opened = open_engine(
            onnx_path=files.onnx,
            model_sha256=files.onnx_sha256,
            group_name=model_slug(model, "RERANKING_MODEL"),
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
            role="reranker",
            seconds=time.perf_counter() - started,
            cache_hit=opened.cache_hit,
            reason=opened.reason,
            engine_dir=opened.directory,
            gpu=gpu,
            tf32=self.tf32,
            workers=self.workers,
            cuda_context=cuda_context_handle(),
            memory=gpu_memory(),
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
        ensure_finite(scores, "reranker", "scores")
        return scores.tolist()

    def _score_chunk(self, query: str, documents: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch([(query, document) for document in documents])
        features = pad_batch(encodings, self._pad_id, MIN_SEQUENCE)
        logits = self._engine.infer({name: features[name] for name in self._engine.input_names if name in features})
        return logits.reshape(len(encodings), -1)[:, 0].astype(np.float32)

    def close(self) -> None:
        """Wait for running jobs, then release the engine and its device memory."""
        self._executor.shutdown(wait=True)
        self._engine.close()
