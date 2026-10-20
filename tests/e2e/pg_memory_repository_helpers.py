"""Builders shared by the PostgreSQL memory repository end-to-end tests.

The tests drive ``PostgresMemoryRepository`` directly, with the real TensorRT embedder
(the ``tensorrt_embedder`` fixture), so they need an owning user and a few memories.
"""
from uuid import UUID

from app.models.memory_models import MemoryCreate
from app.models.user_models import UserCreate
from app.repositories.postgres.user_repository import PostgresUserRepository


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
