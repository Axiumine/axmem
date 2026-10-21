"""Embeddings computed in this process by a bge model on TensorRT (strict fp32, NVIDIA GPU only).

This is the default embeddings provider (``EMBEDDING_PROVIDER=TensorRT``). Construction is the
start-up warm-up: it probes the GPU, fetches the model once, builds or loads the engine for the
current fingerprint, embeds one text, checks that the vector is as wide as ``EMBEDDING_DIMENSIONS``,
logs how long that took and why the engine was built or reused, and raises ``TensorRTStartupError``
when any step fails. There is no CPU path and nothing falls back to another provider.

A text becomes one vector the way the bge models define it: the tokenizer adds ``[CLS]`` and
``[SEP]`` and truncates to 512 tokens, the model's hidden state at ``[CLS]`` is the embedding
(CLS pooling), and the vector is scaled to length 1 (L2 normalisation), so the dot product of two
vectors is their cosine similarity.

The engine is built for batches of 1 to 64 texts and sequences of 2 to 512 tokens. A batch is
padded to its longest text, and never to fewer than two tokens; more than 64 texts are embedded in
chunks of 64. The engine has one execution context, so calls run one at a time on one worker
thread and never block the event loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from app.config.settings import settings
from app.exceptions import TensorRTStartupError
from app.protocols.embedding_protocol import EmbeddingsAdapter
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

PROFILE = OptimizationProfile(min_shape=(1, 2), opt_shape=(16, 128), max_shape=(64, 512))
MAX_BATCH = PROFILE.max_shape[0]
MIN_SEQUENCE = PROFILE.min_shape[1]
WARM_UP_TEXT = "warm-up"


class TensorRTEmbeddingsAdapter(EmbeddingsAdapter):
    """Embed texts with a bge model running on TensorRT."""

    def __init__(
        self,
        model: str | None = None,
        tf32: bool | None = None,
        model_dir: str | None = None,
        cache_dir: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        started = time.perf_counter()
        self.model = model or settings.EMBEDDING_MODEL
        self.dimensions = dimensions or settings.EMBEDDING_DIMENSIONS
        self.tf32 = settings.EMBEDDING_TF32 if tf32 is None else tf32
        self.workers = 1
        # Made before the engine, so that nothing which can fail stands between the engine and the ``try`` below.
        self._executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="tensorrt-embed")
        gpu = probe_gpu()
        files = fetch_model(self.model, model_dir or settings.EMBEDDING_MODEL_DIR, "EMBEDDING_MODEL")
        self._tokenizer, self._pad_id = load_tokenizer(files.tokenizer)
        opened = open_engine(
            onnx_path=files.onnx,
            model_sha256=files.onnx_sha256,
            group_name=model_slug(self.model, "EMBEDDING_MODEL"),
            cache_dir=Path(cache_dir or settings.EMBEDDING_ENGINE_CACHE_DIR),
            gpu=gpu,
            tf32=self.tf32,
            profile=PROFILE,
            contexts=self.workers,
        )
        self._engine = opened.engine
        try:
            self._check_width(self.embed([WARM_UP_TEXT])[0])
            self.startup = StartupReport(
                role="embedder",
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
        except BaseException:
            self.close()  # a refused start must not keep the engine in GPU memory; the error that refused it surfaces
            raise

    def _check_width(self, vector: list[float]) -> None:
        if len(vector) != self.dimensions:
            raise TensorRTStartupError(
                "dimensions",
                f"{self.model} produces {len(vector)}-dimensional embeddings but EMBEDDING_DIMENSIONS is "
                f"{self.dimensions}. Set EMBEDDING_DIMENSIONS={len(vector)}, or choose a model that produces "
                f"{self.dimensions}; memories already stored need `--re-embed` after a change "
                "(see docs/embedding_migration.md).",
            )

    async def generate_embedding(self, text: str) -> list[float]:
        return (await self.generate_embeddings([text]))[0]

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """One unit-length vector per text, in order. An empty list yields an empty list."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.embed, texts)

    @staticmethod
    def _require_text(texts: list[str]) -> None:
        """Refuse a text the model cannot embed, naming its position, before any GPU work is done."""
        for index, text in enumerate(texts):
            if not isinstance(text, str):
                raise TypeError(f"cannot embed {type(text).__name__} (text {index} of {len(texts)} is not a string)")
            if not text.strip():
                raise ValueError(f"cannot embed empty text (text {index} of {len(texts)} is empty or only whitespace)")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` on the calling thread, in chunks the engine's profile allows."""
        if not texts:
            return []
        self._require_text(texts)
        chunks = [self._embed_chunk(texts[start:start + MAX_BATCH]) for start in range(0, len(texts), MAX_BATCH)]
        vectors = np.concatenate(chunks)
        self._engine.verify()
        ensure_finite(vectors, "embedder", "embeddings")
        return vectors.tolist()

    def _embed_chunk(self, texts: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        hidden = self._engine.infer(pad_batch(encodings, self._pad_id, MIN_SEQUENCE))
        cls = hidden[:, 0, :].astype(np.float64)  # CLS pooling
        with np.errstate(all="ignore"):  # a zero vector divides to NaN, which embed() refuses
            return cls / np.linalg.norm(cls, axis=1, keepdims=True)

    def unusable_reason(self) -> str:
        """Why this embedder can serve no more (an engine that met a sticky CUDA error), or an empty string."""
        failure = self._engine.failure
        return f"the embedder engine is unusable after an earlier failure ({failure}); restart the server" if failure else ""

    def close(self) -> None:
        """Wait for running jobs, then release the engine and its device memory (even when the wait fails)."""
        try:
            self._executor.shutdown(wait=True)
        finally:
            self._engine.close()
