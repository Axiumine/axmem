"""MemoryService when a memory changes under it.

Covers the answers of ``update_memory`` / ``mark_memory_obsolete`` when the repository reports
that the memory is gone, and the linked-memory expansion of a query when the repository misbehaves.
"""

import logging
from uuid import uuid4

import pytest

from app.models.memory_models import MemoryCreate, MemoryQueryRequest, MemoryUpdate


@pytest.fixture
def user_id():
    """A tenant of this test alone: the collections are shared by the whole session."""
    return uuid4()


async def _memory(service, user_id, title="Alpha memory"):
    created, _ = await service.create_memory(
        user_id,
        MemoryCreate(
            title=title,
            content="Alpha content",
            context="Alpha context",
            keywords=["alpha"],
            tags=["a"],
            importance=7,
        ),
    )
    return created


async def test_update_of_a_memory_that_vanished_returns_none(test_memory_service, user_id, monkeypatch):
    service = test_memory_service
    memory = await _memory(service, user_id)

    async def vanished(**_):
        return None

    monkeypatch.setattr(service.memory_repo, "update_memory", vanished)

    assert await service.update_memory(user_id, memory.id, MemoryUpdate(title="Renamed memory title")) is None


async def test_obsolete_marking_that_the_repository_refuses_returns_false(test_memory_service, user_id, monkeypatch):
    service = test_memory_service
    memory = await _memory(service, user_id)

    async def refused(**_):
        return False

    monkeypatch.setattr(service.memory_repo, "mark_obsolete", refused)

    assert await service.mark_memory_obsolete(user_id, memory.id, reason="outdated") is False


async def _two_primary_memories(service, user_id):
    first = await _memory(service, user_id, "Alpha memory")
    second = await _memory(service, user_id, "Alpha memory again")
    request = MemoryQueryRequest(
        query="Alpha", query_context="finding alpha", k=5, include_links=True, max_links_per_primary=5,
    )
    return {first.id, second.id}, request


async def test_query_ignores_linked_memories_the_repository_returns_twice(
    test_memory_service, user_id, monkeypatch,
):
    service = test_memory_service
    primary_ids, request = await _two_primary_memories(service, user_id)

    async def returns_the_primaries_again(**_):
        return [
            await service.memory_repo.get_memory_by_id(user_id=user_id, memory_id=memory_id)
            for memory_id in sorted(primary_ids)
        ]

    monkeypatch.setattr(service.memory_repo, "get_linked_memories", returns_the_primaries_again)

    result = await service.query_memory(user_id, request)

    assert {m.id for m in result.primary_memories} == primary_ids
    assert result.linked_memories == []


async def test_query_keeps_primary_results_when_fetching_linked_memories_fails(
    test_memory_service, user_id, monkeypatch, caplog,
):
    service = test_memory_service
    primary_ids, request = await _two_primary_memories(service, user_id)

    async def link_store_down(**_):
        raise RuntimeError("link store down")

    monkeypatch.setattr(service.memory_repo, "get_linked_memories", link_store_down)
    caplog.set_level(logging.WARNING, logger="app.services.memory_service")

    result = await service.query_memory(user_id, request)

    assert {m.id for m in result.primary_memories} == primary_ids
    assert result.linked_memories == []
    assert "failed to fetch memories" in [r.getMessage() for r in caplog.records]
