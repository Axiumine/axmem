"""E2E tests for Graph REST API endpoints.

Uses in-memory SQLite for test isolation.
Tests the /api/v1/graph endpoint: node listing, pagination, sorting and
query-parameter validation. The remaining graph tests live in the sibling
``test_api_graph_*.py`` modules, sharing ``graph_sqlite_helpers``.
"""
import pytest
from graph_sqlite_helpers import (
    assert_ok,
    create_entity,
    create_memory,
    create_memory_series,
    link_memories,
    nodes_of_type,
)


class TestGraphAPI:
    """Test GET /api/v1/graph endpoint."""

    @pytest.mark.asyncio
    async def test_get_graph_empty(self, http_client):
        """GET /api/v1/graph returns empty graph initially."""
        data = assert_ok(await http_client.get("/api/v1/graph"))
        assert data["nodes"] == []
        assert data["edges"] == []
        assert "meta" in data
        assert data["meta"]["memory_count"] == 0
        assert data["meta"]["entity_count"] == 0
        assert data["meta"]["edge_count"] == 0

    @pytest.mark.asyncio
    async def test_get_graph_with_memories(self, http_client):
        """GET /api/v1/graph returns memory nodes."""
        for number, ordinal in ((1, "First"), (2, "Second")):
            await create_memory(
                http_client,
                f"Graph Memory {number}",
                f"{ordinal} memory for graph test",
                "Testing graph API",
                ["graph", "test"],
                ["test"],
            )

        data = assert_ok(await http_client.get("/api/v1/graph"))
        assert len(data["nodes"]) >= 2
        assert data["meta"]["memory_count"] >= 2

        # Check node structure
        memory_nodes = nodes_of_type(data, "memory")
        assert len(memory_nodes) >= 2
        for node in memory_nodes:
            assert "id" in node
            assert "label" in node
            assert "data" in node
            assert node["id"].startswith("memory_")

    @pytest.mark.asyncio
    async def test_get_graph_with_entities(self, http_client):
        """GET /api/v1/graph returns entity nodes when include_entities=true."""
        await create_entity(
            http_client, "Graph Test Entity", "Organization",
            notes="Entity for graph test",
        )

        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=true"))

        entity_nodes = nodes_of_type(data, "entity")
        assert len(entity_nodes) >= 1
        for node in entity_nodes:
            assert node["id"].startswith("entity_")
            assert "name" in node["data"]

    @pytest.mark.asyncio
    async def test_get_graph_without_entities(self, http_client):
        """GET /api/v1/graph excludes entities when include_entities=false."""
        await create_entity(
            http_client, "Excluded Entity", "Individual", notes="Should be excluded",
        )
        await create_memory(
            http_client,
            "Graph Memory Only",
            "Memory without entities in graph",
            "Testing exclude entities",
            ["graph"],
            ["test"],
        )

        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=false"))

        assert len(nodes_of_type(data, "entity")) == 0

    @pytest.mark.asyncio
    async def test_get_graph_with_memory_links(self, http_client):
        """GET /api/v1/graph returns edges for linked memories."""
        mem1_id = await create_memory(
            http_client,
            "Linked Memory A",
            "First linked memory for graph edge test",
            "Testing graph edges",
            ["linkA"],
            ["edge-test"],
        )
        mem2_id = await create_memory(
            http_client,
            "Linked Memory B",
            "Second linked memory for graph edge test",
            "Testing graph edges",
            ["linkB"],
            ["edge-test"],
        )
        await link_memories(http_client, mem1_id, mem2_id)

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check for edge between the memories
        memory_link_edges = [e for e in data["edges"] if e["type"] == "memory_link"]
        # Should have at least one edge
        assert len(memory_link_edges) >= 1

    @pytest.mark.asyncio
    async def test_get_graph_with_limit(self, http_client):
        """GET /api/v1/graph respects limit parameter."""
        await create_memory_series(
            http_client, 5, "Limit Test Memory {i}", "Memory {i} for limit test",
            "Testing limit", "limit{i}", "limit-test",
        )

        data = assert_ok(await http_client.get("/api/v1/graph?limit=2"))

        assert len(nodes_of_type(data, "memory")) <= 2

    @pytest.mark.asyncio
    async def test_get_graph_invalid_limit(self, http_client):
        """GET /api/v1/graph returns 400 for invalid limit."""
        response = await http_client.get("/api/v1/graph?limit=not_a_number")
        assert response.status_code == 400
        assert "Invalid limit" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_get_graph_invalid_project_id(self, http_client):
        """GET /api/v1/graph returns 400 for invalid project_id."""
        response = await http_client.get("/api/v1/graph?project_id=not_a_number")
        assert response.status_code == 400
        assert "Invalid project_id" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_graph_pagination_with_offset(self, http_client):
        """GET /api/v1/graph respects offset parameter for pagination."""
        await create_memory_series(
            http_client, 5, "Pagination Memory {i}",
            "Memory {i} for pagination test", "Testing pagination offset",
            "pagination{i}", "pagination-test",
        )

        # Get first 2 memories, then the next 2
        data1 = assert_ok(await http_client.get("/api/v1/graph?limit=2&offset=0"))
        first_page_ids = {n["id"] for n in nodes_of_type(data1, "memory")}
        data2 = assert_ok(await http_client.get("/api/v1/graph?limit=2&offset=2"))
        second_page_ids = {n["id"] for n in nodes_of_type(data2, "memory")}

        # Pages should not overlap
        assert first_page_ids.isdisjoint(second_page_ids)

    @pytest.mark.asyncio
    async def test_graph_pagination_metadata(self, http_client):
        """GET /api/v1/graph includes pagination metadata in response."""
        await create_memory_series(
            http_client, 5, "Meta Test Memory {i}",
            "Memory {i} for pagination meta test", "Testing pagination metadata",
            "meta{i}", "meta-test",
        )

        data = assert_ok(await http_client.get("/api/v1/graph?limit=2&offset=0"))

        # Check pagination metadata
        meta = data["meta"]
        assert "total_memory_count" in meta
        assert "offset" in meta
        assert "limit" in meta
        assert "has_more" in meta

        assert meta["offset"] == 0
        assert meta["limit"] == 2
        assert meta["total_memory_count"] >= 5
        assert meta["has_more"] is True  # We have more than 2 memories

    @pytest.mark.asyncio
    async def test_graph_pagination_has_more_false(self, http_client):
        """has_more is False when all results are returned."""
        # Create exactly 2 memories
        await create_memory_series(
            http_client, 2, "Small Set Memory {i}", "Memory {i} for has_more test",
            "Testing has_more flag", "small{i}", "small-test",
        )

        # Get with limit higher than total
        data = assert_ok(await http_client.get("/api/v1/graph?limit=100&offset=0"))

        # has_more should be False since we got all memories
        meta = data["meta"]
        memory_count = meta["memory_count"]
        total_count = meta["total_memory_count"]

        # If we have all memories, has_more should be False
        if memory_count >= total_count:
            assert meta["has_more"] is False

    @pytest.mark.asyncio
    async def test_graph_sort_by_importance(self, http_client):
        """GET /api/v1/graph sorts by importance when specified."""
        # Create memories with different importance levels
        await create_memory(
            http_client, "Low Importance", "Memory with low importance",
            "Testing sort by importance", ["lowpriority"], ["sort-test"],
            importance=3,
        )
        await create_memory(
            http_client, "High Importance", "Memory with high importance",
            "Testing sort by importance", ["highpriority"], ["sort-test"],
            importance=10,
        )

        # Sort by importance descending
        url = "/api/v1/graph?sort_by=importance&sort_order=desc&limit=2"
        data = assert_ok(await http_client.get(url))

        memory_nodes = nodes_of_type(data, "memory")
        if len(memory_nodes) >= 2:
            # First memory should have higher or equal importance
            assert memory_nodes[0]["data"]["importance"] >= memory_nodes[1]["data"]["importance"]

    @pytest.mark.asyncio
    async def test_graph_sort_by_created_at(self, http_client):
        """GET /api/v1/graph sorts by created_at by default."""
        # Create memories in sequence
        for title, content, keyword in (
            ("First Created", "Created first", "first"),
            ("Second Created", "Created second", "second"),
        ):
            await create_memory(
                http_client, title, content, "Testing sort", [keyword], ["sort-test"],
            )

        # Sort by created_at descending (default) should put newest first
        url = "/api/v1/graph?sort_by=created_at&sort_order=desc&limit=10"
        data = assert_ok(await http_client.get(url))

        # Verify the request was successful
        assert "nodes" in data
        assert "meta" in data

    @pytest.mark.asyncio
    async def test_graph_sort_order_ascending(self, http_client):
        """GET /api/v1/graph supports ascending sort order."""
        data = assert_ok(await http_client.get("/api/v1/graph?sort_order=asc"))
        assert "nodes" in data

    @pytest.mark.asyncio
    async def test_graph_invalid_offset(self, http_client):
        """GET /api/v1/graph returns 400 for invalid offset."""
        response = await http_client.get("/api/v1/graph?offset=not_a_number")
        assert response.status_code == 400
        assert "Invalid offset" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_graph_negative_offset(self, http_client):
        """GET /api/v1/graph returns 400 for negative offset."""
        response = await http_client.get("/api/v1/graph?offset=-1")
        assert response.status_code == 400
        assert "non-negative" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_graph_invalid_sort_by(self, http_client):
        """GET /api/v1/graph returns 400 for invalid sort_by."""
        response = await http_client.get("/api/v1/graph?sort_by=invalid_field")
        assert response.status_code == 400
        assert "sort_by must be one of" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_graph_invalid_sort_order(self, http_client):
        """GET /api/v1/graph returns 400 for invalid sort_order."""
        response = await http_client.get("/api/v1/graph?sort_order=invalid")
        assert response.status_code == 400
        assert "sort_order must be one of" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_graph_offset_beyond_total(self, http_client):
        """GET /api/v1/graph returns empty results when offset exceeds total."""
        data = assert_ok(await http_client.get("/api/v1/graph?offset=99999"))

        # Should have no memory nodes since offset is beyond total
        assert len(nodes_of_type(data, "memory")) == 0

        # Metadata should still be present
        assert data["meta"]["has_more"] is False
