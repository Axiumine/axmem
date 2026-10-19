"""Builders shared by the PostgreSQL memory repository end-to-end tests.

The tests drive ``PostgresMemoryRepository`` directly, with a deterministic embedder, so
they need an owning user, a few memories and a way to give them distinguishable vectors.
"""
import hashlib
import random
from uuid import UUID

from app.config.settings import settings
from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from app.repositories.postgres.user_repository import PostgresUserRepository


def vector_for(text: str) -> list[float]:
    """Deterministic pseudo-random vector for ``text`` (same text, same vector)."""
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(settings.EMBEDDING_DIMENSIONS)]


class HashingEmbeddings:
    """Embedding adapter returning ``vector_for(text)`` without calling any provider."""

    async def generate_embedding(self, text: str) -> list[float]:
        return vector_for(text)


async def create_user(db_adapter, external_id: str) -> UUID:
    user = await PostgresUserRepository(db_adapter).create_user(
        UserCreate(external_id=external_id, name=external_id, email=f"{external_id}@example.com"),
    )
    return user.id


def memory_data(title: str, importance: int = 7, **extra) -> MemoryCreate:
    return MemoryCreate(
        title=title,
        content=f"content of {title}",
        context="repository test",
        keywords=["k"],
        tags=["t"],
        importance=importance,
        **extra,
    )
