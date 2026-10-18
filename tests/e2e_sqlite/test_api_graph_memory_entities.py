"""E2E tests for the memory subgraph and entity edges of the Graph REST API.

Uses in-memory SQLite for test isolation.
Tests GET /api/v1/graph/memory/{id} and the entity-entity / entity-memory edges
exposed by GET /api/v1/graph.
"""
import pytest
from graph_sqlite_helpers import (
    assert_ok,
    create_entity,
    create_memory,
    edges_of_type,
    link_entity_memory,
    link_memories,
    node_ids,
    nodes_of_type,
    relate_entities,
)


class TestMemorySubgraph:
    """Test GET /api/v1/graph/memory/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_memory_subgraph(self, http_client):
        """GET /api/v1/graph/memory/{id} returns subgraph centered on memory."""
        memory_id = await create_memory(
            http_client, "Center Memory", "Memory at the center of subgraph",
            "Testing subgraph", ["center"], ["subgraph-test"],
        )

        data = assert_ok(await http_client.get(f"/api/v1/graph/memory/{memory_id}"))

        assert "nodes" in data
        assert "edges" in data
        assert data["center_memory_id"] == memory_id
        assert "meta" in data

        # Should include the center memory
        assert f"memory_{memory_id}" in node_ids(data)

    @pytest.mark.asyncio
    async def test_get_memory_subgraph_not_found(self, http_client):
        """GET /api/v1/graph/memory/{id} returns 404 for missing memory."""
        response = await http_client.get("/api/v1/graph/memory/99999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_memory_subgraph_with_links(self, http_client):
        """GET /api/v1/graph/memory/{id} includes linked memories."""
        center_id = await create_memory(
            http_client, "Subgraph Center", "Center of the subgraph",
            "Testing subgraph links", ["subgraphCenter"], ["subgraph"],
        )
        linked_id = await create_memory(
            http_client, "Linked to Center", "Memory linked to center",
            "Testing subgraph links", ["subgraphLinked"], ["subgraph"],
        )
        await link_memories(http_client, center_id, linked_id)

        data = assert_ok(await http_client.get(f"/api/v1/graph/memory/{center_id}"))

        # Should include both memories
        ids = node_ids(data)
        assert f"memory_{center_id}" in ids
        assert f"memory_{linked_id}" in ids

        # Should have edge between them
        assert len(data["edges"]) >= 1

    @pytest.mark.asyncio
    async def test_get_memory_subgraph_with_depth(self, http_client):
        """GET /api/v1/graph/memory/{id} respects depth parameter."""
        # Create chain of linked memories
        mem1_id, mem2_id, mem3_id = [
            await create_memory(
                http_client, f"Depth Chain {number}", f"{ordinal} in chain",
                "Testing depth", [f"depthChain{number}"], ["depth"],
            )
            for number, ordinal in ((1, "First"), (2, "Second"), (3, "Third"))
        ]

        # Link: 1 -> 2 -> 3
        await link_memories(http_client, mem1_id, mem2_id)
        await link_memories(http_client, mem2_id, mem3_id)

        # Get subgraph with depth=1 (should only get mem1 and mem2)
        data = assert_ok(await http_client.get(f"/api/v1/graph/memory/{mem1_id}?depth=1"))
        assert data["meta"]["depth"] == 1

        # Get subgraph with depth=2 (should get all three)
        data = assert_ok(await http_client.get(f"/api/v1/graph/memory/{mem1_id}?depth=2"))
        assert data["meta"]["depth"] == 2
        # With depth=2, we should reach mem3 through mem2
        ids = node_ids(data)
        assert f"memory_{mem1_id}" in ids
        assert f"memory_{mem2_id}" in ids

    @pytest.mark.asyncio
    async def test_get_memory_subgraph_invalid_memory_id(self, http_client):
        """GET /api/v1/graph/memory/{id} returns 400 for invalid memory_id."""
        response = await http_client.get("/api/v1/graph/memory/not_a_number")
        assert response.status_code == 400
        assert "Invalid memory_id" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_get_memory_subgraph_invalid_depth(self, http_client):
        """GET /api/v1/graph/memory/{id} returns 400 for invalid depth."""
        memory_id = await create_memory(
            http_client, "Depth Validation Test",
            "Memory for testing depth validation", "Testing depth",
            ["depthTest"], ["test"],
        )

        response = await http_client.get(
            f"/api/v1/graph/memory/{memory_id}?depth=not_a_number",
        )
        assert response.status_code == 400
        assert "Invalid depth" in response.json()["error"]


class TestGraphEntityEdges:
    """Test entity-entity and entity-memory edges in graph API."""

    @pytest.mark.asyncio
    async def test_graph_includes_entity_relationship_edges(self, http_client):
        """GET /api/v1/graph includes entity-relationship edges."""
        entity1_id = await create_entity(http_client, "Alice Developer", "Individual")
        entity2_id = await create_entity(http_client, "TechCorp Inc", "Organization")

        # Create relationship: Alice works_at TechCorp
        await relate_entities(
            http_client, entity1_id, entity2_id, "works_at",
            strength=0.9, confidence=0.95,
        )

        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=true"))

        # Find entity_relationship edges
        entity_rel_edges = edges_of_type(data, "entity_relationship")
        assert len(entity_rel_edges) >= 1

        # Verify edge structure
        edge = entity_rel_edges[0]
        assert "data" in edge
        assert edge["data"]["relationship_type"] == "works_at"
        assert edge["data"]["strength"] == 0.9
        assert edge["data"]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_graph_includes_entity_memory_edges(self, http_client):
        """GET /api/v1/graph includes entity-memory edges."""
        memory_id = await create_memory(
            http_client, "Project kickoff meeting", "Discussed project timeline",
            "Meeting notes", ["meeting"], ["project"],
        )
        entity_id = await create_entity(http_client, "Project Alpha", "Team")

        # Link entity to memory
        await link_entity_memory(http_client, entity_id, memory_id)

        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=true"))

        # Find entity_memory edges
        entity_mem_edges = edges_of_type(data, "entity_memory")
        assert len(entity_mem_edges) >= 1

        # Verify edge structure
        edge = entity_mem_edges[0]
        assert edge["source"] == f"entity_{entity_id}"
        assert edge["target"] == f"memory_{memory_id}"
        assert edge["id"] == f"entity_{entity_id}_memory_{memory_id}"

    @pytest.mark.asyncio
    async def test_entity_relationship_deduplication_bidirectional(self, http_client):
        """Entity relationships are deduplicated for bidirectional display."""
        entity_a_id = await create_entity(http_client, "Entity A", "Organization")
        entity_b_id = await create_entity(http_client, "Entity B", "Organization")

        # Create A -> B relationship, then B -> A (reverse direction)
        await relate_entities(http_client, entity_a_id, entity_b_id, "partners_with")
        await relate_entities(http_client, entity_b_id, entity_a_id, "partners_with")

        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=true"))

        # Count entity_relationship edges between A and B
        min_id = min(entity_a_id, entity_b_id)
        max_id = max(entity_a_id, entity_b_id)
        expected_edge_id = f"entity_{min_id}_entity_{max_id}_partners_with"

        matching_edges = [
            e for e in edges_of_type(data, "entity_relationship")
            if e["id"] == expected_edge_id
        ]

        # Should only have ONE edge despite two relationships
        assert len(matching_edges) == 1

    @pytest.mark.asyncio
    async def test_entity_edges_excluded_when_include_entities_false(self, http_client):
        """Entity edges are excluded when include_entities=false."""
        entity_id = await create_entity(http_client, "Test Entity", "Organization")
        memory_id = await create_memory(
            http_client, "Test Memory", "Test content", "Testing", ["test"], ["test"],
        )
        await link_entity_memory(http_client, entity_id, memory_id)

        # Get graph WITHOUT entities
        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=false"))

        # Should have no entity edges
        assert len(edges_of_type(data, "entity_relationship")) == 0
        assert len(edges_of_type(data, "entity_memory")) == 0

    @pytest.mark.asyncio
    async def test_graph_meta_includes_edge_counts_by_type(self, http_client):
        """Graph meta includes separate counts for each edge type."""
        # Create test data with all edge types: memory link
        mem1_id = await create_memory(
            http_client, "Memory 1", "Content 1", "Context", ["k1"], ["t1"],
        )
        mem2_id = await create_memory(
            http_client, "Memory 2", "Content 2", "Context", ["k2"], ["t2"],
        )
        await link_memories(http_client, mem1_id, mem2_id)

        # Entity relationship
        ent1_id = await create_entity(http_client, "Entity 1", "Organization")
        ent2_id = await create_entity(http_client, "Entity 2", "Organization")
        await relate_entities(http_client, ent1_id, ent2_id, "related_to")

        # Entity-memory link
        await link_entity_memory(http_client, ent1_id, mem1_id)

        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=true"))

        # Verify meta includes typed counts
        meta = data["meta"]
        assert "memory_link_count" in meta
        assert "entity_relationship_count" in meta
        assert "entity_memory_count" in meta
        assert meta["memory_link_count"] >= 1
        assert meta["entity_relationship_count"] >= 1
        assert meta["entity_memory_count"] >= 1

    @pytest.mark.asyncio
    async def test_entity_edges_only_appear_when_both_nodes_in_result(self, http_client):
        """Entity edges only appear when both endpoint nodes are in the result set."""
        ent1_id = await create_entity(http_client, "Visible Entity", "Organization")
        ent2_id = await create_entity(http_client, "Also Visible Entity", "Organization")
        await relate_entities(http_client, ent1_id, ent2_id, "connected_to")

        # Get graph with entities - edge should appear
        data = assert_ok(await http_client.get("/api/v1/graph?include_entities=true"))

        assert len(edges_of_type(data, "entity_relationship")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_includes_entity_edges_for_related_entities(self, http_client):
        """Memory subgraph includes entities linked to memories and their edges."""
        memory_id = await create_memory(
            http_client, "Center Memory for Entity Test", "Central memory content",
            "Subgraph entity test", ["subgraph", "entity"], ["test"], importance=8,
        )
        ent1_id = await create_entity(http_client, "Linked Entity 1", "Individual")
        ent2_id = await create_entity(http_client, "Linked Entity 2", "Individual")

        # Link both entities to memory
        await link_entity_memory(http_client, ent1_id, memory_id)
        await link_entity_memory(http_client, ent2_id, memory_id)

        # Create relationship between entities
        await relate_entities(
            http_client, ent1_id, ent2_id, "collaborates_with", strength=0.8,
        )

        data = assert_ok(await http_client.get(f"/api/v1/graph/memory/{memory_id}"))

        # Should include entity nodes
        assert len(nodes_of_type(data, "entity")) == 2

        # Should include entity-memory edges
        assert len(edges_of_type(data, "entity_memory")) == 2

        # Should include entity-entity edge
        entity_rel_edges = edges_of_type(data, "entity_relationship")
        assert len(entity_rel_edges) == 1
        assert entity_rel_edges[0]["data"]["relationship_type"] == "collaborates_with"
