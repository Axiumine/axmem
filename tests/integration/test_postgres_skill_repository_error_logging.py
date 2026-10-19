"""PostgreSQL skill repository: a failing database is logged once and re-raised unchanged.

Every repository method wraps its work in a handler that records the failure with the user
and skill ids as structured log fields and then re-raises the original exception, so services
and routes see the real database error. The database here is a stand-in whose sessions cannot
be opened, which is what a dropped connection looks like to the repository.
"""
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.models.skill_models import SkillUpdate
from app.repositories.postgres.skill_repository import PostgresSkillRepository

LOGGER_NAME = "app.repositories.postgres.skill_repository"
USER_ID = uuid4()


def _connection_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))


ERROR_TEXT = str(_connection_error())


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


class FixedEmbeddings:
    """Embedding stand-in: the same small vector for any text."""

    async def generate_embedding(self, text: str) -> list[float]:
        return [0.5, 0.5, 0.5]


Call = Callable[[PostgresSkillRepository], Awaitable[object]]

FAILING_CALLS: list[tuple[str, Call, str, dict[str, object]]] = [
    (
        "get_skill_by_id",
        lambda repo: repo.get_skill_by_id(USER_ID, 7),
        "Failed to get skill 7",
        {"skill_id": 7, "error": ERROR_TEXT},
    ),
    (
        "list_skills",
        lambda repo: repo.list_skills(USER_ID),
        "Failed to list skills",
        {"error": ERROR_TEXT},
    ),
    (
        "update_skill",
        lambda repo: repo.update_skill(USER_ID, 7, SkillUpdate(name="renamed-skill")),
        "Failed to update skill 7",
        {"skill_id": 7, "error": ERROR_TEXT},
    ),
    (
        "delete_skill",
        lambda repo: repo.delete_skill(USER_ID, 7),
        "Failed to delete skill 7",
        {"skill_id": 7, "error": ERROR_TEXT},
    ),
    (
        "search_skills",
        lambda repo: repo.search_skills(USER_ID, "pdf extraction"),
        "Failed to search skills",
        {"query": "pdf extraction", "error": ERROR_TEXT},
    ),
    (
        "link_skill_to_memory",
        lambda repo: repo.link_skill_to_memory(USER_ID, 7, 8),
        "Failed to link skill 7 to memory 8",
        {"skill_id": 7, "memory_id": 8, "error": ERROR_TEXT},
    ),
    (
        "unlink_skill_from_memory",
        lambda repo: repo.unlink_skill_from_memory(USER_ID, 7, 8),
        "Failed to unlink skill 7 from memory 8",
        {"skill_id": 7, "memory_id": 8, "error": ERROR_TEXT},
    ),
    (
        "link_skill_to_file",
        lambda repo: repo.link_skill_to_file(USER_ID, 7, 9),
        "Failed to link skill 7 to file 9",
        {"skill_id": 7, "file_id": 9, "error": ERROR_TEXT},
    ),
    (
        "unlink_skill_from_file",
        lambda repo: repo.unlink_skill_from_file(USER_ID, 7, 9),
        "Failed to unlink skill 7 from file 9",
        {"skill_id": 7, "file_id": 9, "error": ERROR_TEXT},
    ),
    (
        "link_skill_to_code_artifact",
        lambda repo: repo.link_skill_to_code_artifact(USER_ID, 7, 10),
        "Failed to link skill 7 to code artifact 10",
        {"skill_id": 7, "code_artifact_id": 10, "error": ERROR_TEXT},
    ),
    (
        "unlink_skill_from_code_artifact",
        lambda repo: repo.unlink_skill_from_code_artifact(USER_ID, 7, 10),
        "Failed to unlink skill 7 from code artifact 10",
        {"skill_id": 7, "code_artifact_id": 10, "error": ERROR_TEXT},
    ),
    (
        "link_skill_to_document",
        lambda repo: repo.link_skill_to_document(USER_ID, 7, 11),
        "Failed to link skill 7 to document 11",
        {"skill_id": 7, "document_id": 11, "error": ERROR_TEXT},
    ),
    (
        "unlink_skill_from_document",
        lambda repo: repo.unlink_skill_from_document(USER_ID, 7, 11),
        "Failed to unlink skill 7 from document 11",
        {"skill_id": 7, "document_id": 11, "error": ERROR_TEXT},
    ),
    (
        "get_skill_links",
        lambda repo: repo.get_skill_links(USER_ID, 7),
        "Failed to get skill links",
        {"skill_id": 7, "error": ERROR_TEXT},
    ),
    (
        "get_all_skill_file_links",
        lambda repo: repo.get_all_skill_file_links(USER_ID),
        "Failed to get all skill-file links",
        {},
    ),
    (
        "get_all_skill_code_artifact_links",
        lambda repo: repo.get_all_skill_code_artifact_links(USER_ID),
        "Failed to get all skill-code_artifact links",
        {},
    ),
    (
        "get_all_skill_document_links",
        lambda repo: repo.get_all_skill_document_links(USER_ID),
        "Failed to get all skill-document links",
        {},
    ),
]


@pytest.fixture
def connection_error() -> OperationalError:
    return _connection_error()


@pytest.fixture
def skill_repository(connection_error) -> PostgresSkillRepository:
    return PostgresSkillRepository(
        UnreachableDatabase(connection_error),  # type: ignore[arg-type]
        FixedEmbeddings(),
    )


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
    skill_repository, connection_error, live_repository_logger, call, message, fields,
):
    with pytest.raises(OperationalError) as raised:
        await call(skill_repository)

    assert raised.value is connection_error
    (record,) = _records_of_repository(live_repository_logger)
    assert record.getMessage() == message
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.exc_info[1] is connection_error
    assert record.user_id == str(USER_ID)
    _assert_record_has_fields(record, fields)
