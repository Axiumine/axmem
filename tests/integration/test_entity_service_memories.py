"""Integration tests for EntityService memory lookups (entity memories and memory entities) with in-memory stubs.
"""
from uuid import uuid4

import pytest

from app.exceptions import NotFoundError
from app.models.entity_models import (
    EntityCreate,
    EntityType,
)


@pytest.mark.asyncio
async def test_get_entity_memories_basic(test_entity_service, mock_entity_repository):
    """Test getting memories linked to an entity, including titles"""
    user_id = uuid4()

    # Create entity
    entity_data = EntityCreate(
        name="Test Entity for Memories",
        entity_type=EntityType.ORGANIZATION,
        tags=["memory-test"],
    )
    entity = await test_entity_service.create_entity(user_id, entity_data)

    # Give the linked memories known titles
    mock_entity_repository.set_memory_title(1, "Q3 Roadmap")
    mock_entity_repository.set_memory_title(5, "Onboarding Notes")
    mock_entity_repository.set_memory_title(10, "Architecture Decision Record")

    # Link some memories
    await test_entity_service.link_entity_to_memory(user_id, entity.id, 1)
    await test_entity_service.link_entity_to_memory(user_id, entity.id, 5)
    await test_entity_service.link_entity_to_memory(user_id, entity.id, 10)

    # Get entity memories
    memory_ids, count, memories = await test_entity_service.get_entity_memories(user_id, entity.id)

    assert count == 3
    assert len(memory_ids) == 3
    assert 1 in memory_ids
    assert 5 in memory_ids
    assert 10 in memory_ids

    # "memories" must carry (id, title) pairs matching memory_ids, in the same order
    assert [mid for mid, _ in memories] == memory_ids
    titles_by_id = dict(memories)
    assert titles_by_id[1] == "Q3 Roadmap"
    assert titles_by_id[5] == "Onboarding Notes"
    assert titles_by_id[10] == "Architecture Decision Record"


@pytest.mark.asyncio
async def test_get_entity_memories_empty(test_entity_service):
    """Test getting memories for entity with no linked memories"""
    user_id = uuid4()

    # Create entity
    entity_data = EntityCreate(
        name="Entity With No Memories",
        entity_type=EntityType.INDIVIDUAL,
        tags=["empty-test"],
    )
    entity = await test_entity_service.create_entity(user_id, entity_data)

    # Get entity memories (should be empty, not error)
    memory_ids, count, memories = await test_entity_service.get_entity_memories(user_id, entity.id)

    assert count == 0
    assert memory_ids == []
    assert memories == []


@pytest.mark.asyncio
async def test_get_entity_memories_not_found(test_entity_service):
    """Test getting memories for non-existent entity raises error"""
    user_id = uuid4()

    with pytest.raises(NotFoundError):
        await test_entity_service.get_entity_memories(user_id, 999999)


@pytest.mark.asyncio
async def test_get_entity_memories_after_unlink(test_entity_service):
    """Test that unlinking removes memory from entity's memory list"""
    user_id = uuid4()

    # Create entity
    entity_data = EntityCreate(
        name="Entity for Unlink Test",
        entity_type=EntityType.DEVICE,
        tags=["unlink-test"],
    )
    entity = await test_entity_service.create_entity(user_id, entity_data)

    # Link some memories
    await test_entity_service.link_entity_to_memory(user_id, entity.id, 1)
    await test_entity_service.link_entity_to_memory(user_id, entity.id, 2)
    await test_entity_service.link_entity_to_memory(user_id, entity.id, 3)

    # Verify initial state
    memory_ids, count, memories = await test_entity_service.get_entity_memories(user_id, entity.id)
    assert count == 3
    assert len(memories) == 3

    # Unlink one memory
    await test_entity_service.unlink_entity_from_memory(user_id, entity.id, 2)

    # Verify memory was removed
    memory_ids, count, memories = await test_entity_service.get_entity_memories(user_id, entity.id)
    assert count == 2
    assert 1 in memory_ids
    assert 3 in memory_ids
    assert 2 not in memory_ids
    assert {mid for mid, _ in memories} == {1, 3}


@pytest.mark.asyncio
async def test_get_entity_memories_user_isolation(test_entity_service):
    """Test that users can only see their own entity's memories"""
    user_id_1 = uuid4()
    user_id_2 = uuid4()

    # Create entity for user 1
    entity_data = EntityCreate(
        name="User 1 Entity",
        entity_type=EntityType.ORGANIZATION,
        tags=["isolation-test"],
    )
    entity = await test_entity_service.create_entity(user_id_1, entity_data)

    # Link memories for user 1
    await test_entity_service.link_entity_to_memory(user_id_1, entity.id, 1)
    await test_entity_service.link_entity_to_memory(user_id_1, entity.id, 2)

    # User 1 can see memories
    memory_ids, count, memories = await test_entity_service.get_entity_memories(user_id_1, entity.id)
    assert count == 2
    assert len(memories) == 2

    # User 2 should get NotFoundError (entity not owned by them)
    with pytest.raises(NotFoundError):
        await test_entity_service.get_entity_memories(user_id_2, entity.id)


async def _create_entity(service, user_id, name, entity_type):
    return await service.create_entity(
        user_id, EntityCreate(name=name, entity_type=entity_type, tags=["reverse-lookup"]),
    )


@pytest.mark.asyncio
async def test_get_memory_entities_basic(test_entity_service, mock_entity_repository):
    """Every entity directly linked to the memory is returned with name and type"""
    user_id = uuid4()
    mock_entity_repository.register_memory(user_id, 1)
    org = await _create_entity(test_entity_service, user_id, "Acme", EntityType.ORGANIZATION)
    person = await _create_entity(test_entity_service, user_id, "Sarah", EntityType.INDIVIDUAL)
    await test_entity_service.link_entity_to_memory(user_id, org.id, 1)
    await test_entity_service.link_entity_to_memory(user_id, person.id, 1)

    entity_ids, count, entities = await test_entity_service.get_memory_entities(user_id, 1)

    assert count == 2
    assert entity_ids == sorted([org.id, person.id])
    assert [entity_id for entity_id, _, _ in entities] == entity_ids
    assert {(name, entity_type) for _, name, entity_type in entities} == {
        ("Acme", "Organization"), ("Sarah", "Individual"),
    }


@pytest.mark.asyncio
async def test_get_memory_entities_empty(test_entity_service, mock_entity_repository):
    """An existing memory with no links returns empty results, not an error"""
    user_id = uuid4()
    mock_entity_repository.register_memory(user_id, 7)

    entity_ids, count, entities = await test_entity_service.get_memory_entities(user_id, 7)

    assert (entity_ids, count, entities) == ([], 0, [])


@pytest.mark.asyncio
async def test_get_memory_entities_not_found(test_entity_service):
    """A missing memory raises NotFoundError"""
    with pytest.raises(NotFoundError):
        await test_entity_service.get_memory_entities(uuid4(), 999999)


@pytest.mark.asyncio
async def test_get_memory_entities_excludes_other_memories(test_entity_service, mock_entity_repository):
    """Entities linked only to another memory are not returned"""
    user_id = uuid4()
    mock_entity_repository.register_memory(user_id, 1)
    mock_entity_repository.register_memory(user_id, 2)
    mine = await _create_entity(test_entity_service, user_id, "Mine", EntityType.TEAM)
    other = await _create_entity(test_entity_service, user_id, "Other", EntityType.TEAM)
    await test_entity_service.link_entity_to_memory(user_id, mine.id, 1)
    await test_entity_service.link_entity_to_memory(user_id, other.id, 2)

    entity_ids, _, _ = await test_entity_service.get_memory_entities(user_id, 1)

    assert entity_ids == [mine.id]


@pytest.mark.asyncio
async def test_get_memory_entities_after_unlink(test_entity_service, mock_entity_repository):
    """Link and unlink are reflected in subsequent reads"""
    user_id = uuid4()
    mock_entity_repository.register_memory(user_id, 1)
    first = await _create_entity(test_entity_service, user_id, "First", EntityType.DEVICE)
    second = await _create_entity(test_entity_service, user_id, "Second", EntityType.DEVICE)
    await test_entity_service.link_entity_to_memory(user_id, first.id, 1)
    await test_entity_service.link_entity_to_memory(user_id, second.id, 1)

    await test_entity_service.unlink_entity_from_memory(user_id, first.id, 1)
    entity_ids, count, _ = await test_entity_service.get_memory_entities(user_id, 1)

    assert (entity_ids, count) == ([second.id], 1)


@pytest.mark.asyncio
async def test_get_memory_entities_user_isolation(test_entity_service, mock_entity_repository):
    """Another user's memory is reported as not found"""
    owner_id = uuid4()
    mock_entity_repository.register_memory(owner_id, 1)
    entity = await _create_entity(test_entity_service, owner_id, "Owned", EntityType.ORGANIZATION)
    await test_entity_service.link_entity_to_memory(owner_id, entity.id, 1)

    with pytest.raises(NotFoundError):
        await test_entity_service.get_memory_entities(uuid4(), 1)
