"""PostgreSQL skill repository: linking and unlinking memories, files, code artifacts, documents.

The services look ids up before they call the repository, so refusing unknown or foreign ids and
removing links are exercised here by calling the repository directly against real rows.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
import pytest_asyncio

from app.exceptions import NotFoundError
from tests.e2e.pg_repository_world_e2e_helpers import (
    UNKNOWN_ID,
    RepositoryWorld,
    build_world,
    new_code_artifact,
    new_document,
    new_file,
    new_memory,
    new_skill,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@dataclass(frozen=True)
class LinkKind:
    """How one kind of linkable row is created, linked, unlinked and reported."""

    label: str
    id_key: str
    links_field: str
    create: Callable[[RepositoryWorld, object], Awaitable[int]]
    link: str
    unlink: str


async def _new_memory_id(world: RepositoryWorld, user_id) -> int:
    return (await new_memory(world, user_id)).id


LINK_KINDS = {
    "memory": LinkKind(
        "Memory", "memory_id", "memory_ids", _new_memory_id,
        "link_skill_to_memory", "unlink_skill_from_memory",
    ),
    "file": LinkKind(
        "File", "file_id", "file_ids", new_file,
        "link_skill_to_file", "unlink_skill_from_file",
    ),
    "code_artifact": LinkKind(
        "Code artifact", "code_artifact_id", "code_artifact_ids", new_code_artifact,
        "link_skill_to_code_artifact", "unlink_skill_from_code_artifact",
    ),
    "document": LinkKind(
        "Document", "document_id", "document_ids", new_document,
        "link_skill_to_document", "unlink_skill_from_document",
    ),
}


@pytest_asyncio.fixture(loop_scope="session")
async def world(db_adapter) -> RepositoryWorld:
    return await build_world(db_adapter)


@pytest.fixture(params=sorted(LINK_KINDS))
def kind(request) -> LinkKind:
    return LINK_KINDS[request.param]


async def _linked_ids(world: RepositoryWorld, skill_id: int, kind: LinkKind) -> list[int]:
    links = await world.skills.get_skill_links(world.owner, skill_id)
    return getattr(links, kind.links_field)


async def test_linking_to_another_users_skill_is_not_found(world, kind):
    strangers_skill = await new_skill(world, world.stranger, "pdf-processing")
    target = await kind.create(world, world.owner)

    with pytest.raises(NotFoundError) as refused:
        await getattr(world.skills, kind.link)(world.owner, strangers_skill.id, target)

    assert str(refused.value) == f"Skill {strangers_skill.id} not found"


async def test_linking_another_users_row_is_not_found_and_links_nothing(world, kind):
    skill = await new_skill(world, world.owner, "pdf-processing")
    strangers_target = await kind.create(world, world.stranger)

    with pytest.raises(NotFoundError) as refused:
        await getattr(world.skills, kind.link)(world.owner, skill.id, strangers_target)

    assert str(refused.value) == f"{kind.label} {strangers_target} not found"
    assert await _linked_ids(world, skill.id, kind) == []


async def test_linking_an_unknown_row_is_not_found(world, kind):
    skill = await new_skill(world, world.owner, "pdf-processing")

    with pytest.raises(NotFoundError) as refused:
        await getattr(world.skills, kind.link)(world.owner, skill.id, UNKNOWN_ID)

    assert str(refused.value) == f"{kind.label} {UNKNOWN_ID} not found"


async def test_unlinking_removes_the_link_once(world, kind):
    skill = await new_skill(world, world.owner, "pdf-processing")
    target = await kind.create(world, world.owner)
    await getattr(world.skills, kind.link)(world.owner, skill.id, target)
    assert await _linked_ids(world, skill.id, kind) == [target]
    unlink = getattr(world.skills, kind.unlink)

    first = await unlink(world.owner, skill.id, target)
    second = await unlink(world.owner, skill.id, target)

    assert first == {"skill_id": skill.id, kind.id_key: target, "unlinked": True}
    assert second == {"skill_id": skill.id, kind.id_key: target, "unlinked": False}
    assert await _linked_ids(world, skill.id, kind) == []
