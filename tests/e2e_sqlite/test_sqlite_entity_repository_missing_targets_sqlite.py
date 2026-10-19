"""SqliteEntityRepository behaviour when a referenced row is unknown, foreign or unlinked.

The repository is driven directly against a real in-memory SQLite database (the same stack
the REST and MCP layers use), with two users so ownership filtering is part of the
contract: another user's rows must look exactly like rows that do not exist.
"""
import pytest

from app.exceptions import NotFoundError
from app.models.entity_models import (
    EntityCreate,
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityType,
    EntityUpdate,
)
from app.models.memory_models import MemoryCreate
from app.models.project_models import ProjectCreate, ProjectType
from tests.e2e_sqlite.sqlite_repository_stack_helpers import (
    RepositoryStack,
    open_repository_stack,
)

UNKNOWN_ID = 4242


@pytest.fixture
async def stack(monkeypatch):
    async with open_repository_stack(monkeypatch) as repository_stack:
        yield repository_stack


async def _entity(stack: RepositoryStack, name: str, *, owner: str = "user") -> int:
    user_id = stack.user_id if owner == "user" else stack.other_user_id
    created = await stack.entities.create_entity(
        user_id, EntityCreate(name=name, entity_type=EntityType.ORGANIZATION),
    )
    return created.id


async def _project(stack: RepositoryStack) -> int:
    created = await stack.projects.create_project(
        stack.user_id,
        ProjectCreate(name="p", description="d", project_type=ProjectType.DEVELOPMENT),
    )
    return created.id


async def _memory(stack: RepositoryStack) -> int:
    created = await stack.memories.create_memory(
        stack.user_id,
        MemoryCreate(
            title="m", content="c", context="x", keywords=["k"], tags=["t"], importance=7,
        ),
    )
    return created.id


async def _entity_id_for(stack: RepositoryStack, case: str) -> int:
    """An entity id the acting user cannot see: nonexistent, or owned by someone else."""
    if case == "unknown":
        return UNKNOWN_ID
    return await _entity(stack, "Foreign", owner="other")


UNSEEN_ENTITY_CASES = ["unknown", "foreign"]


@pytest.mark.parametrize("case", UNSEEN_ENTITY_CASES)
async def test_update_entity_reports_unseen_entity_as_not_found(stack, case):
    entity_id = await _entity_id_for(stack, case)

    with pytest.raises(NotFoundError, match=f"Entity {entity_id} not found"):
        await stack.entities.update_entity(
            stack.user_id, entity_id, EntityUpdate(name="Renamed"),
        )

    if case == "foreign":
        untouched = await stack.entities.get_entity_by_id(stack.other_user_id, entity_id)
        assert untouched is not None
        assert untouched.name == "Foreign"


@pytest.mark.parametrize("case", UNSEEN_ENTITY_CASES)
async def test_link_entity_to_memory_reports_unseen_entity_as_not_found(stack, case):
    entity_id = await _entity_id_for(stack, case)
    memory_id = await _memory(stack)

    with pytest.raises(NotFoundError, match=f"Entity {entity_id} not found"):
        await stack.entities.link_entity_to_memory(stack.user_id, entity_id, memory_id)

    assert await stack.entities.get_memory_entities(stack.user_id, memory_id) == []


@pytest.mark.parametrize("case", UNSEEN_ENTITY_CASES)
async def test_link_entity_to_project_reports_unseen_entity_as_not_found(stack, case):
    entity_id = await _entity_id_for(stack, case)
    project_id = await _project(stack)

    with pytest.raises(NotFoundError, match=f"Entity {entity_id} not found"):
        await stack.entities.link_entity_to_project(stack.user_id, entity_id, project_id)

    if case == "foreign":
        untouched = await stack.entities.get_entity_by_id(stack.other_user_id, entity_id)
        assert untouched.project_ids == []


async def test_link_entity_to_project_reports_unknown_project_as_not_found(stack):
    entity_id = await _entity(stack, "Acme")

    with pytest.raises(NotFoundError, match=f"Project {UNKNOWN_ID} not found"):
        await stack.entities.link_entity_to_project(stack.user_id, entity_id, UNKNOWN_ID)

    assert (await stack.entities.get_entity_by_id(stack.user_id, entity_id)).project_ids == []


@pytest.mark.parametrize("case", ["unknown_entity", "unknown_memory", "never_linked", "already_unlinked"])
async def test_unlink_entity_from_memory_returns_false_when_there_is_no_link(stack, case):
    entity_id = await _entity(stack, "Acme")
    memory_id = await _memory(stack)
    if case == "unknown_entity":
        entity_id = UNKNOWN_ID
    elif case == "unknown_memory":
        memory_id = UNKNOWN_ID
    elif case == "already_unlinked":
        await stack.entities.link_entity_to_memory(stack.user_id, entity_id, memory_id)
        assert await stack.entities.unlink_entity_from_memory(stack.user_id, entity_id, memory_id) is True

    assert await stack.entities.unlink_entity_from_memory(stack.user_id, entity_id, memory_id) is False


async def test_unlink_entity_from_memory_removes_the_stored_link(stack):
    entity_id = await _entity(stack, "Acme")
    memory_id = await _memory(stack)
    await stack.entities.link_entity_to_memory(stack.user_id, entity_id, memory_id)

    removed = await stack.entities.unlink_entity_from_memory(stack.user_id, entity_id, memory_id)

    assert removed is True
    assert await stack.entities.get_entity_memories(stack.user_id, entity_id) == []


@pytest.mark.parametrize("case", ["unknown_entity", "unknown_project", "never_linked", "already_unlinked"])
async def test_unlink_entity_from_project_returns_false_when_there_is_no_link(stack, case):
    entity_id = await _entity(stack, "Acme")
    project_id = await _project(stack)
    if case == "unknown_entity":
        entity_id = UNKNOWN_ID
    elif case == "unknown_project":
        project_id = UNKNOWN_ID
    elif case == "already_unlinked":
        await stack.entities.link_entity_to_project(stack.user_id, entity_id, project_id)
        assert await stack.entities.unlink_entity_from_project(stack.user_id, entity_id, project_id) is True

    assert await stack.entities.unlink_entity_from_project(stack.user_id, entity_id, project_id) is False


async def test_unlink_entity_from_project_removes_the_stored_link(stack):
    entity_id = await _entity(stack, "Acme")
    project_id = await _project(stack)
    await stack.entities.link_entity_to_project(stack.user_id, entity_id, project_id)

    removed = await stack.entities.unlink_entity_from_project(stack.user_id, entity_id, project_id)

    assert removed is True
    assert (await stack.entities.get_entity_by_id(stack.user_id, entity_id)).project_ids == []


@pytest.mark.parametrize(
    ("missing_end", "message"),
    [
        ("source", f"Source entity {UNKNOWN_ID} not found"),
        ("target", f"Target entity {UNKNOWN_ID} not found"),
    ],
)
async def test_create_entity_relationship_reports_unknown_endpoint_as_not_found(
    stack, missing_end, message,
):
    existing = await _entity(stack, "Acme")
    source_id, target_id = (
        (UNKNOWN_ID, existing) if missing_end == "source" else (existing, UNKNOWN_ID)
    )

    with pytest.raises(NotFoundError, match=message):
        await stack.entities.create_entity_relationship(
            stack.user_id,
            EntityRelationshipCreate(
                source_entity_id=source_id, target_entity_id=target_id, relationship_type="owns",
            ),
        )

    assert await stack.entities.get_entity_relationships(stack.user_id, existing) == []


async def test_get_entity_relationships_reports_unknown_entity_as_not_found(stack):
    with pytest.raises(NotFoundError, match=f"Entity {UNKNOWN_ID} not found"):
        await stack.entities.get_entity_relationships(stack.user_id, UNKNOWN_ID)


async def test_get_entity_relationships_filters_by_relationship_type(stack):
    acme = await _entity(stack, "Acme")
    globex = await _entity(stack, "Globex")
    for relationship_type in ("owns", "partners_with"):
        await stack.entities.create_entity_relationship(
            stack.user_id,
            EntityRelationshipCreate(
                source_entity_id=acme, target_entity_id=globex, relationship_type=relationship_type,
            ),
        )

    owned = await stack.entities.get_entity_relationships(
        stack.user_id, acme, relationship_type="owns",
    )
    everything = await stack.entities.get_entity_relationships(stack.user_id, acme)

    assert [r.relationship_type for r in owned] == ["owns"]
    assert sorted(r.relationship_type for r in everything) == ["owns", "partners_with"]


async def test_update_entity_relationship_reports_unknown_relationship_as_not_found(stack):
    with pytest.raises(NotFoundError, match=f"Entity relationship {UNKNOWN_ID} not found"):
        await stack.entities.update_entity_relationship(
            stack.user_id, UNKNOWN_ID, EntityRelationshipUpdate(strength=0.5),
        )


async def test_delete_entity_relationship_returns_false_for_unknown_relationship(stack):
    assert await stack.entities.delete_entity_relationship(stack.user_id, UNKNOWN_ID) is False


async def test_delete_entity_relationship_leaves_another_users_relationship_alone(stack):
    acme = await _entity(stack, "Acme", owner="other")
    globex = await _entity(stack, "Globex", owner="other")
    foreign = await stack.entities.create_entity_relationship(
        stack.other_user_id,
        EntityRelationshipCreate(
            source_entity_id=acme, target_entity_id=globex, relationship_type="owns",
        ),
    )

    deleted = await stack.entities.delete_entity_relationship(stack.user_id, foreign.id)

    assert deleted is False
    survivors = await stack.entities.get_entity_relationships(stack.other_user_id, acme)
    assert [r.id for r in survivors] == [foreign.id]
