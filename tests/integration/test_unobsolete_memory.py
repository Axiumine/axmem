"""Integration tests for unobsolete_memory restore path."""
import asyncio
from uuid import uuid4

import pytest

from app.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.memory_models import MemoryCreate, MemoryQueryRequest


@pytest.mark.asyncio
async def test_unobsolete_restores_query_visibility_and_clears_fields(test_memory_service):
    user_id = uuid4()
    mem_data = MemoryCreate(
        title="Restore me unique-title-ff86",
        content="Memory to obsolete then restore",
        context="unobsolete test",
        keywords=["restore", "ff86"],
        tags=["stub"],
        importance=5,
    )
    mem, _ = await test_memory_service.create_memory(user_id=user_id, memory_data=mem_data)
    await test_memory_service.mark_memory_obsolete(
        user_id=user_id,
        memory_id=mem.id,
        reason="test obsolete",
        superseded_by=None,
    )

    before = await test_memory_service.query_memory(
        user_id=user_id,
        memory_query=MemoryQueryRequest(
            query="unique-title-ff86",
            query_context="unobsolete visibility check",
            k=5,
        ),
    )
    assert all(m.id != mem.id for m in before.primary_memories)

    ok = await test_memory_service.unobsolete_memory(user_id=user_id, memory_id=mem.id)
    assert ok is True

    refreshed = await test_memory_service.get_memory(user_id=user_id, memory_id=mem.id)
    assert refreshed is not None
    assert refreshed.is_obsolete is False
    assert refreshed.obsolete_reason is None
    assert refreshed.superseded_by is None
    assert refreshed.obsoleted_at is None

    after = await test_memory_service.query_memory(
        user_id=user_id,
        memory_query=MemoryQueryRequest(
            query="unique-title-ff86",
            query_context="unobsolete visibility check",
            k=5,
        ),
    )
    assert any(m.id == mem.id for m in after.primary_memories)


@pytest.mark.asyncio
async def test_unobsolete_unknown_id_raises_not_found(test_memory_service):
    user_id = uuid4()
    with pytest.raises(NotFoundError):
        await test_memory_service.unobsolete_memory(user_id=user_id, memory_id=uuid4())


@pytest.mark.asyncio
async def test_unobsolete_active_memory_raises_invalid_state_transition(test_memory_service):
    user_id = uuid4()
    mem_data = MemoryCreate(
        title="Still active",
        content="Never obsolete",
        context="unobsolete negative test",
        keywords=["active"],
        tags=["stub"],
        importance=5,
    )
    mem, _ = await test_memory_service.create_memory(user_id=user_id, memory_data=mem_data)

    with pytest.raises(InvalidStateTransitionError, match="is not obsolete"):
        await test_memory_service.unobsolete_memory(user_id=user_id, memory_id=mem.id)


async def _restore_twice_concurrently(service, user_id, memory_id):
    """Fire two overlapping unobsolete_memory calls and return both outcomes."""
    return await asyncio.gather(
        service.unobsolete_memory(user_id=user_id, memory_id=memory_id),
        service.unobsolete_memory(user_id=user_id, memory_id=memory_id),
        return_exceptions=True,
    )


def _split_restore_outcomes(results):
    """Split concurrent restore outcomes into (successes, invalid-state errors)."""
    successes = [r for r in results if r is True]
    invalid = [r for r in results if isinstance(r, InvalidStateTransitionError)]
    return successes, invalid


@pytest.mark.asyncio
async def test_unobsolete_concurrent_restore_one_success_one_invalid_state(test_memory_service):
    """Two overlapping restores: one success, one invalid-state."""
    service = test_memory_service
    user_id = uuid4()
    mem_data = MemoryCreate(
        title="Concurrent restore race",
        content="Obsolete memory restored concurrently",
        context="unobsolete concurrency test",
        keywords=["concurrent", "restore"],
        tags=["stub"],
        importance=5,
    )
    mem, _ = await service.create_memory(user_id=user_id, memory_data=mem_data)
    await service.mark_memory_obsolete(
        user_id=user_id,
        memory_id=mem.id,
        reason="concurrency obsolete",
        superseded_by=None,
    )

    results = await _restore_twice_concurrently(service, user_id, mem.id)

    successes, invalid = _split_restore_outcomes(results)
    assert len(successes) == 1
    assert len(invalid) == 1


@pytest.mark.asyncio
async def test_unobsolete_many_overlapping_restores_have_one_winner(test_memory_service):
    """The restore is one conditional write: of eight overlapping calls exactly one wins."""
    user_id = uuid4()
    mem, _ = await test_memory_service.create_memory(
        user_id=user_id,
        memory_data=MemoryCreate(
            title="Eight-way restore race",
            content="Obsolete memory restored by eight overlapping calls",
            context="unobsolete concurrency test",
            keywords=["concurrent", "restore"],
            tags=["stub"],
            importance=5,
        ),
    )
    await test_memory_service.mark_memory_obsolete(
        user_id=user_id, memory_id=mem.id, reason="concurrency obsolete", superseded_by=None,
    )

    results = await asyncio.gather(
        *(test_memory_service.unobsolete_memory(user_id=user_id, memory_id=mem.id) for _ in range(8)),
        return_exceptions=True,
    )

    successes, invalid = _split_restore_outcomes(results)
    assert len(successes) == 1
    assert len(invalid) == 7
