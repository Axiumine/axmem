"""Integration tests: internal lookups must not emit user-facing READ events.

With ACTIVITY_TRACK_READS enabled only an explicit read (get_*) is a read.
Write paths that merely check that something exists (unlinking memories)
must not write ``read`` activity events or bump a memory's ``access_count``.
"""
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.models.activity_models import ActionType, EntityType
from app.models.memory_models import MemoryCreate
from app.services.memory_service import MemoryService
from tests.integration.conftest import CollectingEventBus


@pytest.fixture
def event_bus():
    return CollectingEventBus()


@pytest.fixture(autouse=True)
def track_reads():
    """Opt-in read tracking is the condition under which the noise appeared."""
    with patch.object(settings, "ACTIVITY_TRACK_READS", True):
        yield


def _reads(event_bus, entity_type):
    return [
        e for e in event_bus.collected_events
        if e.entity_type == entity_type and e.action == ActionType.READ
    ]


# ============================================================================
# MemoryService.unlink_memories
# ============================================================================


def _memory_data(title: str, keyword: str) -> MemoryCreate:
    # Distinct keywords so the stub repository does not auto-link the memories
    return MemoryCreate(
        title=title,
        content=f"content of {title}",
        context="read event noise",
        keywords=[keyword],
        tags=["noise"],
        importance=5,
    )


@pytest.mark.asyncio
async def test_unlink_memories_is_not_counted_as_a_read(mock_memory_repository, event_bus):
    service = MemoryService(mock_memory_repository, event_bus=event_bus)
    service.register_access_tracking_handlers(event_bus)
    user_id = uuid4()

    m1, _ = await service.create_memory(user_id, _memory_data("alpha", "quasar"))
    m2, _ = await service.create_memory(user_id, _memory_data("beta", "granite"))
    await service.link_memories(user_id=user_id, memory_id=m1.id, related_ids=[m2.id])
    await event_bus.wait_for_pending()

    removed = await service.unlink_memories(user_id=user_id, memory_id=m1.id, target_id=m2.id)
    await event_bus.wait_for_pending()

    assert removed is True
    assert _reads(event_bus, EntityType.MEMORY) == []
    source = await mock_memory_repository.get_memory_by_id(user_id=user_id, memory_id=m1.id)
    assert source.access_count == 0
    assert source.last_accessed_at is None
