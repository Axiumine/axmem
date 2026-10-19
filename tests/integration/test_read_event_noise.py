"""Integration tests: internal lookups must not emit user-facing READ events.

With ACTIVITY_TRACK_READS enabled only an explicit read (get_*) is a read.
Write paths that merely check that something exists (unlinking memories,
linking skills) must not write ``read`` activity events or bump a memory's ``access_count``.
"""
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.activity_models import ActionType, EntityType
from app.models.memory_models import MemoryCreate
from app.models.skill_models import SkillCreate
from app.services.memory_service import MemoryService
from app.services.skill_service import SkillService
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


# ============================================================================
# SkillService link / unlink / export
# ============================================================================


@pytest.fixture
def skill_service(mock_skill_repository, event_bus):
    return SkillService(mock_skill_repository, event_bus=event_bus)


async def _new_skill(skill_service, user_id):
    return await skill_service.create_skill(
        user_id,
        SkillCreate(name="noise-skill", description="d", content="# body"),
    )


LINK_METHODS = [
    ("link_skill_to_memory", "memory_id"),
    ("unlink_skill_from_memory", "memory_id"),
    ("link_skill_to_file", "file_id"),
    ("unlink_skill_from_file", "file_id"),
    ("link_skill_to_code_artifact", "code_artifact_id"),
    ("unlink_skill_from_code_artifact", "code_artifact_id"),
    ("link_skill_to_document", "document_id"),
    ("unlink_skill_from_document", "document_id"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "target_kwarg"), LINK_METHODS)
async def test_skill_link_and_unlink_emit_no_read_event(
    skill_service, event_bus, method, target_kwarg,
):
    user_id = uuid4()
    skill = await _new_skill(skill_service, user_id)
    event_bus.collected_events.clear()

    await getattr(skill_service, method)(user_id=user_id, skill_id=skill.id, **{target_kwarg: 1})

    assert _reads(event_bus, EntityType.SKILL) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "target_kwarg"), LINK_METHODS)
async def test_skill_link_and_unlink_still_require_the_skill(
    skill_service, method, target_kwarg,
):
    with pytest.raises(NotFoundError):
        await getattr(skill_service, method)(user_id=uuid4(), skill_id=999, **{target_kwarg: 1})


@pytest.mark.asyncio
async def test_get_skill_and_export_skill_still_emit_read_events(skill_service, event_bus):
    """Explicit reads keep their READ event: only internal lookups are silenced."""
    user_id = uuid4()
    skill = await _new_skill(skill_service, user_id)
    event_bus.collected_events.clear()

    await skill_service.get_skill(user_id=user_id, skill_id=skill.id)
    assert len(_reads(event_bus, EntityType.SKILL)) == 1

    await skill_service.export_skill(user_id=user_id, skill_id=skill.id)
    assert len(_reads(event_bus, EntityType.SKILL)) == 2

