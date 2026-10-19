"""PostgreSQL entity repository: ids of other users, missing rows and constraint errors.

The services look ids up before they call the repository, so these paths are only reached by
calling the repository itself: another user's id, or a row removed between the check and the
write. The repository has to refuse them on its own and leave every existing row untouched.
"""
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from app.exceptions import ConflictError, NotFoundError
from app.models.entity_models import (
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityUpdate,
)
from tests.e2e.pg_repository_world_e2e_helpers import (
    RepositoryWorld,
    build_world,
    new_entity,
    new_memory,
    new_project,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@dataclass
class Scene:
    """Rows of the owner (unlinked) and of a stranger (linked to each other)."""

    world: RepositoryWorld
    entity: int
    other_entity: int
    memory: int
    project: int
    stranger_entity: int
    stranger_other_entity: int
    stranger_memory: int
    stranger_project: int
    stranger_relationship: int
    stranger_memory_title: str


@pytest_asyncio.fixture(loop_scope="session")
async def scene(db_adapter) -> Scene:
    world = await build_world(db_adapter)
    entity = await new_entity(world, world.owner, "Acme")
    other_entity = await new_entity(world, world.owner, "Globex")
    memory = await new_memory(world, world.owner, "Owner memory")
    project = await new_project(world, world.owner, "Owner project")

    stranger_entity = await new_entity(world, world.stranger, "Stranger Org")
    stranger_other_entity = await new_entity(world, world.stranger, "Stranger Partner")
    stranger_memory = await new_memory(world, world.stranger, "Stranger memory")
    stranger_project = await new_project(world, world.stranger, "Stranger project")
    await world.entities.link_entity_to_memory(
        world.stranger, stranger_entity.id, stranger_memory.id,
    )
    await world.entities.link_entity_to_project(
        world.stranger, stranger_entity.id, stranger_project,
    )
    relationship = await world.entities.create_entity_relationship(
        world.stranger,
        EntityRelationshipCreate(
            source_entity_id=stranger_entity.id,
            target_entity_id=stranger_other_entity.id,
            relationship_type="partners_with",
            strength=0.2,
        ),
    )
    return Scene(
        world=world,
        entity=entity.id,
        other_entity=other_entity.id,
        memory=memory.id,
        project=project,
        stranger_entity=stranger_entity.id,
        stranger_other_entity=stranger_other_entity.id,
        stranger_memory=stranger_memory.id,
        stranger_project=stranger_project,
        stranger_relationship=relationship.id,
        stranger_memory_title=stranger_memory.title,
    )


async def _assert_strangers_rows_untouched(scene: Scene) -> None:
    """The stranger's entity, links and relationship are exactly as the fixture left them."""
    repo, stranger = scene.world.entities, scene.world.stranger
    entity = await repo.get_entity_by_id(stranger, scene.stranger_entity)
    assert entity is not None
    assert entity.name == "Stranger Org"
    assert entity.project_ids == [scene.stranger_project]
    assert await repo.get_entity_memories(stranger, scene.stranger_entity) == [
        (scene.stranger_memory, scene.stranger_memory_title),
    ]
    relationships = await repo.get_entity_relationships(stranger, scene.stranger_entity)
    assert [(r.id, r.strength) for r in relationships] == [(scene.stranger_relationship, 0.2)]


async def _assert_owners_entity_unlinked(scene: Scene) -> None:
    repo, owner = scene.world.entities, scene.world.owner
    entity = await repo.get_entity_by_id(owner, scene.entity)
    assert entity is not None
    assert entity.name == "Acme"
    assert not entity.project_ids
    assert await repo.get_entity_memories(owner, scene.entity) == []
    assert await repo.get_entity_relationships(owner, scene.entity) == []


# (id, call, expected NotFoundError message with {scene field} placeholders)
REFUSED_WITH_NOT_FOUND = [
    pytest.param(
        lambda s: s.world.entities.update_entity(
            s.world.owner, s.stranger_entity, EntityUpdate(name="Hijacked"),
        ),
        "Entity {stranger_entity} not found",
        id="update-entity-of-stranger",
    ),
    pytest.param(
        lambda s: s.world.entities.link_entity_to_memory(
            s.world.owner, s.stranger_entity, s.memory,
        ),
        "Entity {stranger_entity} not found",
        id="link-memory-to-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.link_entity_to_memory(
            s.world.owner, s.entity, s.stranger_memory,
        ),
        "Memory {stranger_memory} not found",
        id="link-entity-to-stranger-memory",
    ),
    pytest.param(
        lambda s: s.world.entities.link_entity_to_project(
            s.world.owner, s.stranger_entity, s.project,
        ),
        "Entity {stranger_entity} not found",
        id="link-project-to-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.link_entity_to_project(
            s.world.owner, s.entity, s.stranger_project,
        ),
        "Project {stranger_project} not found",
        id="link-entity-to-stranger-project",
    ),
    pytest.param(
        lambda s: s.world.entities.create_entity_relationship(
            s.world.owner,
            EntityRelationshipCreate(
                source_entity_id=s.stranger_entity,
                target_entity_id=s.entity,
                relationship_type="owns",
            ),
        ),
        "Source entity {stranger_entity} not found",
        id="relationship-from-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.create_entity_relationship(
            s.world.owner,
            EntityRelationshipCreate(
                source_entity_id=s.entity,
                target_entity_id=s.stranger_entity,
                relationship_type="owns",
            ),
        ),
        "Target entity {stranger_entity} not found",
        id="relationship-to-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.get_entity_relationships(s.world.owner, s.stranger_entity),
        "Entity {stranger_entity} not found",
        id="list-relationships-of-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.update_entity_relationship(
            s.world.owner,
            s.stranger_relationship,
            EntityRelationshipUpdate(strength=0.9),
        ),
        "Entity relationship {stranger_relationship} not found",
        id="update-relationship-of-stranger",
    ),
]


@pytest.mark.parametrize(("call", "message"), REFUSED_WITH_NOT_FOUND)
async def test_operation_on_another_users_row_raises_not_found_and_changes_nothing(
    scene, call, message,
):
    with pytest.raises(NotFoundError) as refused:
        await call(scene)

    assert str(refused.value) == message.format(**vars(scene))
    await _assert_strangers_rows_untouched(scene)
    await _assert_owners_entity_unlinked(scene)


REFUSED_WITH_FALSE = [
    pytest.param(
        lambda s: s.world.entities.delete_entity(s.world.owner, s.stranger_entity),
        id="delete-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.delete_entity_relationship(
            s.world.owner, s.stranger_relationship,
        ),
        id="delete-stranger-relationship",
    ),
    pytest.param(
        lambda s: s.world.entities.unlink_entity_from_memory(
            s.world.owner, s.stranger_entity, s.stranger_memory,
        ),
        id="unlink-memory-from-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.unlink_entity_from_memory(
            s.world.owner, s.entity, s.stranger_memory,
        ),
        id="unlink-stranger-memory-from-own-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.unlink_entity_from_project(
            s.world.owner, s.stranger_entity, s.stranger_project,
        ),
        id="unlink-project-from-stranger-entity",
    ),
    pytest.param(
        lambda s: s.world.entities.unlink_entity_from_project(
            s.world.owner, s.entity, s.stranger_project,
        ),
        id="unlink-stranger-project-from-own-entity",
    ),
]


@pytest.mark.parametrize("call", REFUSED_WITH_FALSE)
async def test_removal_involving_another_users_row_returns_false_and_keeps_it(scene, call):
    assert await call(scene) is False

    await _assert_strangers_rows_untouched(scene)


async def test_unlinking_a_memory_that_was_never_linked_returns_false(scene):
    repo, owner = scene.world.entities, scene.world.owner

    assert await repo.unlink_entity_from_memory(owner, scene.entity, scene.memory) is False

    assert await repo.get_entity_memories(owner, scene.entity) == []


async def test_unlinking_a_project_that_was_never_linked_returns_false(scene):
    repo, owner = scene.world.entities, scene.world.owner

    assert await repo.unlink_entity_from_project(owner, scene.entity, scene.project) is False

    entity = await repo.get_entity_by_id(owner, scene.entity)
    assert entity is not None
    assert not entity.project_ids


async def test_unlinking_a_linked_project_removes_only_that_link(scene):
    repo, owner = scene.world.entities, scene.world.owner
    second_project = await new_project(scene.world, owner, "Second project")
    await repo.link_entity_to_project(owner, scene.entity, scene.project)
    await repo.link_entity_to_project(owner, scene.entity, second_project)

    assert await repo.unlink_entity_from_project(owner, scene.entity, scene.project) is True

    entity = await repo.get_entity_by_id(owner, scene.entity)
    assert entity is not None
    assert entity.project_ids == [second_project]
    assert await repo.unlink_entity_from_project(owner, scene.entity, scene.project) is False


async def test_relationships_can_be_filtered_by_type(scene):
    repo, owner = scene.world.entities, scene.world.owner
    for relationship_type in ("owns", "manages"):
        await repo.create_entity_relationship(
            owner,
            EntityRelationshipCreate(
                source_entity_id=scene.entity,
                target_entity_id=scene.other_entity,
                relationship_type=relationship_type,
            ),
        )

    managed = await repo.get_entity_relationships(
        owner, scene.entity, relationship_type="manages",
    )

    assert [(r.source_entity_id, r.target_entity_id, r.relationship_type) for r in managed] == [
        (scene.entity, scene.other_entity, "manages"),
    ]


async def test_duplicate_relationship_is_a_conflict(scene):
    repo, owner = scene.world.entities, scene.world.owner
    created = EntityRelationshipCreate(
        source_entity_id=scene.entity, target_entity_id=scene.other_entity,
        relationship_type="owns",
    )
    await repo.create_entity_relationship(owner, created)

    with pytest.raises(ConflictError, match="already exists between these entities"):
        await repo.create_entity_relationship(owner, created)

    assert len(await repo.get_entity_relationships(owner, scene.entity)) == 1


async def test_other_constraint_violation_on_create_is_not_reported_as_a_duplicate(scene):
    repo, owner = scene.world.entities, scene.world.owner
    nameless = EntityRelationshipCreate.model_construct(
        source_entity_id=scene.entity,
        target_entity_id=scene.other_entity,
        relationship_type=None,
    )

    with pytest.raises(IntegrityError, match="relationship_type"):
        await repo.create_entity_relationship(owner, nameless)

    assert await repo.get_entity_relationships(owner, scene.entity) == []


async def test_other_constraint_violation_on_update_is_not_reported_as_a_duplicate(scene):
    repo, owner = scene.world.entities, scene.world.owner
    relationship = await repo.create_entity_relationship(
        owner,
        EntityRelationshipCreate(
            source_entity_id=scene.entity, target_entity_id=scene.other_entity,
            relationship_type="owns",
        ),
    )
    nameless = EntityRelationshipUpdate.model_construct(relationship_type=None)

    with pytest.raises(IntegrityError, match="relationship_type"):
        await repo.update_entity_relationship(owner, relationship.id, nameless)

    stored = await repo.get_entity_relationships(owner, scene.entity)
    assert [(r.id, r.relationship_type) for r in stored] == [(relationship.id, "owns")]


async def test_deleting_an_owned_entity_removes_its_relationships(scene):
    repo, owner = scene.world.entities, scene.world.owner
    await repo.create_entity_relationship(
        owner,
        EntityRelationshipCreate(
            source_entity_id=scene.entity, target_entity_id=scene.other_entity,
            relationship_type="owns",
        ),
    )

    assert await repo.delete_entity(owner, scene.entity) is True

    assert await repo.get_entity_by_id(owner, scene.entity) is None
    assert await repo.get_entity_relationships(owner, scene.other_entity) == []
