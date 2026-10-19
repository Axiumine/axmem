"""Integration tests for MemoryUpdate semantics ("Unchanged if null")

Covers the request model and MemoryService.update_memory with stubbed I/O. The
database level behaviour lives in tests/e2e_sqlite/test_memory_update_consistency_sqlite.py.
"""
from uuid import uuid4

import pytest

from app.models.memory_models import MemoryCreate, MemoryUpdate

NULLABLE_FIELDS = [
    "title",
    "content",
    "context",
    "keywords",
    "tags",
    "importance",
    "project_ids",
    "source_repo",
    "source_files",
    "source_url",
    "confidence",
    "encoding_agent",
    "encoding_version",
    "agent_id",
    "agent_version",
    "agent_model",
]


@pytest.mark.parametrize("field", NULLABLE_FIELDS)
def test_explicit_null_is_treated_as_unset(field):
    """A JSON null must behave exactly like an omitted field."""
    update = MemoryUpdate(**{field: None})

    assert update.model_fields_set == set()
    assert update.model_dump(exclude_unset=True) == {}


def test_null_keeps_other_fields_in_update():
    update = MemoryUpdate(title=None, content="new content", source_repo=None, importance=8)

    assert update.model_fields_set == {"content", "importance"}
    assert update.model_dump(exclude_unset=True) == {"content": "new content", "importance": 8}


def test_empty_values_are_not_treated_as_null():
    """Only None is dropped: empty lists still mean "clear this list"."""
    update = MemoryUpdate(project_ids=[], keywords=[])

    assert update.model_fields_set == {"project_ids", "keywords"}
    assert update.model_dump(exclude_unset=True) == {
        "project_ids": [],
        "keywords": [],
    }


@pytest.mark.asyncio
async def test_service_update_with_nulls_leaves_memory_unchanged(test_memory_service):
    user_id = uuid4()
    memory, _ = await test_memory_service.create_memory(
        user_id,
        MemoryCreate(
            title="Original title",
            content="Original content",
            context="Original context",
            keywords=["one"],
            tags=["two"],
            importance=7,
            source_repo="owner/repo",
        ),
    )

    unchanged = await test_memory_service.update_memory(
        user_id=user_id,
        memory_id=memory.id,
        updated_memory=MemoryUpdate(title=None, importance=None, keywords=None, source_repo=None),
    )

    assert unchanged.title == "Original title"
    assert unchanged.importance == 7
    assert unchanged.keywords == ["one"]
    assert unchanged.source_repo == "owner/repo"


@pytest.mark.asyncio
async def test_service_update_applies_value_next_to_nulls(test_memory_service):
    user_id = uuid4()
    memory, _ = await test_memory_service.create_memory(
        user_id,
        MemoryCreate(
            title="Original title",
            content="Original content",
            context="Original context",
            keywords=["one"],
            tags=["two"],
            importance=7,
            source_repo="owner/repo",
        ),
    )

    updated = await test_memory_service.update_memory(
        user_id=user_id,
        memory_id=memory.id,
        updated_memory=MemoryUpdate(content="Changed content", title=None, source_repo=None),
    )

    assert updated.content == "Changed content"
    assert updated.title == "Original title"
    assert updated.source_repo == "owner/repo"
