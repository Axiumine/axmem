"""PostgreSQL skill repository: list filters, metadata updates, ownership and search scope.

Calls the repository directly (the services check ids first), so other users' ids and the
optional filters are exercised against real rows and a real vector index.
"""
import logging

import pytest
import pytest_asyncio

from app.config.settings import settings
from app.exceptions import NotFoundError
from app.models.skill_models import SkillUpdate
from tests.e2e.pg_repository_world_e2e_helpers import (
    RepositoryWorld,
    build_world,
    captured_logger,
    new_project,
    new_skill,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

SKILL_LOGGER = "app.repositories.postgres.skill_repository"


@pytest_asyncio.fixture(loop_scope="session")
async def world(db_adapter) -> RepositoryWorld:
    return await build_world(db_adapter)


async def test_list_skills_filters_by_project_tag_and_importance(world):
    repo, owner = world.skills, world.owner
    first_project = await new_project(world, owner, "first")
    second_project = await new_project(world, owner, "second")
    await new_skill(world, owner, "in-first-project", project_id=first_project, tags=["pdf"], importance=3)
    await new_skill(world, owner, "in-second-project", project_id=second_project, tags=["docs"], importance=9)
    await new_skill(world, owner, "without-project", tags=["pdf", "docs"], importance=6)

    async def names(**filters):
        return sorted(s.name for s in await repo.list_skills(owner, **filters))

    assert await names() == ["in-first-project", "in-second-project", "without-project"]
    assert await names(project_id=first_project) == ["in-first-project"]
    assert await names(tags=["docs"]) == ["in-second-project", "without-project"]
    assert await names(importance_threshold=6) == ["in-second-project", "without-project"]
    assert await names(tags=["pdf"], importance_threshold=5) == ["without-project"]


async def test_update_replaces_metadata_and_keeps_other_fields(world):
    repo, owner = world.skills, world.owner
    skill = await new_skill(world, owner, "pdf-processing", metadata={"author": "first"})

    updated = await repo.update_skill(owner, skill.id, SkillUpdate(metadata={"author": "second"}))

    assert updated.metadata == {"author": "second"}
    stored = await repo.get_skill_by_id(owner, skill.id)
    assert stored is not None
    assert stored.metadata == {"author": "second"}
    assert (stored.name, stored.description) == (skill.name, skill.description)


async def test_update_of_another_users_skill_is_not_found_and_changes_nothing(world):
    repo = world.skills
    strangers = await new_skill(world, world.stranger, "pdf-processing", description="original")

    with pytest.raises(NotFoundError) as refused:
        await repo.update_skill(world.owner, strangers.id, SkillUpdate(description="hijacked"))

    assert str(refused.value) == f"Skill {strangers.id} not found"
    stored = await repo.get_skill_by_id(world.stranger, strangers.id)
    assert stored is not None
    assert stored.description == "original"


async def test_delete_of_another_users_skill_returns_false_and_keeps_it(world):
    repo = world.skills
    strangers = await new_skill(world, world.stranger, "pdf-processing")

    assert await repo.delete_skill(world.owner, strangers.id) is False

    assert await repo.get_skill_by_id(world.stranger, strangers.id) is not None


async def test_search_with_project_returns_only_skills_of_that_project(world):
    repo, owner = world.skills, world.owner
    wanted_project = await new_project(world, owner, "wanted")
    other_project = await new_project(world, owner, "other")
    wanted = await new_skill(world, owner, "wanted-skill", project_id=wanted_project)
    await new_skill(world, owner, "other-skill", project_id=other_project)
    await new_skill(world, owner, "loose-skill")

    unscoped = await repo.search_skills(owner, "skill", k=10)
    scoped = await repo.search_skills(owner, "skill", k=10, project_id=wanted_project)

    assert sorted(s.name for s in unscoped) == ["loose-skill", "other-skill", "wanted-skill"]
    assert [s.id for s in scoped] == [wanted.id]


async def test_dimension_check_reports_vectors_that_differ_from_the_configured_width(
    world, monkeypatch, caplog,
):
    await new_skill(world, world.owner, "pdf-processing")
    configured = settings.EMBEDDING_DIMENSIONS
    assert await world.skills.validate_embedding_dimensions() is True

    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", configured + 1)
    with captured_logger(caplog, SKILL_LOGGER, logging.ERROR) as records:
        assert await world.skills.validate_embedding_dimensions() is False

    assert [r.getMessage() for r in records] == ["Skill dimension mismatch"]
    assert (records[0].expected, records[0].actual) == (configured + 1, configured)
