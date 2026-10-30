# SPDX-FileCopyrightText: 2026 Giovanni Manzoni
# SPDX-License-Identifier: AGPL-3.0-only

"""Wrappers around the production embedder for the tests that must inject what a model cannot do on demand.

Vectors always come from the real TensorRT embedder on the GPU (the ``tensorrt_embedder`` session
fixture). These wrappers only add the things the model cannot produce by itself: a record of the
calls, a delay or a gate inside a call, code that runs in the middle of a call, a failure on cue, or a
vector of another width than the model's. Nothing here computes a vector.
"""
import math
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.config.settings import settings


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity of two vectors."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right)))


async def similarity(embedder: Any, left: Any, right: Any) -> float:
    """Cosine similarity of two texts, or of two ``MemoryCreate`` objects (their embedding text), under ``embedder``."""
    from app.models.memory_models import MemoryCreate
    from app.utils.embedding_helpers import build_embedding_text

    def text_of(item: Any) -> str:
        return build_embedding_text(item) if isinstance(item, MemoryCreate) else item

    first, second = await embedder.generate_embeddings([text_of(left), text_of(right)])
    return cosine(first, second)


def spy_on(real: Any) -> MagicMock:
    """The real embedder behind ``AsyncMock`` spies: both entry points record their calls and pass through.

    A test injects a fault by assigning ``side_effect`` to one of the two spies, which then replaces
    the pass-through for that entry point.
    """
    spy = MagicMock()
    spy.real = real
    spy.generate_embedding = AsyncMock(wraps=real.generate_embedding)
    spy.generate_embeddings = AsyncMock(wraps=real.generate_embeddings)
    return spy


def failing_on(real_method: Callable[..., Awaitable[Any]], *, call: int, error: Exception):
    """A ``side_effect`` that passes every call to ``real_method`` except the numbered one (from 1), which raises."""
    seen = 0

    async def run(*args: Any, **kwargs: Any) -> Any:
        nonlocal seen
        seen += 1
        if seen == call:
            raise error
        return await real_method(*args, **kwargs)

    return run


class DelegatingEmbedder:
    """The real embedder; a subclass overrides ``generate_embedding`` to add timing, hooks or faults.

    ``generate_embeddings`` goes through ``generate_embedding`` text by text, so an override applies to
    batches too.
    """

    def __init__(self, real: Any):
        self.real = real

    async def generate_embedding(self, text: str) -> list[float]:
        return await self.real.generate_embedding(text)

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [await self.generate_embedding(text) for text in texts]


class NarrowedEmbedder(DelegatingEmbedder):
    """The real vector cut to ``settings.EMBEDDING_DIMENSIONS`` components and scaled back to length 1.

    The model has one width (384), so a test of a change of embedding width needs this wrapper: the
    components are the model's own, only their number follows the configuration at call time.
    """

    async def generate_embedding(self, text: str) -> list[float]:
        vector = (await self.real.generate_embedding(text))[: settings.EMBEDDING_DIMENSIONS]
        length = math.sqrt(sum(value * value for value in vector))
        return [value / length for value in vector]


class MigratingEmbedder(DelegatingEmbedder):
    """The real embedder as a migration to another model leaves it: every vector differs from the stored one.

    Each text is embedded with a prefix, so a stored vector that a failed migration did not put back
    shows as a cosine below 1 against the original text. ``stop`` is awaited on call number ``stop_at``
    (counted from 1) and may raise, or hold the call while a signal is delivered.
    """

    PREFIX = "migrated: "

    def __init__(
        self, real: Any, *, stop_at: int | None = None, stop: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__(real)
        self.calls = 0
        self.stop_at = stop_at
        self.stop = stop

    async def generate_embedding(self, text: str) -> list[float]:
        self.calls += 1
        if self.stop is not None and self.calls == self.stop_at:
            await self.stop()
        return await super().generate_embedding(self.PREFIX + text)
