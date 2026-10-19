"""SqliteSkillRepository when the database fails underneath it.

Every repository method must let a database error through unchanged (callers translate it
into a 5xx/ToolError) and leave one ERROR record naming the user and the skill involved,
so an operator can tell which call failed. Only the unique-name violation is translated
(into ConflictError); any other failure of the same write must surface as itself.
"""
import logging
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import OperationalError

from app.exceptions import ConflictError
from app.models.skill_models import SkillCreate, SkillUpdate
from app.repositories.sqlite.skill_repository import SqliteSkillRepository
from tests.e2e_sqlite.sqlite_repository_stack_helpers import (
    FaultyAdapter,
    RepositoryStack,
    locked_adapter,
    locked_database_error,
    open_repository_stack,
)

REPOSITORY_LOGGER = "app.repositories.sqlite.skill_repository"

SkillCall = Callable[[SqliteSkillRepository, RepositoryStack], Awaitable[object]]


@pytest.fixture
async def stack(monkeypatch):
    async with open_repository_stack(monkeypatch) as repository_stack:
        yield repository_stack


@pytest.fixture
def repository_errors(caplog, monkeypatch):
    """ERROR records of the skill repository (re-enabling its logger after migrations)."""
    monkeypatch.setattr(logging.getLogger(REPOSITORY_LOGGER), "disabled", False)
    caplog.set_level(logging.ERROR, logger=REPOSITORY_LOGGER)

    def records() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.name == REPOSITORY_LOGGER]

    return records


SKILL_OPERATIONS: dict[str, tuple[SkillCall, str, dict[str, int]]] = {
    "create_skill": (
        lambda repo, s: repo.create_skill(
            s.user_id, SkillCreate(name="new-skill", description="d", content="body"),
        ),
        "Failed to create skill",
        {},
    ),
    "skill_name_exists": (
        lambda repo, s: repo.skill_name_exists(s.user_id, "new-skill"),
        "Failed to check skill name existence",
        {},
    ),
    "get_skill_by_id": (
        lambda repo, s: repo.get_skill_by_id(s.user_id, 7),
        "Failed to get skill 7",
        {"skill_id": 7},
    ),
    "list_skills": (
        lambda repo, s: repo.list_skills(s.user_id),
        "Failed to list skills",
        {},
    ),
    "update_skill": (
        lambda repo, s: repo.update_skill(s.user_id, 7, SkillUpdate(content="changed")),
        "Failed to update skill 7",
        {"skill_id": 7},
    ),
    "delete_skill": (
        lambda repo, s: repo.delete_skill(s.user_id, 7),
        "Failed to delete skill 7",
        {"skill_id": 7},
    ),
    "search_skills": (
        lambda repo, s: repo.search_skills(s.user_id, "deploy"),
        "Failed to search skills",
        {},
    ),
    "link_skill_to_memory": (
        lambda repo, s: repo.link_skill_to_memory(s.user_id, 7, 8),
        "Failed to link skill 7 to memory 8",
        {"skill_id": 7, "memory_id": 8},
    ),
    "unlink_skill_from_memory": (
        lambda repo, s: repo.unlink_skill_from_memory(s.user_id, 7, 8),
        "Failed to unlink skill 7 from memory 8",
        {"skill_id": 7, "memory_id": 8},
    ),
    "link_skill_to_file": (
        lambda repo, s: repo.link_skill_to_file(s.user_id, 7, 8),
        "Failed to link skill 7 to file 8",
        {"skill_id": 7, "file_id": 8},
    ),
    "unlink_skill_from_file": (
        lambda repo, s: repo.unlink_skill_from_file(s.user_id, 7, 8),
        "Failed to unlink skill 7 from file 8",
        {"skill_id": 7, "file_id": 8},
    ),
    "link_skill_to_code_artifact": (
        lambda repo, s: repo.link_skill_to_code_artifact(s.user_id, 7, 8),
        "Failed to link skill 7 to code artifact 8",
        {"skill_id": 7, "code_artifact_id": 8},
    ),
    "unlink_skill_from_code_artifact": (
        lambda repo, s: repo.unlink_skill_from_code_artifact(s.user_id, 7, 8),
        "Failed to unlink skill 7 from code artifact 8",
        {"skill_id": 7, "code_artifact_id": 8},
    ),
    "link_skill_to_document": (
        lambda repo, s: repo.link_skill_to_document(s.user_id, 7, 8),
        "Failed to link skill 7 to document 8",
        {"skill_id": 7, "document_id": 8},
    ),
    "unlink_skill_from_document": (
        lambda repo, s: repo.unlink_skill_from_document(s.user_id, 7, 8),
        "Failed to unlink skill 7 from document 8",
        {"skill_id": 7, "document_id": 8},
    ),
    "get_skill_links": (
        lambda repo, s: repo.get_skill_links(s.user_id, 7),
        "Failed to get skill links",
        {"skill_id": 7},
    ),
    "get_all_skill_file_links": (
        lambda repo, s: repo.get_all_skill_file_links(s.user_id),
        "Failed to get all skill-file links",
        {},
    ),
    "get_all_skill_code_artifact_links": (
        lambda repo, s: repo.get_all_skill_code_artifact_links(s.user_id),
        "Failed to get all skill-code_artifact links",
        {},
    ),
    "get_all_skill_document_links": (
        lambda repo, s: repo.get_all_skill_document_links(s.user_id),
        "Failed to get all skill-document links",
        {},
    ),
}


@pytest.mark.parametrize(
    ("operation", "message", "context"),
    [
        pytest.param(call, message, context, id=name)
        for name, (call, message, context) in SKILL_OPERATIONS.items()
    ],
)
async def test_locked_database_error_is_logged_and_re_raised(
    stack, repository_errors, monkeypatch, operation, message, context,
):
    monkeypatch.setattr(stack.skills, "db_adapter", locked_adapter(stack.adapter))

    with pytest.raises(OperationalError, match="database is locked") as raised:
        await operation(stack.skills, stack)

    assert not isinstance(raised.value, ConflictError)
    [record] = repository_errors()
    assert record.getMessage() == message
    assert record.levelno == logging.ERROR
    assert record.user_id == str(stack.user_id)
    assert {key: getattr(record, key) for key in context} == context
    assert record.exc_info is not None


async def test_commit_failure_rolls_back_the_skill_update(stack, monkeypatch):
    created = await stack.skills.create_skill(
        stack.user_id, SkillCreate(name="stable-skill", description="d", content="original"),
    )
    monkeypatch.setattr(
        stack.skills,
        "db_adapter",
        FaultyAdapter(stack.adapter, {"commit": locked_database_error()}),
    )

    with pytest.raises(OperationalError, match="database is locked"):
        await stack.skills.update_skill(stack.user_id, created.id, SkillUpdate(content="changed"))

    healthy = SqliteSkillRepository(
        db_adapter=stack.adapter, embedding_adapter=stack.skills.embedding_adapter,
    )
    stored = await healthy.get_skill_by_id(stack.user_id, created.id)
    assert stored is not None
    assert stored.content == "original"
