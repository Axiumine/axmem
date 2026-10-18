"""End-to-end tests for Graph REST API pagination with the PostgreSQL backend.

Covers offset paging, pagination metadata, the MAX_GRAPH_LIMIT clamp, sorting
and invalid pagination parameters of GET /api/v1/graph.

Requires:
- PostgreSQL with pgvector running in Docker
- In-process FastMCP server (provided by conftest fixtures)
"""
import pytest

from app.config.settings import settings
from tests.e2e.graph_pg_e2e_helpers import (
    assert_keys_present,
    create_memory,
    nodes_of_type,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_numbered_memories(
    http_client, count, *, title, content, context, keyword, tag,
):
    """Create ``count`` memories; ``title``, ``content`` and ``keyword`` take ``{i}``."""
    for i in range(count):
        await create_memory(
            http_client,
            title.format(i=i),
            content.format(i=i),
            context,
            [keyword.format(i=i)],
            tags=[tag],
        )


async def _assert_invalid_graph_query(http_client, query, error_fragment):
    """GET /api/v1/graph with a bad query and assert a 400 mentioning the fragment."""
    response = await http_client.get(f"/api/v1/graph?{query}")
    assert response.status_code == 400
    assert error_fragment in response.json()["error"]


@pytest.mark.e2e
async def test_graph_pagination_with_offset(http_client):
    """GET /api/v1/graph supports pagination with offset parameter."""
    await _create_numbered_memories(
        http_client,
        5,
        title="Pagination E2E Memory {i}",
        content="Memory {i} for pagination E2E test",
        context="Testing pagination offset",
        keyword="pagination_e2e_{i}",
        tag="pagination-e2e",
    )

    # Get first 2 memories
    response1 = await http_client.get("/api/v1/graph?limit=2&offset=0")
    assert response1.status_code == 200
    first_page_ids = {n["id"] for n in nodes_of_type(response1.json(), "memory")}

    # Get next 2 memories
    response2 = await http_client.get("/api/v1/graph?limit=2&offset=2")
    assert response2.status_code == 200
    second_page_ids = {n["id"] for n in nodes_of_type(response2.json(), "memory")}

    # Pages should not overlap
    assert first_page_ids.isdisjoint(second_page_ids)


@pytest.mark.e2e
async def test_graph_pagination_metadata(http_client):
    """GET /api/v1/graph includes pagination metadata in response."""
    await _create_numbered_memories(
        http_client,
        5,
        title="Pagination Meta E2E {i}",
        content="Memory {i} for pagination meta E2E test",
        context="Testing pagination metadata",
        keyword="meta_e2e_{i}",
        tag="meta-e2e",
    )

    response = await http_client.get("/api/v1/graph?limit=2&offset=0")
    assert response.status_code == 200
    meta = response.json()["meta"]

    assert_keys_present(
        meta, ("total_memory_count", "offset", "limit", "has_more"), "pagination meta",
    )
    assert meta["offset"] == 0
    assert meta["limit"] == 2
    assert meta["total_memory_count"] >= 5
    assert meta["has_more"] is True  # We have more than 2 memories


@pytest.mark.e2e
async def test_graph_respects_max_graph_limit_setting(http_client, monkeypatch):
    """GET /api/v1/graph clamps the requested limit to settings.MAX_GRAPH_LIMIT."""
    monkeypatch.setattr(settings, "MAX_GRAPH_LIMIT", 50)

    response = await http_client.get("/api/v1/graph?limit=99999")
    assert response.status_code == 200
    meta = response.json()["meta"]
    assert meta["limit"] == 50


@pytest.mark.e2e
async def test_graph_sort_by_importance(http_client):
    """GET /api/v1/graph sorts by importance when specified."""
    await create_memory(
        http_client,
        "Low Importance E2E",
        "Memory with low importance for E2E test",
        "Testing sort by importance",
        ["low_importance_e2e"],
        tags=["sort-e2e"],
        importance=3,
    )
    await create_memory(
        http_client,
        "High Importance E2E",
        "Memory with high importance for E2E test",
        "Testing sort by importance",
        ["high_importance_e2e"],
        tags=["sort-e2e"],
        importance=10,
    )

    # Sort by importance descending
    response = await http_client.get("/api/v1/graph?sort_by=importance&sort_order=desc&limit=2")
    assert response.status_code == 200
    memory_nodes = nodes_of_type(response.json(), "memory")

    if len(memory_nodes) >= 2:
        # First memory should have higher or equal importance
        assert memory_nodes[0]["data"]["importance"] >= memory_nodes[1]["data"]["importance"]


@pytest.mark.e2e
async def test_graph_invalid_pagination_params(http_client):
    """GET /api/v1/graph returns 400 for invalid pagination parameters."""
    # Invalid offset
    await _assert_invalid_graph_query(http_client, "offset=not_a_number", "Invalid offset")
    # Negative offset
    await _assert_invalid_graph_query(http_client, "offset=-1", "non-negative")
    # Invalid sort_by
    await _assert_invalid_graph_query(
        http_client, "sort_by=invalid_field", "sort_by must be one of",
    )
    # Invalid sort_order
    await _assert_invalid_graph_query(
        http_client, "sort_order=invalid", "sort_order must be one of",
    )


@pytest.mark.e2e
async def test_graph_offset_beyond_total(http_client):
    """GET /api/v1/graph returns empty results when offset exceeds total."""
    response = await http_client.get("/api/v1/graph?offset=99999")
    assert response.status_code == 200
    data = response.json()

    # Should have no memory nodes since offset is beyond total
    assert len(nodes_of_type(data, "memory")) == 0
    # Metadata should still be present
    assert data["meta"]["has_more"] is False
