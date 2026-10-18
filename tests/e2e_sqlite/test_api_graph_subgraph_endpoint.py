"""E2E tests for the recursive-CTE subgraph endpoint of the Graph REST API.

Uses in-memory SQLite for test isolation.
Tests GET /api/v1/graph/subgraph: centering, depth, cycles, filtering,
truncation, edge types, validation and meta fields.
"""
import pytest
from graph_sqlite_helpers import (
    assert_ok,
    auto_link_disabled,
    create_entity,
    create_memory,
    create_memory_series,
    find_node,
    link_entity_memory,
    link_memories,
    node_ids,
    nodes_of_type,
    relate_entities,
)


def assert_subgraph_meta_counts(meta):
    """Assert the subgraph meta reports its node and edge count fields."""
    assert "memory_count" in meta
    assert "entity_count" in meta
    assert "edge_count" in meta
    assert "memory_link_count" in meta
    assert "entity_relationship_count" in meta
    assert "entity_memory_count" in meta
    assert "truncated" in meta


class TestSubgraphEndpoint:
    """Test GET /api/v1/graph/subgraph endpoint with recursive CTE traversal."""

    @pytest.mark.asyncio
    async def test_subgraph_from_memory_center(self, http_client):
        """Subgraph traversal starting from a memory node."""
        memory_id = await create_memory(
            http_client, "CTE Center Memory", "Memory at the center of CTE subgraph",
            "Testing CTE subgraph", ["cte_center"], ["cte-test"],
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}",
        ))

        assert "nodes" in data
        assert "edges" in data
        assert "meta" in data
        assert data["meta"]["center_node_id"] == f"memory_{memory_id}"

        # Should include the center memory with depth 0
        assert f"memory_{memory_id}" in node_ids(data)
        assert find_node(data, f"memory_{memory_id}")["depth"] == 0

    @pytest.mark.asyncio
    async def test_subgraph_from_entity_center(self, http_client):
        """Subgraph traversal starting from an entity node."""
        entity_id = await create_entity(
            http_client, "CTE Center Entity", "Organization",
            notes="Entity at center of CTE subgraph",
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=entity_{entity_id}",
        ))

        assert data["meta"]["center_node_id"] == f"entity_{entity_id}"

        # Should include the center entity with depth 0
        assert f"entity_{entity_id}" in node_ids(data)
        assert find_node(data, f"entity_{entity_id}")["depth"] == 0

    @pytest.mark.asyncio
    async def test_subgraph_depth_limits(self, http_client):
        """Depth parameter controls traversal depth."""
        # Disable auto-linking for this test to control exact graph structure
        with auto_link_disabled():
            # Create chain of linked memories: M1 -> M2 -> M3
            mem1_id = await create_memory(
                http_client, "Antarctic Penguin Migration",
                "Emperor penguins travel 70km across ice to breeding grounds",
                "Wildlife documentary research",
                ["penguin", "antarctica", "migration"], ["depth-test"],
            )
            mem2_id = await create_memory(
                http_client, "Quantum Entanglement Properties",
                "Particles maintain correlated states regardless of distance",
                "Physics lecture notes",
                ["quantum", "physics", "entanglement"], ["depth-test"],
            )
            mem3_id = await create_memory(
                http_client, "Renaissance Art Techniques",
                "Sfumato technique developed by Leonardo da Vinci for soft edges",
                "Art history seminar",
                ["art", "renaissance", "painting"], ["depth-test"],
            )
            await link_memories(http_client, mem1_id, mem2_id)
            await link_memories(http_client, mem2_id, mem3_id)

            # Depth 1: Should get M1 and M2 only
            data = assert_ok(await http_client.get(
                f"/api/v1/graph/subgraph?node_id=memory_{mem1_id}&depth=1",
            ))
            assert data["meta"]["depth"] == 1

            ids = node_ids(data)
            assert f"memory_{mem1_id}" in ids
            assert f"memory_{mem2_id}" in ids
            # M3 should NOT be present at depth 1
            assert f"memory_{mem3_id}" not in ids

            # Depth 2: Should get all three
            data = assert_ok(await http_client.get(
                f"/api/v1/graph/subgraph?node_id=memory_{mem1_id}&depth=2",
            ))
            assert data["meta"]["depth"] == 2

            ids = node_ids(data)
            assert f"memory_{mem1_id}" in ids
            assert f"memory_{mem2_id}" in ids
            assert f"memory_{mem3_id}" in ids

    @pytest.mark.asyncio
    async def test_subgraph_cycle_detection(self, http_client):
        """Cycles in graph don't cause infinite loops."""
        # Create memories that form a cycle: A -> B -> C -> A
        mem_a_id, mem_b_id, mem_c_id = [
            await create_memory(
                http_client, f"Cycle Node {letter}", f"Node {letter} in cycle",
                "Testing cycle detection", [f"cycle_{letter.lower()}"], ["cycle"],
            )
            for letter in "ABC"
        ]
        await link_memories(http_client, mem_a_id, mem_b_id)
        await link_memories(http_client, mem_b_id, mem_c_id)
        await link_memories(http_client, mem_c_id, mem_a_id)

        # Should complete without infinite loop, even with depth=3
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{mem_a_id}&depth=3",
        ))

        # Should have all three nodes, each appearing once
        ids = node_ids(data)
        assert f"memory_{mem_a_id}" in ids
        assert f"memory_{mem_b_id}" in ids
        assert f"memory_{mem_c_id}" in ids
        assert len(data["nodes"]) == 3  # No duplicates

    @pytest.mark.asyncio
    async def test_subgraph_node_types_filter(self, http_client):
        """node_types parameter filters which node types to include."""
        # Create memory and entity linked together
        memory_id = await create_memory(
            http_client, "Filter Test Memory", "Memory for filter test",
            "Testing node_types filter", ["filter"], ["filter-test"],
        )
        entity_id = await create_entity(http_client, "Filter Test Entity", "Organization")
        await link_entity_memory(http_client, entity_id, memory_id)

        # With node_types=memory only - should not traverse to entity
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}&node_types=memory",
        ))
        assert len(nodes_of_type(data, "entity")) == 0

        # With node_types=memory,entity - should traverse to entity
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}&node_types=memory,entity",
        ))
        assert len(nodes_of_type(data, "entity")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_max_nodes_truncation(self, http_client):
        """max_nodes limit causes truncation with truncated flag set."""
        # Create multiple linked memories
        mem_ids = await create_memory_series(
            http_client, 5, "Truncation Test Memory {i}",
            "Memory {i} for truncation test", "Testing max_nodes",
            "truncation_{i}", "truncation",
        )

        # Link all to first
        for related_id in mem_ids[1:]:
            await link_memories(http_client, mem_ids[0], related_id)

        # Request with very low max_nodes
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{mem_ids[0]}&max_nodes=2",
        ))

        # Should respect max_nodes limit
        assert len(data["nodes"]) <= 2
        # Should indicate truncation
        assert data["meta"]["truncated"] is True

    @pytest.mark.asyncio
    async def test_subgraph_depth_field_on_nodes(self, http_client):
        """Each node has correct depth value."""
        # Create linked memories: Center -> Neighbor
        center_id = await create_memory(
            http_client, "Depth Center", "Center memory for depth test",
            "Testing depth field", ["depth_center"], ["depth"],
        )
        neighbor_id = await create_memory(
            http_client, "Depth Neighbor", "Neighbor memory for depth test",
            "Testing depth field", ["depth_neighbor"], ["depth"],
        )
        await link_memories(http_client, center_id, neighbor_id)

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{center_id}",
        ))

        # Center should have depth 0
        assert find_node(data, f"memory_{center_id}")["depth"] == 0

        # Neighbor should have depth 1
        assert find_node(data, f"memory_{neighbor_id}")["depth"] == 1

    @pytest.mark.asyncio
    async def test_subgraph_all_edge_types(self, http_client):
        """Response includes all edge types: memory_link, entity_memory, entity_relationship."""
        mem1_id = await create_memory(
            http_client, "Edge Test Memory 1", "Memory 1 for edge test",
            "Testing edge types", ["edge1"], ["edges"],
        )
        mem2_id = await create_memory(
            http_client, "Edge Test Memory 2", "Memory 2 for edge test",
            "Testing edge types", ["edge2"], ["edges"],
        )
        ent1_id = await create_entity(http_client, "Edge Entity 1", "Individual")
        ent2_id = await create_entity(http_client, "Edge Entity 2", "Individual")

        # Create all edge types:
        # memory_link: mem1 <-> mem2
        await link_memories(http_client, mem1_id, mem2_id)
        # entity_memory: ent1 -> mem1
        await link_entity_memory(http_client, ent1_id, mem1_id)
        # entity_relationship: ent1 -> ent2
        await relate_entities(http_client, ent1_id, ent2_id, "knows")
        # Link ent2 to mem2 to ensure both entities are in subgraph
        await link_entity_memory(http_client, ent2_id, mem2_id)

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{mem1_id}&depth=2",
        ))

        # Check edge types are present
        edge_types = {e["type"] for e in data["edges"]}
        assert "memory_link" in edge_types
        assert "entity_memory" in edge_types
        assert "entity_relationship" in edge_types

        # Check meta counts
        assert data["meta"]["memory_link_count"] >= 1
        assert data["meta"]["entity_memory_count"] >= 1
        assert data["meta"]["entity_relationship_count"] >= 1

    @pytest.mark.asyncio
    async def test_subgraph_invalid_node_id_format(self, http_client):
        """Returns 400 for invalid node_id format."""
        response = await http_client.get("/api/v1/graph/subgraph?node_id=invalid_format")
        assert response.status_code == 400
        assert "Invalid node_id format" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_subgraph_missing_node_id(self, http_client):
        """Returns 400 when node_id is not provided."""
        response = await http_client.get("/api/v1/graph/subgraph")
        assert response.status_code == 400
        assert "Missing required parameter" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_subgraph_nonexistent_memory(self, http_client):
        """Returns 404 for nonexistent memory."""
        response = await http_client.get("/api/v1/graph/subgraph?node_id=memory_99999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_subgraph_nonexistent_entity(self, http_client):
        """Returns 404 for nonexistent entity."""
        response = await http_client.get("/api/v1/graph/subgraph?node_id=entity_99999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_subgraph_invalid_depth(self, http_client):
        """Returns 400 for invalid depth parameter."""
        response = await http_client.get(
            "/api/v1/graph/subgraph?node_id=memory_1&depth=not_a_number",
        )
        assert response.status_code == 400
        assert "Invalid depth" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_subgraph_depth_below_minimum(self, http_client):
        """Returns 400 for depth < 1."""
        response = await http_client.get("/api/v1/graph/subgraph?node_id=memory_1&depth=0")
        assert response.status_code == 400
        assert "Must be at least 1" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_subgraph_invalid_node_types(self, http_client):
        """Returns 400 for invalid node_types parameter."""
        response = await http_client.get(
            "/api/v1/graph/subgraph?node_id=memory_1&node_types=invalid",
        )
        assert response.status_code == 400
        assert "Invalid node_types" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_subgraph_meta_includes_all_fields(self, http_client):
        """Meta object includes all expected fields."""
        memory_id = await create_memory(
            http_client, "Meta Test Memory", "Memory for meta test",
            "Testing meta fields", ["meta"], ["meta-test"],
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}",
        ))

        meta = data["meta"]
        assert "center_node_id" in meta
        assert "depth" in meta
        assert "node_types" in meta
        assert "max_nodes" in meta
        assert_subgraph_meta_counts(meta)
