"""Non-repository test doubles: deterministic embeddings and a recording bus."""

import hashlib
import random

from app.events import EventBus
from app.models.activity_models import ActivityEvent


class MockEmbeddingsAdapter:
    """Mock embeddings adapter that returns deterministic 384-dim vectors"""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    async def embed_text(self, text: str) -> list[float]:
        """Generate deterministic embeddings from text using hash-based seeding"""
        # Use MD5 hash for reproducibility (same text -> same embedding)
        hash_value = hashlib.md5(text.encode()).hexdigest()
        seed = int(hash_value[:8], 16)
        random.seed(seed)

        # Generate normalized vector
        vector = [random.random() for _ in range(self.dimensions)]

        # Normalize to unit length (typical for embeddings)
        magnitude = sum(x**2 for x in vector) ** 0.5
        normalized = [x / magnitude for x in vector]

        return normalized


class CollectingEventBus(EventBus):
    """EventBus that collects emitted events for testing."""

    def __init__(self):
        super().__init__()
        self.collected_events: list[ActivityEvent] = []

    async def emit(self, event: ActivityEvent) -> None:
        self.collected_events.append(event)
        await super().emit(event)
