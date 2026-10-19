"""PostgreSQL entity repository: a failing database is logged once and re-raised unchanged.

Every repository method wraps its work in a handler that records the failure with the
user and entity ids as structured log fields and then re-raises the original exception, so
services and routes see the real database error. The database here is a stand-in whose
sessions cannot be opened, which is what a dropped connection looks like to the repository.
"""
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.models.entity_models import (
    EntityCreate,
    EntityRelationshipCreate,
    EntityRelationshipUpdate,
    EntityType,
    EntityUpdate,
)
from app.repositories.postgres.entity_repository import PostgresEntityRepository

LOGGER_NAME = "app.repositories.postgres.entity_repository"
USER_ID = uuid4()


class UnreachableSession:
    """Async context manager whose entry fails like a refused database connection."""

    def __init__(self, error: Exception):
        self._error = error

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, *exc_info) -> bool:
        return False


class UnreachableDatabase:
    """Adapter stand-in: every user session fails with the given error."""

    def __init__(self, error: Exception):
        self._error = error

    def session(self, user_id: UUID) -> UnreachableSession:
        return UnreachableSession(self._error)


Call = Callable[[PostgresEntityRepository], Awaitable[object]]

FAILING_CALLS: list[tuple[str, Call, str, dict[str, object]]] = [
    (
        "create_entity",
        lambda repo: repo.create_entity(
            USER_ID, EntityCreate(name="Acme", entity_type=EntityType.ORGANIZATION),
        ),
        "Failed to create entity",
        {},
    ),
    (
        "get_entity_by_id",
        lambda repo: repo.get_entity_by_id(USER_ID, 7),
        "Failed to get entity 7",
        {"entity_id": 7},
    ),
    (
        "list_entities",
        lambda repo: repo.list_entities(USER_ID),
        "Failed to list entities",
        {},
    ),
    (
        "search_entities",
        lambda repo: repo.search_entities(USER_ID, "acme"),
        "Failed to search entities",
        {"query": "acme"},
    ),
    (
        "update_entity",
        lambda repo: repo.update_entity(USER_ID, 7, EntityUpdate(name="Renamed")),
        "Failed to update entity 7",
        {"entity_id": 7},
    ),
    (
        "delete_entity",
        lambda repo: repo.delete_entity(USER_ID, 7),
        "Failed to delete entity 7",
        {"entity_id": 7},
    ),
    (
        "link_entity_to_memory",
        lambda repo: repo.link_entity_to_memory(USER_ID, 7, 8),
        "Failed to link entity 7 to memory 8",
        {"entity_id": 7, "memory_id": 8},
    ),
    (
        "unlink_entity_from_memory",
        lambda repo: repo.unlink_entity_from_memory(USER_ID, 7, 8),
        "Failed to unlink entity 7 from memory 8",
        {"entity_id": 7, "memory_id": 8},
    ),
    (
        "link_entity_to_project",
        lambda repo: repo.link_entity_to_project(USER_ID, 7, 9),
        "Failed to link entity 7 to project 9",
        {"entity_id": 7, "project_id": 9},
    ),
    (
        "unlink_entity_from_project",
        lambda repo: repo.unlink_entity_from_project(USER_ID, 7, 9),
        "Failed to unlink entity 7 from project 9",
        {"entity_id": 7, "project_id": 9},
    ),
    (
        "create_entity_relationship",
        lambda repo: repo.create_entity_relationship(
            USER_ID,
            EntityRelationshipCreate(
                source_entity_id=1, target_entity_id=2, relationship_type="owns",
            ),
        ),
        "Failed to create entity relationship",
        {},
    ),
    (
        "get_entity_relationships",
        lambda repo: repo.get_entity_relationships(USER_ID, 7),
        "Failed to get relationships for entity 7",
        {"entity_id": 7},
    ),
    (
        "update_entity_relationship",
        lambda repo: repo.update_entity_relationship(
            USER_ID, 5, EntityRelationshipUpdate(strength=0.5),
        ),
        "Failed to update entity relationship 5",
        {"relationship_id": 5},
    ),
    (
        "delete_entity_relationship",
        lambda repo: repo.delete_entity_relationship(USER_ID, 5),
        "Failed to delete entity relationship 5",
        {"relationship_id": 5},
    ),
    (
        "get_all_entity_relationships",
        lambda repo: repo.get_all_entity_relationships(USER_ID),
        "Failed to get all entity relationships",
        {},
    ),
    (
        "get_all_entity_memory_links",
        lambda repo: repo.get_all_entity_memory_links(USER_ID),
        "Failed to get all entity-memory links",
        {},
    ),
    (
        "get_all_entity_project_links",
        lambda repo: repo.get_all_entity_project_links(USER_ID),
        "Failed to get all entity-project links",
        {},
    ),
    (
        "get_all_entity_file_links",
        lambda repo: repo.get_all_entity_file_links(USER_ID),
        "Failed to get all entity-file links",
        {},
    ),
    (
        "get_memory_entities",
        lambda repo: repo.get_memory_entities(USER_ID, 8),
        "Failed to get memory entities",
        {"memory_id": 8},
    ),
    (
        "get_entity_memories",
        lambda repo: repo.get_entity_memories(USER_ID, 7),
        "Failed to get entity memories",
        {"entity_id": 7},
    ),
]


@pytest.fixture
def connection_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))


@pytest.fixture
def entity_repository(connection_error) -> PostgresEntityRepository:
    return PostgresEntityRepository(UnreachableDatabase(connection_error))  # type: ignore[arg-type]


@pytest.fixture
def live_repository_logger(caplog):
    """Capture the repository logger even if an earlier Alembic run disabled it."""
    repository_logger = logging.getLogger(LOGGER_NAME)
    was_disabled = repository_logger.disabled
    repository_logger.disabled = False
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    yield caplog
    repository_logger.disabled = was_disabled


def _records_of_repository(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER_NAME]


def _assert_record_has_fields(record: logging.LogRecord, fields: dict[str, object]) -> None:
    for field, value in fields.items():
        assert getattr(record, field) == value


@pytest.mark.parametrize(
    ("call", "message", "fields"),
    [pytest.param(call, message, fields, id=name) for name, call, message, fields in FAILING_CALLS],
)
async def test_database_failure_is_logged_with_context_and_reraised(
    entity_repository, connection_error, live_repository_logger, call, message, fields,
):
    with pytest.raises(OperationalError) as raised:
        await call(entity_repository)

    assert raised.value is connection_error
    (record,) = _records_of_repository(live_repository_logger)
    assert record.getMessage() == message
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.exc_info[1] is connection_error
    assert record.user_id == str(USER_ID)
    assert record.error == str(connection_error)
    _assert_record_has_fields(record, fields)
