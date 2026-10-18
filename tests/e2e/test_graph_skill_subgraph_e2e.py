"""End-to-end tests for skill subgraph traversal and meta shape with PostgreSQL.

Verifies that the CTE behind GET /api/v1/graph/subgraph traverses skill
associations (file, memory, code artifact, document, project) and that the
full-graph and subgraph meta expose every skill_/file_ count field.

Requires:
- PostgreSQL with pgvector running in Docker
- In-process FastMCP server (provided by conftest fixtures)
"""
import pytest

from tests.e2e.graph_pg_e2e_helpers import (
    assert_graph_nodes_present,
    assert_keys_present,
    create_code_artifact,
    create_document,
    create_file,
    create_memory,
    create_project,
    create_skill,
    edges_of_type,
    link_resources,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_EXPECTED_NEW_META_KEYS = {
    "file_count",
    "skill_count",
    "memory_file_count",
    "file_project_count",
    "entity_file_count",
    "memory_skill_count",
    "skill_project_count",
    "skill_file_count",
    "skill_code_artifact_count",
    "skill_document_count",
}


@pytest.mark.e2e
async def test_subgraph_from_skill_center_postgres(http_client):
    """Subgraph centered on skill returns skill node + linked file (PostgreSQL)."""
    skill_id = await create_skill(http_client, "e2e-skill-center", "Skill at center", "# Center")
    file_id = await create_file(
        http_client, "e2e-skill-center-file.png", "File linked to center skill",
    )
    await link_resources(
        http_client, f"/api/v1/skills/{skill_id}/files", {"file_id": file_id},
    )

    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=skill_{skill_id}&node_types=skill,file",
    )
    assert response.status_code == 200
    data = response.json()

    assert_graph_nodes_present(data, f"skill_{skill_id}", f"file_{file_id}")
    assert len(edges_of_type(data, "skill_file")) >= 1


@pytest.mark.e2e
async def test_subgraph_traverses_memory_skill_postgres(http_client):
    """CTE traverses memory↔skill association from memory center (PostgreSQL)."""
    skill_id = await create_skill(http_client, "e2e-mem-skill-trav", "Skill", "# T")
    memory_id = await create_memory(
        http_client,
        "E2E Memory Skill Trav",
        "Memory traversed to skill",
        "Subgraph",
        ["t"],
        skill_ids=[skill_id],
    )

    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=memory_{memory_id}&node_types=memory,skill",
    )
    assert response.status_code == 200

    assert_graph_nodes_present(response.json(), f"skill_{skill_id}")


@pytest.mark.e2e
async def test_subgraph_traverses_skill_to_code_artifact_postgres(http_client):
    """CTE traverses skill↔code_artifact from skill center (PostgreSQL)."""
    skill_id = await create_skill(http_client, "e2e-skill-artifact-trav", "Skill", "# T")
    artifact_id = await create_code_artifact(
        http_client, "E2E Artifact via skill", "d", "x=1",
    )
    await link_resources(
        http_client,
        f"/api/v1/skills/{skill_id}/code-artifacts",
        {"code_artifact_id": artifact_id},
    )

    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=skill_{skill_id}"
        "&node_types=skill,code_artifact",
    )
    assert response.status_code == 200

    assert_graph_nodes_present(response.json(), f"code_artifact_{artifact_id}")


@pytest.mark.e2e
async def test_subgraph_traverses_skill_to_document_postgres(http_client):
    """CTE traverses skill↔document from skill center (PostgreSQL)."""
    skill_id = await create_skill(http_client, "e2e-skill-doc-trav", "Skill", "# T")
    doc_id = await create_document(
        http_client, "E2E Doc via skill", "d", "Document content",
    )
    await link_resources(
        http_client,
        f"/api/v1/skills/{skill_id}/documents",
        {"document_id": doc_id},
    )

    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=skill_{skill_id}"
        "&node_types=skill,document",
    )
    assert response.status_code == 200

    assert_graph_nodes_present(response.json(), f"document_{doc_id}")


@pytest.mark.e2e
async def test_subgraph_traverses_skill_project_postgres(http_client):
    """CTE traverses skill↔project from skill center (PostgreSQL)."""
    project_id = await create_project(http_client, "E2E Skill CTE Project", "P")
    skill_id = await create_skill(
        http_client,
        "e2e-skill-project-trav",
        "Skill linked to project",
        "# T",
        project_id=project_id,
    )

    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=skill_{skill_id}&node_types=skill,project",
    )
    assert response.status_code == 200

    assert_graph_nodes_present(response.json(), f"project_{project_id}")


@pytest.mark.e2e
async def test_full_graph_meta_includes_all_skill_file_counts_postgres(http_client):
    """Full /api/v1/graph meta exposes every skill_/file_ count field (PostgreSQL)."""
    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    meta = response.json()["meta"]

    assert_keys_present(meta, _EXPECTED_NEW_META_KEYS, "full graph meta")


@pytest.mark.e2e
async def test_subgraph_meta_includes_all_skill_file_counts_postgres(http_client):
    """Subgraph meta exposes every skill_/file_ count field (PostgreSQL)."""
    memory_id = await create_memory(
        http_client, "E2E Meta Shape", "C", "Testing meta shape", ["m"],
    )

    response = await http_client.get(
        f"/api/v1/graph/subgraph?node_id=memory_{memory_id}",
    )
    assert response.status_code == 200
    meta = response.json()["meta"]

    assert_keys_present(meta, _EXPECTED_NEW_META_KEYS, "subgraph meta")
