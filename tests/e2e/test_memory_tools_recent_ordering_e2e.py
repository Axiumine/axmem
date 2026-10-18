"""End-to-end tests for MCP memory tools: get_recent_memories ordering (PostgreSQL backend)

Requires:
- PostgreSQL with pgvector running in Docker
- FastEmbed embeddings adapter

Covers importance and creation-time filters applied before pagination,
deterministic tie-breaking and obsolete/sort options.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app.repositories.postgres.postgres_tables import MemoryTable
from tests.e2e.memory_tools_helpers_e2e import (
    create_memory,
    get_memory,
    mark_obsolete,
    recent_envelope,
    recent_memories,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_get_recent_memories_filters_before_pagination_e2e(mcp_client):
    """PostgreSQL applies both filters before counting and paging."""
    tag = f"e2e-recent-filter-{uuid.uuid4().hex[:8]}"
    created = []
    for importance in (9, 8, 3):
        result = await create_memory(
            mcp_client,
            title=f"Postgres recent filter {importance}",
            content="Memory for recent filtering",
            context="Testing PostgreSQL recent filters",
            keywords=["recent-filter"],
            tags=[tag],
            importance=importance,
        )
        created.append(result.data["id"])

    floor_memory = await get_memory(mcp_client, created[1])
    created_since = datetime.fromisoformat(floor_memory.data["created_at"]).isoformat()

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


async def _pin_created_at(db_adapter, memory_ids, timestamps):
    """Overwrite created_at for each memory so tie-breaking is deterministic."""
    async with db_adapter.system_session() as session:
        for memory_id, created_at in zip(memory_ids, timestamps, strict=True):
            await session.execute(
                update(MemoryTable)
                .where(MemoryTable.id == memory_id)
                .values(created_at=created_at),
            )


@pytest.mark.e2e
async def test_get_recent_memories_importance_ties_use_creation_time_e2e(
    mcp_client, db_adapter,
):
    """PostgreSQL uses creation time and ID to break importance ties."""
    tag = f"e2e-recent-tie-{uuid.uuid4().hex[:8]}"
    created = []
    for index in range(3):
        result = await create_memory(
            mcp_client,
            title=f"Postgres importance tie {index}",
            content="Memory for importance ordering",
            context="Testing deterministic recent memory ordering",
            keywords=["importance-tie"],
            tags=[tag],
            importance=8,
        )
        created.append(result.data["id"])

    await _pin_created_at(
        db_adapter,
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


@pytest.mark.e2e
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
