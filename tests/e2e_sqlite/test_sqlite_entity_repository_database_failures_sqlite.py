"""SqliteEntityRepository when the database fails underneath it.

Every repository method must let a database error through unchanged (callers translate it
into a 5xx/ToolError) and leave one ERROR record naming the user and the operation, so an
operator can tell which call failed. Constraint violations other than the duplicate
relationship check must not be mistaken for a duplicate (HTTP 409).
"""
import logging
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.exceptions import ConflictError
from app.models.entity_models import (
    EntityCreate,
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityType,
    EntityUpdate,
)
from app.repositories.sqlite.entity_repository import SqliteEntityRepository
from tests.e2e_sqlite.sqlite_repository_stack_helpers import (
    FaultyAdapter,
    RepositoryStack,
    foreign_key_violation,
    locked_adapter,
    open_repository_stack,
)

REPOSITORY_LOGGER = "app.repositories.sqlite.entity_repository"

EntityCall = Callable[[SqliteEntityRepository, "RepositoryStack"], Awaitable[object]]


@pytest.fixture
async def stack(monkeypatch):
    async with open_repository_stack(monkeypatch) as repository_stack:
        yield repository_stack


@pytest.fixture
def repository_errors(caplog, monkeypatch):
    """ERROR records of the entity repository (re-enabling its logger after migrations)."""
    monkeypatch.setattr(logging.getLogger(REPOSITORY_LOGGER), "disabled", False)
    caplog.set_level(logging.ERROR, logger=REPOSITORY_LOGGER)

    def records() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.name == REPOSITORY_LOGGER]

    return records


ENTITY_OPERATIONS: dict[str, tuple[EntityCall, str]] = {
    "create_entity": (
        lambda repo, s: repo.create_entity(
            s.user_id, EntityCreate(name="Acme", entity_type=EntityType.ORGANIZATION),
        ),
        "Failed to create entity",
    ),
    "get_entity_by_id": (
        lambda repo, s: repo.get_entity_by_id(s.user_id, 7),
        "Failed to get entity 7",
    ),
    "list_entities": (
        lambda repo, s: repo.list_entities(s.user_id),
        "Failed to list entities",
    ),
    "search_entities": (
        lambda repo, s: repo.search_entities(s.user_id, "acme"),
        "Failed to search entities",
    ),
    "update_entity": (
        lambda repo, s: repo.update_entity(s.user_id, 7, EntityUpdate(name="Renamed")),
        "Failed to update entity 7",
    ),
    "delete_entity": (
        lambda repo, s: repo.delete_entity(s.user_id, 7),
        "Failed to delete entity 7",
    ),
    "link_entity_to_memory": (
        lambda repo, s: repo.link_entity_to_memory(s.user_id, 7, 8),
        "Failed to link entity 7 to memory 8",
    ),
    "unlink_entity_from_memory": (
        lambda repo, s: repo.unlink_entity_from_memory(s.user_id, 7, 8),
        "Failed to unlink entity 7 from memory 8",
    ),
    "link_entity_to_project": (
        lambda repo, s: repo.link_entity_to_project(s.user_id, 7, 9),
        "Failed to link entity 7 to project 9",
    ),
    "unlink_entity_from_project": (
        lambda repo, s: repo.unlink_entity_from_project(s.user_id, 7, 9),
        "Failed to unlink entity 7 from project 9",
    ),
    "create_entity_relationship": (
        lambda repo, s: repo.create_entity_relationship(
            s.user_id,
            EntityRelationshipCreate(
                source_entity_id=7, target_entity_id=8, relationship_type="owns",
            ),
        ),
        "Failed to create entity relationship",
    ),
    "get_entity_relationships": (
        lambda repo, s: repo.get_entity_relationships(s.user_id, 7),
        "Failed to get relationships for entity 7",
    ),
    "update_entity_relationship": (
        lambda repo, s: repo.update_entity_relationship(
            s.user_id, 5, EntityRelationshipUpdate(strength=0.5),
        ),
        "Failed to update entity relationship 5",
    ),
    "delete_entity_relationship": (
        lambda repo, s: repo.delete_entity_relationship(s.user_id, 5),
        "Failed to delete entity relationship 5",
    ),
    "get_all_entity_relationships": (
        lambda repo, s: repo.get_all_entity_relationships(s.user_id),
        "Failed to get all entity relationships",
    ),
    "get_all_entity_memory_links": (
        lambda repo, s: repo.get_all_entity_memory_links(s.user_id),
        "Failed to get all entity-memory links",
    ),
    "get_all_entity_project_links": (
        lambda repo, s: repo.get_all_entity_project_links(s.user_id),
        "Failed to get all entity-project links",
    ),
    "get_all_entity_file_links": (
        lambda repo, s: repo.get_all_entity_file_links(s.user_id),
        "Failed to get all entity-file links",
    ),
    "get_memory_entities": (
        lambda repo, s: repo.get_memory_entities(s.user_id, 8),
        "Failed to get memory entities",
    ),
    "get_entity_memories": (
        lambda repo, s: repo.get_entity_memories(s.user_id, 7),
        "Failed to get entity memories",
    ),
}


@pytest.mark.parametrize(
    ("operation", "message"),
    [pytest.param(call, message, id=name) for name, (call, message) in ENTITY_OPERATIONS.items()],
)
async def test_locked_database_error_is_logged_and_re_raised(
    stack, repository_errors, monkeypatch, operation, message,
):
    monkeypatch.setattr(stack.entities, "db_adapter", locked_adapter(stack.adapter))

    with pytest.raises(OperationalError, match="database is locked"):
        await operation(stack.entities, stack)

    [record] = repository_errors()
    assert record.getMessage() == message
    assert record.levelno == logging.ERROR
    assert record.user_id == str(stack.user_id)
    assert "database is locked" in record.error
    assert record.exc_info is not None


async def _two_entities(stack: RepositoryStack) -> tuple[int, int]:
    ids = []
    for name in ("Acme", "Globex"):
        created = await stack.entities.create_entity(
            stack.user_id, EntityCreate(name=name, entity_type=EntityType.ORGANIZATION),
        )
        ids.append(created.id)
    return ids[0], ids[1]


async def test_create_relationship_surfaces_non_duplicate_constraint_violation(
    stack, repository_errors, monkeypatch,
):
    acme, globex = await _two_entities(stack)
    monkeypatch.setattr(
        stack.entities,
        "db_adapter",
        FaultyAdapter(stack.adapter, {"commit": foreign_key_violation()}),
    )

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed") as raised:
        await stack.entities.create_entity_relationship(
            stack.user_id,
            EntityRelationshipCreate(
                source_entity_id=acme, target_entity_id=globex, relationship_type="owns",
            ),
        )

    assert not isinstance(raised.value, ConflictError)
    [record] = repository_errors()
    assert record.getMessage() == "Failed to create entity relationship"
    healthy = SqliteEntityRepository(db_adapter=stack.adapter)
    assert await healthy.get_all_entity_relationships(stack.user_id) == []


async def test_update_relationship_surfaces_non_duplicate_constraint_violation(
    stack, repository_errors, monkeypatch,
):
    acme, globex = await _two_entities(stack)
    relationship = await stack.entities.create_entity_relationship(
        stack.user_id,
        EntityRelationshipCreate(
            source_entity_id=acme, target_entity_id=globex, relationship_type="owns", strength=0.2,
        ),
    )
    monkeypatch.setattr(
        stack.entities,
        "db_adapter",
        FaultyAdapter(stack.adapter, {"commit": foreign_key_violation()}),
    )

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed") as raised:
        await stack.entities.update_entity_relationship(
            stack.user_id, relationship.id, EntityRelationshipUpdate(strength=0.9),
        )

    assert not isinstance(raised.value, ConflictError)
    [record] = repository_errors()
    assert record.getMessage() == f"Failed to update entity relationship {relationship.id}"
    healthy = SqliteEntityRepository(db_adapter=stack.adapter)
    [stored] = await healthy.get_entity_relationships(stack.user_id, acme)
    assert stored.strength == pytest.approx(0.2)
