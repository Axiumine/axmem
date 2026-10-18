"""End-to-end tests for Graph REST API endpoints with PostgreSQL backend.

Tests the /api/v1/graph endpoints with real PostgreSQL database and pgvector
to validate CTE traversal, all 5 node types, and 8 edge types.

Related PostgreSQL graph suites live next to this module:
- test_graph_pagination_e2e.py (pagination, sorting, limits)
- test_graph_file_skill_edges_e2e.py (file and skill nodes and edges)
- test_graph_skill_subgraph_e2e.py (skill subgraph traversal and meta shape)
- test_graph_plan_task_e2e.py (plan and task nodes, edges and subgraphs)

Requires:
- PostgreSQL with pgvector running in Docker
- In-process FastMCP server (provided by conftest fixtures)
"""
import pytest

from tests.e2e.graph_pg_e2e_helpers import (
    assert_edge_types_present,
    assert_graph_nodes_present,
    assert_keys_present,
    assert_meta_counts_at_least,
    assert_node_types_present,
    create_code_artifact,
    create_document,
    create_entity,
    create_memory,
    create_project,
    link_resources,
    nodes_of_type,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_NODE_COUNT_KEYS = (
    "memory_count",
    "entity_count",
    "project_count",
    "document_count",
    "code_artifact_count",
)
_EDGE_COUNT_KEYS = (
    "memory_link_count",
    "entity_relationship_count",
    "entity_memory_count",
    "memory_project_count",
    "document_project_count",
    "code_artifact_project_count",
    "memory_document_count",
    "memory_code_artifact_count",
)
_OTHER_META_KEYS = ("center_node_id", "depth", "node_types", "max_nodes", "truncated")


@pytest.mark.e2e
async def test_graph_empty_returns_200(http_client):
    """GET /api/v1/graph returns empty graph initially."""
    response = await http_client.get("/api/v1/graph")

    assert response.status_code == 200
    data = response.json()
    assert_keys_present(data, ("nodes", "edges", "meta"), "empty graph response")


@pytest.mark.e2e
async def test_graph_with_all_node_types(http_client):
    """GET /api/v1/graph returns all 5 node types: memory, entity, project, document, code_artifact."""
    project_id = await create_project(
        http_client, "E2E Test Project", "Project for PostgreSQL E2E graph test",
    )
    document_id = await create_document(
        http_client,
        "E2E Test Document",
        "Document for PostgreSQL E2E graph test",
        "This is test document content for PostgreSQL graph E2E testing.",
        tags=["e2e-test"],
        project_id=project_id,
    )
    artifact_id = await create_code_artifact(
        http_client,
        "E2E Test Artifact",
        "Artifact for PostgreSQL E2E graph test",
        "def e2e_test(): return 'PostgreSQL'",
        tags=["e2e-test"],
        project_id=project_id,
    )
    memory_id = await create_memory(
        http_client,
        "E2E Test Memory",
        "Memory linked to project, document, and artifact for PostgreSQL E2E test",
        "Testing PostgreSQL graph API",
        ["e2e", "postgresql", "graph"],
        importance=8,
        project_ids=[project_id],
        document_ids=[document_id],
        code_artifact_ids=[artifact_id],
    )
    entity_id = await create_entity(
        http_client,
        "E2E Test Entity",
        "Organization",
        "Entity for PostgreSQL E2E graph test",
    )
    await link_resources(
        http_client, f"/api/v1/entities/{entity_id}/memories", {"memory_id": memory_id},
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_node_types_present(
        data, "memory", "entity", "project", "document", "code_artifact",
    )
    assert_edge_types_present(
        data,
        "memory_project",
        "document_project",
        "code_artifact_project",
        "memory_document",
        "memory_code_artifact",
        "entity_memory",
    )
    assert_meta_counts_at_least(data["meta"], 1, *_NODE_COUNT_KEYS)


@pytest.mark.e2e
async def test_graph_node_types_filter(http_client):
    """GET /api/v1/graph?node_types=memory,entity returns only specified types."""
    # Project should be excluded when filtering
    await create_project(http_client, "Filtered Project", "Should be excluded")
    await create_memory(
        http_client,
        "Filtered Memory",
        "Should be included",
        "Testing filter",
        ["filter"],
        tags=["test"],
    )

    response = await http_client.get("/api/v1/graph?node_types=memory,entity")
    assert response.status_code == 200
    data = response.json()

    assert len(nodes_of_type(data, "project")) == 0


@pytest.mark.e2e
async def test_subgraph_from_memory_center(http_client):
    """GET /api/v1/graph/subgraph?node_id=memory_X traverses from memory."""
    memory_id = await create_memory(
        http_client,
        "Subgraph Center Memory",
        "Memory at the center of PostgreSQL subgraph",
        "Testing PostgreSQL CTE subgraph",
        ["subgraph", "postgresql"],
        importance=8,
    )

    response = await http_client.get(f"/api/v1/graph/subgraph?node_id=memory_{memory_id}")
    if response.status_code != 200:
        print(f"ERROR Response: {response.status_code} - {response.text}")
    assert response.status_code == 200
    data = response.json()

    assert_keys_present(data, ("nodes", "edges", "meta"), "subgraph response")
    assert data["meta"]["center_node_id"] == f"memory_{memory_id}"

    # Center node should have depth 0
    center_node = next(n for n in data["nodes"] if n["id"] == f"memory_{memory_id}")
    assert center_node["depth"] == 0


@pytest.mark.e2e
async def test_subgraph_from_project_center(http_client):
    """GET /api/v1/graph/subgraph?node_id=project_X traverses from project."""
    project_id = await create_project(
        http_client, "Subgraph Center Project", "Project at center of subgraph",
    )
    await create_memory(
        http_client,
        "Project Linked Memory",
        "Memory linked to project for subgraph test",
        "Testing project center subgraph",
        ["project", "subgraph"],
        project_ids=[project_id],
    )

    response = await http_client.get(f"/api/v1/graph/subgraph?node_id=project_{project_id}")
    assert response.status_code == 200
    data = response.json()

    assert data["meta"]["center_node_id"] == f"project_{project_id}"
    # Should include linked memory via traversal
    assert len(nodes_of_type(data, "memory")) >= 1


@pytest.mark.e2e
async def test_subgraph_from_document_center(http_client):
    """GET /api/v1/graph/subgraph?node_id=document_X traverses from document."""
    document_id = await create_document(
        http_client,
        "Subgraph Center Document",
        "Document at center of subgraph",
        "Document content for PostgreSQL subgraph test",
        tags=["e2e-test"],
    )
    await create_memory(
        http_client,
        "Document Linked Memory",
        "Memory linked to document for subgraph test",
        "Testing document center subgraph",
        ["document", "subgraph"],
        document_ids=[document_id],
    )

    response = await http_client.get(f"/api/v1/graph/subgraph?node_id=document_{document_id}")
    assert response.status_code == 200
    data = response.json()

    assert data["meta"]["center_node_id"] == f"document_{document_id}"
    # Should include linked memory via traversal
    assert len(nodes_of_type(data, "memory")) >= 1


@pytest.mark.e2e
async def test_subgraph_from_code_artifact_center(http_client):
    """GET /api/v1/graph/subgraph?node_id=code_artifact_X traverses from artifact."""
    artifact_id = await create_code_artifact(
        http_client,
        "Subgraph Center Artifact",
        "Artifact at center of subgraph",
        "print('PostgreSQL E2E')",
        tags=["e2e-test"],
    )
    await create_memory(
        http_client,
        "Artifact Linked Memory",
        "Memory linked to artifact for subgraph test",
        "Testing artifact center subgraph",
        ["artifact", "subgraph"],
        code_artifact_ids=[artifact_id],
    )

    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=code_artifact_{artifact_id}",
    )
    assert response.status_code == 200
    data = response.json()

    assert data["meta"]["center_node_id"] == f"code_artifact_{artifact_id}"
    # Should include linked memory via traversal
    assert len(nodes_of_type(data, "memory")) >= 1


async def _create_cycle_memory(http_client, letter):
    """Create one PostgreSQL cycle-test memory (``A``, ``B`` or ``C``) and return its id."""
    return await create_memory(
        http_client,
        f"Cycle Node {letter} PostgreSQL",
        f"Node {letter} in cycle for PostgreSQL CTE test",
        "Testing PostgreSQL cycle detection",
        [f"cycle_{letter.lower()}_pg"],
        tags=["cycle-test"],
    )


def _assert_cycle_nodes_unique(data, memory_ids):
    """Assert every cycle memory is present exactly once and no node id repeats."""
    cycle_node_ids = {f"memory_{memory_id}" for memory_id in memory_ids}
    assert_graph_nodes_present(data, *cycle_node_ids)

    # Each cycle node should appear exactly once (cycle detection prevents duplicates)
    node_ids = [n["id"] for n in data["nodes"]]
    cycle_nodes_found = [i for i in node_ids if i in cycle_node_ids]
    assert len(cycle_nodes_found) == len(memory_ids), (
        f"Expected {len(memory_ids)} unique cycle nodes, found {len(cycle_nodes_found)}"
    )

    # Verify no duplicate IDs overall (cycle detection working)
    assert len(node_ids) == len(set(node_ids)), "Duplicate node IDs found - cycle detection failed"


@pytest.mark.e2e
async def test_subgraph_cycle_detection_postgresql(http_client):
    """PostgreSQL CTE handles cycles without infinite loops (uses ARRAY cycle detection)."""
    mem_a_id = await _create_cycle_memory(http_client, "A")
    mem_b_id = await _create_cycle_memory(http_client, "B")
    mem_c_id = await _create_cycle_memory(http_client, "C")

    # Create cycle: A -> B -> C -> A
    for source_id, target_id in [(mem_a_id, mem_b_id), (mem_b_id, mem_c_id), (mem_c_id, mem_a_id)]:
        await link_resources(
            http_client, f"/api/v1/memories/{source_id}/links", {"related_ids": [target_id]},
        )

    # Should complete without infinite loop, even with depth=3
    response = await http_client.get(f"/api/v1/graph/subgraph?node_id=memory_{mem_a_id}&depth=3")
    assert response.status_code == 200
    data = response.json()

    _assert_cycle_nodes_unique(data, [mem_a_id, mem_b_id, mem_c_id])


@pytest.mark.e2e
async def test_subgraph_multi_hop_traversal(http_client):
    """Subgraph traverses through multiple node types: Project -> Memory -> Entity."""
    project_id = await create_project(
        http_client, "Multi-Hop Project", "Project for multi-hop traversal test",
    )
    memory_id = await create_memory(
        http_client,
        "Multi-Hop Memory",
        "Memory linked to project for multi-hop test",
        "Testing multi-hop traversal",
        ["multi-hop"],
        project_ids=[project_id],
    )
    entity_id = await create_entity(
        http_client, "Multi-Hop Entity", "Individual", "Entity for multi-hop test",
    )
    await link_resources(
        http_client, f"/api/v1/entities/{entity_id}/memories", {"memory_id": memory_id},
    )

    # Start from project, should reach entity via project -> memory -> entity
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=project_{project_id}&depth=2",
    )
    assert response.status_code == 200
    data = response.json()

    assert_node_types_present(data, "project", "memory", "entity")
    # Entity should be at depth 2 (project -> memory -> entity)
    assert nodes_of_type(data, "entity")[0]["depth"] == 2


@pytest.mark.e2e
async def test_subgraph_meta_includes_all_new_counts(http_client):
    """Subgraph meta includes counts for all 5 node types and 8 edge types."""
    memory_id = await create_memory(
        http_client,
        "Meta Fields Test Memory",
        "Memory for meta fields test",
        "Testing meta fields",
        ["meta"],
    )

    response = await http_client.get(f"/api/v1/graph/subgraph?node_id=memory_{memory_id}")
    assert response.status_code == 200
    meta = response.json()["meta"]

    assert_keys_present(meta, _NODE_COUNT_KEYS, "subgraph meta node counts")
    assert_keys_present(meta, _EDGE_COUNT_KEYS, "subgraph meta edge counts")
    assert_keys_present(meta, _OTHER_META_KEYS, "subgraph meta other fields")


@pytest.mark.e2e
async def test_subgraph_node_types_filter_postgresql(http_client):
    """node_types filter controls which types are traversed in PostgreSQL CTE."""
    project_id = await create_project(
        http_client, "Filter Test Project PG", "Project for filter test",
    )
    memory_id = await create_memory(
        http_client,
        "Filter Test Memory PG",
        "Memory for filter test",
        "Testing filter",
        ["filter"],
        project_ids=[project_id],
    )

    # With node_types=memory only - should not traverse to project
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=memory_{memory_id}&node_types=memory",
    )
    assert response.status_code == 200
    assert len(nodes_of_type(response.json(), "project")) == 0

    # With node_types=memory,project - should traverse to project
    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=memory_{memory_id}&node_types=memory,project",
    )
    assert response.status_code == 200
    assert len(nodes_of_type(response.json(), "project")) >= 1
