"""End-to-end tests for MCP memory tools: get_recent_memories ordering (SQLite backend)

Covers importance and creation-time filters applied before pagination,
deterministic tie-breaking, rejection of invalid filters and obsolete/sort options.
"""
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import update

from app.repositories.sqlite.sqlite_tables import MemoryTable
from tests.e2e_sqlite.memory_tools_helpers_sqlite import (
    create_memory,
    get_memory,
    mark_obsolete,
    recent_envelope,
    recent_memories,
)


async def _create_ranked_memories(client, tag, importances, title, content, keyword):
    """Create one memory per importance value and return their IDs in order."""
    created = []
    for importance in importances:
        result = await create_memory(
            client,
            title=title.format(importance=importance),
            content=content,
            context="Testing recent memory filtering",
            keywords=[keyword],
            tags=[tag],
            importance=importance,
        )
        created.append(result.data["id"])
    return created


@pytest.mark.asyncio
async def test_get_recent_memories_importance_min_filters_before_pagination(mcp_client):
    """The importance floor controls both the page and its total count."""
    tag = f"e2e-recent-importance-{uuid.uuid4().hex[:8]}"
    created = await _create_ranked_memories(
        mcp_client, tag, (9, 3, 8), "Importance floor {importance}",
        "Memory for importance filtering", "importance",
    )

    result = await recent_memories(
        mcp_client, tags=[tag], importance_min=8, sort_by="importance", limit=1,
    )

    assert result.data["total_count"] == 2
    assert [memory["id"] for memory in result.data["memories"]] == [created[0]]


@pytest.mark.asyncio
async def test_get_recent_memories_created_since_is_inclusive(mcp_client):
    """An exact timestamp match stays in; older memories do not."""
    tag = f"e2e-recent-since-{uuid.uuid4().hex[:8]}"
    created = await _create_ranked_memories(
        mcp_client, tag, (9, 8), "Time floor {importance}",
        "Memory for time filtering", "time-floor",
    )

    floor_memory = await get_memory(mcp_client, created[1])
    timestamp = datetime.fromisoformat(floor_memory.data["created_at"])
    created_since = timestamp.replace(tzinfo=UTC).astimezone(
        timezone(timedelta(hours=2)),
    ).isoformat()

    result = await recent_memories(
        mcp_client,
        tags=[tag],
        importance_min=8,
        created_since=created_since,
        sort_by="importance",
        limit=1,
    )

    assert result.data["total_count"] == 1
    assert [memory["id"] for memory in result.data["memories"]] == [created[1]]


async def _pin_created_at(sqlite_app, memory_ids, timestamps):
    """Overwrite created_at for each memory so tie-breaking is deterministic."""
    db_adapter = sqlite_app.memory_service.memory_repo.db_adapter
    async with db_adapter.system_session() as session:
        for memory_id, created_at in zip(memory_ids, timestamps, strict=True):
            await session.execute(
                update(MemoryTable)
                .where(MemoryTable.id == memory_id)
                .values(created_at=created_at),
            )


@pytest.mark.asyncio
async def test_get_recent_memories_importance_ties_use_creation_time(mcp_client, sqlite_app):
    """Importance ties use newest creation time, then ID for exact ties."""
    tag = f"e2e-recent-tie-{uuid.uuid4().hex[:8]}"
    created = []
    for index in range(3):
        result = await create_memory(
            mcp_client,
            title=f"Importance tie {index}",
            content="Memory for importance ordering",
            context="Testing deterministic recent memory ordering",
            keywords=["importance-tie"],
            tags=[tag],
            importance=8,
        )
        created.append(result.data["id"])

    await _pin_created_at(
        sqlite_app,
        created,
        (
            datetime(2026, 2, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 2, 1, tzinfo=UTC),
        ),
    )

    result = await recent_memories(
        mcp_client, tags=[tag], sort_by="importance", limit=10,
    )

    assert [memory["id"] for memory in result.data["memories"]] == [
        created[2], created[0], created[1],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments, message", [
    ({"importance_min": 0}, "importance_min must be"),
    ({"importance_min": 11}, "importance_min must be"),
    ({"importance_min": True}, "importance_min must be"),
    ({"created_since": "2026-08-01T00:00:00"}, "created_since must be"),
    ({"created_since": "not-a-timestamp"}, "created_since must be"),
])
async def test_get_recent_memories_rejects_invalid_filters(mcp_client, arguments, message):
    """Invalid filters fail clearly instead of returning an unfiltered brief."""
    with pytest.raises(ToolError, match=message):
        await recent_memories(mcp_client, **arguments)


@pytest.mark.asyncio
async def test_get_recent_memories_include_obsolete_and_sort_importance_e2e(mcp_client):
    """Test include_obsolete and importance sort on get_recent_memories"""
    low = await create_memory(
        mcp_client,
        title="E2E recent sort low",
        content="Lower importance memory",
        context="Sort/importance test",
        keywords=["e2e", "sort"],
        tags=["e2e-recent-sort"],
        importance=3,
    )
    high = await create_memory(
        mcp_client,
        title="E2E recent sort high",
        content="Higher importance memory",
        context="Sort/importance test",
        keywords=["e2e", "sort"],
        tags=["e2e-recent-sort"],
        importance=9,
    )
    obsolete = await create_memory(
        mcp_client,
        title="E2E recent sort obsolete",
        content="Will be marked obsolete",
        context="Sort/importance test",
        keywords=["e2e", "sort"],
        tags=["e2e-recent-sort"],
        importance=8,
    )

    low_id = low.data["id"]
    high_id = high.data["id"]
    obsolete_id = obsolete.data["id"]

    await mark_obsolete(mcp_client, obsolete_id, "E2E include_obsolete test")

    sorted_result = await recent_memories(
        mcp_client,
        tags=["e2e-recent-sort"],
        sort_by="importance",
        sort_order="desc",
        limit=10,
    )
    sorted_memories = recent_envelope(sorted_result)["memories"]
    sorted_ids = [m["id"] for m in sorted_memories if m["id"] in {low_id, high_id, obsolete_id}]
    assert sorted_ids.index(high_id) < sorted_ids.index(low_id)

    without_obsolete = await recent_memories(
        mcp_client, tags=["e2e-recent-sort"], limit=10,
    )
    without_ids = {m["id"] for m in recent_envelope(without_obsolete)["memories"]}
    assert obsolete_id not in without_ids

    with_obsolete = await recent_memories(
        mcp_client, tags=["e2e-recent-sort"], include_obsolete=True, limit=10,
    )
    with_ids = {m["id"] for m in recent_envelope(with_obsolete)["memories"]}
    assert obsolete_id in with_ids
