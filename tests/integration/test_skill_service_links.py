"""Integration tests for SkillService links to files, code artifacts and documents with in-memory stubs.
"""
from uuid import uuid4

import pytest

from app.exceptions import NotFoundError
from app.models.skill_models import SkillCreate


@pytest.mark.asyncio
async def test_link_skill_to_file(test_skill_service):
    """Test linking a skill to a file returns confirmation."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="link-file-test",
            description="Test skill for file linking",
            content="# Content",
            tags=[],
        ),
    )

    result = await test_skill_service.link_skill_to_file(
        user_id=user_id, skill_id=skill.id, file_id=99,
    )
    assert result["skill_id"] == skill.id
    assert result["file_id"] == 99
    assert result["linked"] is True


@pytest.mark.asyncio
async def test_unlink_skill_from_file(test_skill_service):
    """Test unlinking a skill from a file returns confirmation."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="unlink-file-test",
            description="Test skill for file unlinking",
            content="# Content",
            tags=[],
        ),
    )

    await test_skill_service.link_skill_to_file(
        user_id=user_id, skill_id=skill.id, file_id=99,
    )
    result = await test_skill_service.unlink_skill_from_file(
        user_id=user_id, skill_id=skill.id, file_id=99,
    )
    assert result["skill_id"] == skill.id
    assert result["file_id"] == 99
    assert result["unlinked"] is True


@pytest.mark.asyncio
async def test_link_skill_to_file_skill_not_found(test_skill_service):
    """Test linking to a non-existent skill raises NotFoundError."""
    user_id = uuid4()
    with pytest.raises(NotFoundError):
        await test_skill_service.link_skill_to_file(
            user_id=user_id, skill_id=9999, file_id=1,
        )


@pytest.mark.asyncio
async def test_link_skill_to_code_artifact(test_skill_service):
    """Test linking a skill to a code artifact returns confirmation."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="link-ca-test",
            description="Test skill for code artifact linking",
            content="# Content",
            tags=[],
        ),
    )

    result = await test_skill_service.link_skill_to_code_artifact(
        user_id=user_id, skill_id=skill.id, code_artifact_id=42,
    )
    assert result["skill_id"] == skill.id
    assert result["code_artifact_id"] == 42
    assert result["linked"] is True


@pytest.mark.asyncio
async def test_unlink_skill_from_code_artifact(test_skill_service):
    """Test unlinking a skill from a code artifact returns confirmation."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="unlink-ca-test",
            description="Test skill for code artifact unlinking",
            content="# Content",
            tags=[],
        ),
    )

    await test_skill_service.link_skill_to_code_artifact(
        user_id=user_id, skill_id=skill.id, code_artifact_id=42,
    )
    result = await test_skill_service.unlink_skill_from_code_artifact(
        user_id=user_id, skill_id=skill.id, code_artifact_id=42,
    )
    assert result["skill_id"] == skill.id
    assert result["code_artifact_id"] == 42
    assert result["unlinked"] is True


@pytest.mark.asyncio
async def test_link_skill_to_code_artifact_skill_not_found(test_skill_service):
    """Test linking code artifact to non-existent skill raises NotFoundError."""
    user_id = uuid4()
    with pytest.raises(NotFoundError):
        await test_skill_service.link_skill_to_code_artifact(
            user_id=user_id, skill_id=9999, code_artifact_id=1,
        )


@pytest.mark.asyncio
async def test_link_skill_to_document(test_skill_service):
    """Test linking a skill to a document returns confirmation."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="link-doc-test",
            description="Test skill for document linking",
            content="# Content",
            tags=[],
        ),
    )

    result = await test_skill_service.link_skill_to_document(
        user_id=user_id, skill_id=skill.id, document_id=77,
    )
    assert result["skill_id"] == skill.id
    assert result["document_id"] == 77
    assert result["linked"] is True


@pytest.mark.asyncio
async def test_unlink_skill_from_document(test_skill_service):
    """Test unlinking a skill from a document returns confirmation."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="unlink-doc-test",
            description="Test skill for document unlinking",
            content="# Content",
            tags=[],
        ),
    )

    await test_skill_service.link_skill_to_document(
        user_id=user_id, skill_id=skill.id, document_id=77,
    )
    result = await test_skill_service.unlink_skill_from_document(
        user_id=user_id, skill_id=skill.id, document_id=77,
    )
    assert result["skill_id"] == skill.id
    assert result["document_id"] == 77
    assert result["unlinked"] is True


@pytest.mark.asyncio
async def test_link_skill_to_document_skill_not_found(test_skill_service):
    """Test linking document to non-existent skill raises NotFoundError."""
    user_id = uuid4()
    with pytest.raises(NotFoundError):
        await test_skill_service.link_skill_to_document(
            user_id=user_id, skill_id=9999, document_id=1,
        )


@pytest.mark.asyncio
async def test_duplicate_link_is_idempotent(test_skill_service):
    """Bug #1: Linking the same resource twice should not raise an error."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="idempotent-link",
            description="Test idempotent linking",
            content="# Content",
            tags=[],
        ),
    )
    # Link twice — second call should succeed silently
    await test_skill_service.link_skill_to_file(
        user_id=user_id, skill_id=skill.id, file_id=1,
    )
    result = await test_skill_service.link_skill_to_file(
        user_id=user_id, skill_id=skill.id, file_id=1,
    )
    assert result["linked"] is True


@pytest.mark.asyncio
async def test_unlink_nonexistent_returns_false(test_skill_service):
    """Bug #2: Unlinking a non-existent link should return unlinked=False."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="unlink-false-test",
            description="Test unlink returns false",
            content="# Content",
            tags=[],
        ),
    )
    result = await test_skill_service.unlink_skill_from_file(
        user_id=user_id, skill_id=skill.id, file_id=999,
    )
    assert result["unlinked"] is False


@pytest.mark.asyncio
async def test_get_skill_links_basic(test_skill_service):
    """Linked memories, files, code artifacts and documents all appear in the lookup."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="links-basic-test",
            description="Test skill for reverse link lookup",
            content="# Content",
            tags=[],
        ),
    )

    await test_skill_service.link_skill_to_memory(
        user_id=user_id, skill_id=skill.id, memory_id=11,
    )
    await test_skill_service.link_skill_to_file(
        user_id=user_id, skill_id=skill.id, file_id=22,
    )
    await test_skill_service.link_skill_to_code_artifact(
        user_id=user_id, skill_id=skill.id, code_artifact_id=33,
    )
    await test_skill_service.link_skill_to_document(
        user_id=user_id, skill_id=skill.id, document_id=44,
    )

    links = await test_skill_service.get_skill_links(user_id=user_id, skill_id=skill.id)

    assert links.memory_ids == [11]
    assert links.file_ids == [22]
    assert links.code_artifact_ids == [33]
    assert links.document_ids == [44]


@pytest.mark.asyncio
async def test_get_skill_links_empty(test_skill_service):
    """A skill with no links returns four empty lists."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="links-empty-test",
            description="Test skill with no links",
            content="# Content",
            tags=[],
        ),
    )

    links = await test_skill_service.get_skill_links(user_id=user_id, skill_id=skill.id)

    assert links.memory_ids == []
    assert links.file_ids == []
    assert links.code_artifact_ids == []
    assert links.document_ids == []


@pytest.mark.asyncio
async def test_get_skill_links_not_found(test_skill_service):
    """Looking up links for a non-existent skill raises NotFoundError."""
    user_id = uuid4()
    with pytest.raises(NotFoundError):
        await test_skill_service.get_skill_links(user_id=user_id, skill_id=9999)


@pytest.mark.asyncio
async def test_get_skill_links_after_unlink(test_skill_service):
    """Unlinked content no longer appears in the lookup."""
    user_id = uuid4()
    skill = await test_skill_service.create_skill(
        user_id,
        SkillCreate(
            name="links-unlink-test",
            description="Test skill for unlink reverse lookup",
            content="# Content",
            tags=[],
        ),
    )

    await test_skill_service.link_skill_to_document(
        user_id=user_id, skill_id=skill.id, document_id=44,
    )
    await test_skill_service.link_skill_to_document(
        user_id=user_id, skill_id=skill.id, document_id=55,
    )
    await test_skill_service.unlink_skill_from_document(
        user_id=user_id, skill_id=skill.id, document_id=44,
    )

    links = await test_skill_service.get_skill_links(user_id=user_id, skill_id=skill.id)

    assert links.document_ids == [55]
    assert links.memory_ids == []


@pytest.mark.asyncio
async def test_get_skill_links_user_isolation(test_skill_service):
    """A user cannot look up links for another user's skill."""
    user_id_1 = uuid4()
    user_id_2 = uuid4()
    skill = await test_skill_service.create_skill(
        user_id_1,
        SkillCreate(
            name="links-isolation-test",
            description="Test skill for user isolation",
            content="# Content",
            tags=[],
        ),
    )
    await test_skill_service.link_skill_to_memory(
        user_id=user_id_1, skill_id=skill.id, memory_id=11,
    )

    links = await test_skill_service.get_skill_links(user_id=user_id_1, skill_id=skill.id)
    assert links.memory_ids == [11]

    with pytest.raises(NotFoundError):
        await test_skill_service.get_skill_links(user_id=user_id_2, skill_id=skill.id)
