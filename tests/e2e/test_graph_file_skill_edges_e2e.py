"""End-to-end tests for file and skill graph nodes and edges with PostgreSQL.

Verifies that GET /api/v1/graph exposes file and skill nodes, the memory_file,
file_project, skill_project, memory_skill, skill_file, skill_code_artifact,
skill_document and entity_file edges, and their matching meta counts.

Requires:
- PostgreSQL with pgvector running in Docker
- In-process FastMCP server (provided by conftest fixtures)
"""
import pytest
from sqlalchemy import insert

from app.repositories.postgres.postgres_tables import entity_file_association
from tests.e2e.graph_pg_e2e_helpers import (
    assert_edge_types_present,
    assert_meta_counts_at_least,
    assert_typed_nodes_present,
    create_code_artifact,
    create_document,
    create_entity,
    create_file,
    create_memory,
    create_project,
    create_skill,
    link_resources,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.e2e
async def test_graph_includes_file_nodes(http_client):
    """GET /api/v1/graph includes file nodes when files exist (PostgreSQL)."""
    file_id = await create_file(
        http_client,
        "e2e-file-node.png",
        "File for PostgreSQL graph node test",
        tags=["e2e-test"],
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_typed_nodes_present(data, "file", file_id)
    assert_meta_counts_at_least(data["meta"], 1, "file_count")


@pytest.mark.e2e
async def test_graph_memory_file_edges(http_client):
    """Graph includes memory_file edges when memory linked to file (PostgreSQL)."""
    file_id = await create_file(
        http_client,
        "e2e-linked-file.png",
        "File for memory_file edge test",
        tags=["e2e-test"],
    )
    await create_memory(
        http_client,
        "E2E File Linked Memory",
        "Memory linked to file for PostgreSQL E2E test",
        "Testing memory_file edges",
        ["file", "e2e"],
        file_ids=[file_id],
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "memory_file")
    assert_meta_counts_at_least(data["meta"], 1, "memory_file_count")


@pytest.mark.e2e
async def test_graph_file_project_edges(http_client):
    """Graph includes file_project edges when file linked to project (PostgreSQL)."""
    project_id = await create_project(
        http_client, "E2E File Edge Project", "Project for file_project edge test",
    )
    await create_file(
        http_client,
        "e2e-project-linked.png",
        "File linked to project",
        tags=["e2e-test"],
        project_id=project_id,
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "file_project")
    assert_meta_counts_at_least(data["meta"], 1, "file_project_count")


@pytest.mark.e2e
async def test_graph_includes_skill_nodes(http_client):
    """GET /api/v1/graph includes skill nodes when skills exist (PostgreSQL)."""
    skill_id = await create_skill(
        http_client,
        "e2e-skill-node",
        "Skill for PostgreSQL graph node test",
        "# E2E Skill\n\nDoes nothing.",
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_typed_nodes_present(data, "skill", skill_id)
    assert_meta_counts_at_least(data["meta"], 1, "skill_count")


@pytest.mark.e2e
async def test_graph_skill_project_edges(http_client):
    """Graph includes skill_project edges when skill linked to project (PostgreSQL)."""
    project_id = await create_project(
        http_client, "E2E Skill Edge Project", "Project for skill_project edge test",
    )
    await create_skill(
        http_client,
        "e2e-project-linked-skill",
        "Skill linked to project",
        "# Project Skill",
        project_id=project_id,
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "skill_project")
    assert_meta_counts_at_least(data["meta"], 1, "skill_project_count")


@pytest.mark.e2e
async def test_graph_memory_skill_edges(http_client):
    """Graph includes memory_skill edges when memory linked to skill (PostgreSQL)."""
    skill_id = await create_skill(
        http_client,
        "e2e-memory-linked-skill",
        "Skill linked from memory",
        "# Linked Skill\n\nFor memory_skill E2E test.",
    )
    await create_memory(
        http_client,
        "E2E Skill Linked Memory",
        "Memory linked to skill for PostgreSQL E2E test",
        "Testing memory_skill edges",
        ["skill", "e2e"],
        skill_ids=[skill_id],
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "memory_skill")
    assert_meta_counts_at_least(data["meta"], 1, "memory_skill_count")


@pytest.mark.e2e
async def test_graph_skill_file_edges(http_client):
    """Graph includes skill_file edges when skill linked to file (PostgreSQL)."""
    skill_id = await create_skill(
        http_client,
        "e2e-skill-file-linker",
        "Skill that links to file",
        "# Skill File Link",
    )
    file_id = await create_file(http_client, "e2e-skill-linked.png", "File linked from skill")
    await link_resources(
        http_client, f"/api/v1/skills/{skill_id}/files", {"file_id": file_id},
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "skill_file")
    assert_meta_counts_at_least(data["meta"], 1, "skill_file_count")


@pytest.mark.e2e
async def test_graph_skill_code_artifact_edges(http_client):
    """Graph includes skill_code_artifact edges when skill linked to artifact (PostgreSQL)."""
    skill_id = await create_skill(
        http_client,
        "e2e-skill-artifact-linker",
        "Skill that links to artifact",
        "# Skill Artifact Link",
    )
    artifact_id = await create_code_artifact(
        http_client, "E2E Skill Linked Artifact", "Artifact linked from skill", "x = 1",
    )
    await link_resources(
        http_client,
        f"/api/v1/skills/{skill_id}/code-artifacts",
        {"code_artifact_id": artifact_id},
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "skill_code_artifact")
    assert_meta_counts_at_least(data["meta"], 1, "skill_code_artifact_count")


@pytest.mark.e2e
async def test_graph_skill_document_edges(http_client):
    """Graph includes skill_document edges when skill linked to document (PostgreSQL)."""
    skill_id = await create_skill(
        http_client,
        "e2e-skill-document-linker",
        "Skill that links to document",
        "# Skill Document Link",
    )
    doc_id = await create_document(
        http_client,
        "E2E Skill Linked Document",
        "Document linked from skill",
        "Document content",
    )
    await link_resources(
        http_client,
        f"/api/v1/skills/{skill_id}/documents",
        {"document_id": doc_id},
    )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "skill_document")
    assert_meta_counts_at_least(data["meta"], 1, "skill_document_count")


@pytest.mark.e2e
async def test_graph_entity_file_edges(http_client, postgres_app):
    """Graph includes entity_file edges when entity linked to file (PostgreSQL)."""
    entity_id = await create_entity(
        http_client, "E2E Entity For File Edge", "Organization", "Entity to link to file",
    )
    file_id = await create_file(
        http_client,
        "e2e-entity-linked.png",
        "File linked to entity",
        tags=["e2e-test"],
    )

    db_adapter = postgres_app.entity_service.entity_repo.db_adapter
    async with db_adapter.system_session() as session:
        await session.execute(
            insert(entity_file_association).values(
                entity_id=entity_id, file_id=file_id,
            ),
        )

    response = await http_client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    assert_edge_types_present(data, "entity_file")
    assert_meta_counts_at_least(data["meta"], 1, "entity_file_count")
