"""PostgreSQL memory repository: lookups, links, usage and obsolescence edges.

Behaviour of the repository operations that the services normally shield from odd input:
unknown ids, empty id lists, optional filters left out and ids no memory can have. Each test
creates its memories through the repository and reads the outcome back through it.
"""
import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.exceptions import InvalidStateTransitionError, NotFoundError
from app.models.project_models import ProjectCreate, ProjectType
from app.repositories.postgres.memory_repository import PostgresMemoryRepository
from app.repositories.postgres.project_repository import PostgresProjectRepository
from tests.e2e.pg_memory_repository_helpers import (
    HashingEmbeddings,
    create_user,
    memory_data,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

UNKNOWN_ID = 987654
BEYOND_INT4 = 2**31


@pytest_asyncio.fixture(loop_scope="session")
async def owner(db_adapter):
    return await create_user(db_adapter, "memory-edges-e2e")


@pytest_asyncio.fixture(loop_scope="session")
async def memories(db_adapter):
    return PostgresMemoryRepository(db_adapter, HashingEmbeddings())


@pytest_asyncio.fixture(loop_scope="session")
async def project_id(db_adapter, owner):
    project = await PostgresProjectRepository(db_adapter).create_project(
        owner, ProjectCreate(name="p", description="d", project_type=ProjectType.DEVELOPMENT),
    )
    return project.id


async def test_getting_an_unknown_memory_raises_not_found(memories, owner):
    with pytest.raises(NotFoundError, match=f"Memory with id {UNKNOWN_ID} not found"):
        await memories.get_memory_by_id(owner, UNKNOWN_ID)


# ---- links ----


async def test_linked_memories_come_back_most_important_first(memories, owner):
    center = await memories.create_memory(owner, memory_data("center"))
    minor = await memories.create_memory(owner, memory_data("minor", importance=3))
    major = await memories.create_memory(owner, memory_data("major", importance=9))
    assert await memories.create_links_batch(owner, center.id, [minor.id, major.id]) == [
        minor.id, major.id,
    ]

    linked = await memories.get_linked_memories(
        owner, center.id, project_ids=None, max_links=5, exclude_ids=None,
    )

    assert [m.title for m in linked] == ["major", "minor"]


async def test_linked_memories_can_be_restricted_to_projects(memories, owner, project_id):
    center = await memories.create_memory(owner, memory_data("center"))
    inside = await memories.create_memory(owner, memory_data("inside", project_ids=[project_id]))
    outside = await memories.create_memory(owner, memory_data("outside"))
    await memories.create_links_batch(owner, center.id, [inside.id, outside.id])

    linked = await memories.get_linked_memories(
        owner, center.id, project_ids=[project_id], max_links=5,
    )

    assert [m.id for m in linked] == [inside.id]


async def test_linking_from_an_unknown_source_raises_not_found(memories, owner):
    target = await memories.create_memory(owner, memory_data("target"))

    with pytest.raises(NotFoundError, match=f"Source memory with id {UNKNOWN_ID} not found"):
        await memories.create_link(owner, UNKNOWN_ID, target.id)


async def test_linking_the_same_pair_twice_is_rejected_as_duplicate(memories, owner):
    first = await memories.create_memory(owner, memory_data("first"))
    second = await memories.create_memory(owner, memory_data("second"))
    await memories.create_link(owner, first.id, second.id)

    with pytest.raises(IntegrityError):
        await memories.create_link(owner, second.id, first.id)


async def test_batch_linking_skips_self_links_unknown_and_unstorable_ids(memories, owner):
    source = await memories.create_memory(owner, memory_data("source"))
    target = await memories.create_memory(owner, memory_data("target"))

    created = await memories.create_links_batch(
        owner, source.id, [source.id, UNKNOWN_ID, BEYOND_INT4, -1, target.id],
    )

    assert created == [target.id]
    linked = await memories.get_linked_memories(owner, source.id, project_ids=None)
    assert [m.id for m in linked] == [target.id]


@pytest.mark.parametrize("source_id", [0, BEYOND_INT4], ids=["zero", "beyond-int4"])
async def test_batch_linking_from_an_impossible_source_id_creates_nothing(
    memories, owner, source_id,
):
    target = await memories.create_memory(owner, memory_data("target"))

    assert await memories.create_links_batch(owner, source_id, [target.id]) == []


async def test_batch_linking_to_no_targets_creates_nothing(memories, owner):
    source = await memories.create_memory(owner, memory_data("source"))

    assert await memories.create_links_batch(owner, source.id, []) == []


# ---- usage ----


async def test_recording_access_for_no_memories_updates_nothing(memories, owner):
    assert await memories.record_memory_access(owner, []) == 0


# ---- obsolescence ----


async def test_obsoleting_an_unknown_memory_raises_not_found(memories, owner):
    with pytest.raises(NotFoundError, match=f"Memory {UNKNOWN_ID} not found"):
        await memories.mark_obsolete(owner, UNKNOWN_ID, reason="gone")


async def test_obsoleting_a_memory_id_beyond_int4_raises_not_found(memories, owner):
    with pytest.raises(NotFoundError, match=f"Memory {BEYOND_INT4} not found"):
        await memories.mark_obsolete(owner, BEYOND_INT4, reason="gone")


async def test_obsoleting_an_already_obsolete_memory_is_an_invalid_transition(memories, owner):
    memory = await memories.create_memory(owner, memory_data("once"))
    assert await memories.mark_obsolete(owner, memory.id, reason="first") is True

    with pytest.raises(InvalidStateTransitionError, match=f"Memory {memory.id} is already obsolete"):
        await memories.mark_obsolete(owner, memory.id, reason="second")

    stored = await memories.get_memory_by_id(owner, memory.id)
    assert stored.obsolete_reason == "first"


async def test_restoring_an_obsolete_memory_clears_its_obsolescence(memories, owner):
    successor = await memories.create_memory(owner, memory_data("successor"))
    memory = await memories.create_memory(owner, memory_data("old"))
    await memories.mark_obsolete(owner, memory.id, reason="replaced", superseded_by=successor.id)

    restored = await memories.restore_obsolete(owner, memory.id)

    assert restored is True
    stored = await memories.get_memory_by_id(owner, memory.id)
    assert (stored.is_obsolete, stored.obsolete_reason, stored.superseded_by) == (False, None, None)
    assert stored.obsoleted_at is None


async def test_restoring_a_memory_that_is_not_obsolete_changes_nothing(memories, owner):
    memory = await memories.create_memory(owner, memory_data("active"))

    assert await memories.restore_obsolete(owner, memory.id) is False
    assert await memories.restore_obsolete(owner, UNKNOWN_ID) is False
