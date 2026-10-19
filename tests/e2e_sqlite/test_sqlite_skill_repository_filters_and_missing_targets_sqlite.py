"""SqliteSkillRepository filtering, search scoping and unknown-target handling.

The repository is driven directly against a real in-memory SQLite database with sqlite-vec,
with two users so ownership filtering is part of the contract: another user's rows must
look exactly like rows that do not exist.
"""
import base64
import logging

import pytest
from sqlalchemy import func, select

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.code_artifact_models import CodeArtifactCreate
from app.models.document_models import DocumentCreate
from app.models.file_models import FileCreate
from app.models.memory_models import MemoryCreate
from app.models.project_models import ProjectCreate, ProjectType
from app.models.skill_models import SkillCreate, SkillUpdate
from app.repositories.sqlite.sqlite_tables import (
    skill_code_artifact_association,
    skill_document_association,
    skill_file_association,
)
from tests.e2e_sqlite.sqlite_repository_stack_helpers import (
    RepositoryStack,
    open_repository_stack,
)

UNKNOWN_ID = 4242


@pytest.fixture
async def stack(monkeypatch):
    async with open_repository_stack(monkeypatch) as repository_stack:
        yield repository_stack


async def _skill(stack: RepositoryStack, name: str, *, owner: str = "user", **fields) -> int:
    user_id = stack.user_id if owner == "user" else stack.other_user_id
    created = await stack.skills.create_skill(
        user_id, SkillCreate(name=name, description=f"{name} description", content="body", **fields),
    )
    return created.id


async def _project(stack: RepositoryStack, name: str = "p") -> int:
    created = await stack.projects.create_project(
        stack.user_id,
        ProjectCreate(name=name, description="d", project_type=ProjectType.DEVELOPMENT),
    )
    return created.id


async def _stored_skill_link_count(stack: RepositoryStack) -> int:
    """Rows across the skill-file, skill-code-artifact and skill-document link tables."""
    tables = (skill_file_association, skill_code_artifact_association, skill_document_association)
    async with stack.adapter.session(stack.user_id) as session:
        return sum([await session.scalar(select(func.count()).select_from(t)) for t in tables])


async def test_list_skills_filters_by_project(stack):
    project_id = await _project(stack)
    await _skill(stack, "in-project", project_id=project_id)
    await _skill(stack, "elsewhere")

    listed = await stack.skills.list_skills(stack.user_id, project_id=project_id)

    assert [s.name for s in listed] == ["in-project"]


async def test_list_skills_filters_by_minimum_importance(stack):
    await _skill(stack, "minor", importance=3)
    await _skill(stack, "critical", importance=9)
    await _skill(stack, "borderline", importance=7)

    listed = await stack.skills.list_skills(stack.user_id, importance_threshold=7)

    assert sorted(s.name for s in listed) == ["borderline", "critical"]


@pytest.mark.parametrize("case", ["unknown", "foreign"])
async def test_update_skill_reports_unseen_skill_as_not_found(stack, case):
    skill_id = UNKNOWN_ID if case == "unknown" else await _skill(stack, "theirs", owner="other")

    with pytest.raises(NotFoundError, match=f"Skill {skill_id} not found"):
        await stack.skills.update_skill(stack.user_id, skill_id, SkillUpdate(content="changed"))

    if case == "foreign":
        untouched = await stack.skills.get_skill_by_id(stack.other_user_id, skill_id)
        assert untouched is not None
        assert untouched.content == "body"


async def test_update_skill_replaces_metadata(stack):
    skill_id = await _skill(stack, "tagged", metadata={"owner": "team-a", "tier": "gold"})

    updated = await stack.skills.update_skill(
        stack.user_id, skill_id, SkillUpdate(metadata={"owner": "team-b"}),
    )

    assert updated.metadata == {"owner": "team-b"}
    stored = await stack.skills.get_skill_by_id(stack.user_id, skill_id)
    assert stored is not None
    assert stored.metadata == {"owner": "team-b"}


async def test_search_skills_returns_nothing_when_user_has_no_skills(stack):
    await _skill(stack, "someone-elses", owner="other")

    assert await stack.skills.search_skills(stack.user_id, "anything") == []


async def test_search_skills_is_scoped_to_project(stack):
    project_id = await _project(stack)
    await _skill(stack, "in-project", project_id=project_id)
    await _skill(stack, "elsewhere")

    scoped = await stack.skills.search_skills(stack.user_id, "skill", project_id=project_id)
    unscoped = await stack.skills.search_skills(stack.user_id, "skill")

    assert [s.name for s in scoped] == ["in-project"]
    assert sorted(s.name for s in unscoped) == ["elsewhere", "in-project"]


async def _create_target(stack: RepositoryStack, kind: str) -> int:
    uid = stack.user_id
    if kind == "memory":
        created = await stack.memories.create_memory(
            uid,
            MemoryCreate(
                title="m", content="c", context="x", keywords=["k"], tags=["t"], importance=7,
            ),
        )
    elif kind == "file":
        created = await stack.files.create_file(
            uid,
            FileCreate(
                filename="a.txt",
                description="d",
                data=base64.b64encode(b"hello").decode(),
                mime_type="text/plain",
            ),
        )
    elif kind == "code_artifact":
        created = await stack.code_artifacts.create_code_artifact(
            uid, CodeArtifactCreate(title="t", description="d", code="print(1)", language="python"),
        )
    else:
        created = await stack.documents.create_document(
            uid, DocumentCreate(title="t", description="d", content="body"),
        )
    return created.id


LINK_KINDS = {
    "memory": ("link_skill_to_memory", "Memory"),
    "file": ("link_skill_to_file", "File"),
    "code_artifact": ("link_skill_to_code_artifact", "Code artifact"),
    "document": ("link_skill_to_document", "Document"),
}


@pytest.mark.parametrize("kind", list(LINK_KINDS))
async def test_linking_unknown_skill_is_not_found_and_creates_no_link(stack, kind):
    method_name, _label = LINK_KINDS[kind]
    target_id = await _create_target(stack, kind)

    with pytest.raises(NotFoundError, match=f"Skill {UNKNOWN_ID} not found"):
        await getattr(stack.skills, method_name)(stack.user_id, UNKNOWN_ID, target_id)

    assert await _stored_skill_link_count(stack) == 0


@pytest.mark.parametrize("kind", list(LINK_KINDS))
async def test_linking_unknown_target_is_not_found_and_creates_no_link(stack, kind):
    method_name, label = LINK_KINDS[kind]
    skill_id = await _skill(stack, "linker")

    with pytest.raises(NotFoundError, match=f"{label} {UNKNOWN_ID} not found"):
        await getattr(stack.skills, method_name)(stack.user_id, skill_id, UNKNOWN_ID)

    links = await stack.skills.get_skill_links(stack.user_id, skill_id)
    assert (links.memory_ids, links.file_ids, links.code_artifact_ids, links.document_ids) == (
        [], [], [], [],
    )


async def test_embedding_dimension_check_accepts_stored_vectors(stack):
    await _skill(stack, "vectorised")

    assert await stack.skills.validate_embedding_dimensions() is True


async def test_embedding_dimension_check_rejects_stored_vectors_of_another_width(
    stack, monkeypatch, caplog,
):
    await _skill(stack, "vectorised")
    stored_width = settings.EMBEDDING_DIMENSIONS
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", stored_width * 2)
    logger_name = "app.repositories.sqlite.skill_repository"
    monkeypatch.setattr(logging.getLogger(logger_name), "disabled", False)
    caplog.set_level(logging.ERROR, logger=logger_name)

    assert await stack.skills.validate_embedding_dimensions() is False

    [record] = [r for r in caplog.records if r.name == logger_name]
    assert record.getMessage() == "Skill dimension mismatch"
    assert (record.expected, record.actual) == (stored_width * 2, stored_width)
