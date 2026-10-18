"""E2E tests for skill nodes and skill edges in the Graph REST API.

Uses in-memory SQLite for test isolation.
Covers skill nodes/edges in the full graph, skill edge emission in the
subgraph endpoint and recursive-CTE traversal across skill branches.
"""
import pytest
from graph_sqlite_helpers import (
    assert_ok,
    create_code_artifact,
    create_document,
    create_file,
    create_memory,
    create_project,
    create_skill,
    edges_of_type,
    link_skill,
    node_ids,
    nodes_of_type,
)


class TestGraphSkillNodes:
    """Tests for skill node type and skill-related edges in the full graph endpoint."""

    @pytest.mark.asyncio
    async def test_graph_includes_skill_nodes(self, http_client):
        """GET /api/v1/graph includes skill nodes when skills exist."""
        skill_resp = await http_client.post("/api/v1/skills", json={
            "name": "graph-test-skill",
            "description": "Skill for graph node test",
            "content": "# Test Skill\n\nDoes nothing.",
            "tags": ["test"],
            "importance": 7,
        })
        assert skill_resp.status_code in [200, 201]
        skill_id = skill_resp.json()["id"]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        skill_nodes = nodes_of_type(data, "skill")
        assert len(skill_nodes) >= 1
        assert any(n["data"]["id"] == skill_id for n in skill_nodes)
        assert "skill_count" in data["meta"]
        assert data["meta"]["skill_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_memory_skill_edges(self, http_client):
        """Graph includes memory_skill edges when memory linked to skill."""
        skill_id = await create_skill(
            http_client, "memory-linked-skill", "Skill linked from memory",
            "# Linked Skill\n\nFor memory_skill edge test.",
        )
        await create_memory(
            http_client, "Skill Linked Memory", "Memory linked to skill",
            "Testing memory_skill edges", ["skill"], ["skill-test"],
            skill_ids=[skill_id],
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        memory_skill_edges = edges_of_type(data, "memory_skill")
        assert len(memory_skill_edges) >= 1
        assert "memory_skill_count" in data["meta"]
        assert data["meta"]["memory_skill_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_skill_project_edges(self, http_client):
        """Graph includes skill_project edges when skill linked to project."""
        project_id = await create_project(
            http_client, "Skill Edge Project", "Project for skill edge test",
        )
        await create_skill(
            http_client, "project-linked-skill", "Skill linked to project",
            "# Project Skill", project_id=project_id,
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        skill_project_edges = edges_of_type(data, "skill_project")
        assert len(skill_project_edges) >= 1
        assert "skill_project_count" in data["meta"]
        assert data["meta"]["skill_project_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_skill_file_edges(self, http_client):
        """Graph includes skill_file edges when skill linked to file."""
        skill_id = await create_skill(
            http_client, "file-linker-skill", "Skill that links to file",
            "# Skill File Link",
        )
        file_id = await create_file(
            http_client, "skill-linked.png", "File linked from skill",
        )

        link_resp = await link_skill(http_client, skill_id, "files", file_id)
        assert link_resp.status_code in [200, 201]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        skill_file_edges = edges_of_type(data, "skill_file")
        assert len(skill_file_edges) >= 1
        assert "skill_file_count" in data["meta"]
        assert data["meta"]["skill_file_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_skill_code_artifact_edges(self, http_client):
        """Graph includes skill_code_artifact edges when skill linked to artifact."""
        skill_id = await create_skill(
            http_client, "artifact-linker-skill", "Skill that links to artifact",
            "# Skill Artifact Link",
        )
        artifact_id = await create_code_artifact(
            http_client, "Skill Linked Artifact", "Artifact linked from skill", "x = 1",
        )

        link_resp = await link_skill(http_client, skill_id, "code-artifacts", artifact_id)
        assert link_resp.status_code in [200, 201]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        skill_code_artifact_edges = edges_of_type(data, "skill_code_artifact")
        assert len(skill_code_artifact_edges) >= 1
        assert "skill_code_artifact_count" in data["meta"]
        assert data["meta"]["skill_code_artifact_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_skill_document_edges(self, http_client):
        """Graph includes skill_document edges when skill linked to document."""
        skill_id = await create_skill(
            http_client, "document-linker-skill", "Skill that links to document",
            "# Skill Document Link",
        )
        doc_id = await create_document(
            http_client, "Skill Linked Document", "Document linked from skill",
            "Document content",
        )

        link_resp = await link_skill(http_client, skill_id, "documents", doc_id)
        assert link_resp.status_code in [200, 201]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        skill_document_edges = edges_of_type(data, "skill_document")
        assert len(skill_document_edges) >= 1
        assert "skill_document_count" in data["meta"]
        assert data["meta"]["skill_document_count"] >= 1


class TestSubgraphSkillEdges:
    """Tests for skill_file/skill_code_artifact/skill_document edge emission in /subgraph endpoint."""

    @pytest.mark.asyncio
    async def test_subgraph_includes_skill_file_edge(self, http_client):
        """Subgraph emits skill_file edge when both skill and file are in result."""
        skill_id = await create_skill(
            http_client, "subgraph-skill-file", "Skill linked to file", "# Subgraph",
        )
        file_id = await create_file(http_client, "subgraph-file.png", "Linked file")

        # Link skill -> file
        await link_skill(http_client, skill_id, "files", file_id)

        # Memory linked to BOTH skill and file (so CTE finds both via memory)
        memory_id = await create_memory(
            http_client, "Hub Memory", "Hub for skill+file traversal",
            "Subgraph test", ["hub"], ["test"],
            skill_ids=[skill_id], file_ids=[file_id],
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}"
            "&node_types=memory,skill,file",
        ))

        assert len(edges_of_type(data, "skill_file")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_includes_skill_code_artifact_edge(self, http_client):
        """Subgraph emits skill_code_artifact edge when both endpoints in result."""
        skill_id = await create_skill(
            http_client, "subgraph-skill-artifact", "Skill linked to artifact",
            "# Subgraph",
        )
        artifact_id = await create_code_artifact(
            http_client, "Subgraph Artifact", "Artifact linked from skill", "x = 1",
        )

        await link_skill(http_client, skill_id, "code-artifacts", artifact_id)

        memory_id = await create_memory(
            http_client, "Hub Memory CA", "Hub for skill+artifact traversal",
            "Subgraph test", ["hub"], ["test"],
            skill_ids=[skill_id], code_artifact_ids=[artifact_id],
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}"
            "&node_types=memory,skill,code_artifact",
        ))

        assert len(edges_of_type(data, "skill_code_artifact")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_includes_skill_document_edge(self, http_client):
        """Subgraph emits skill_document edge when both endpoints in result."""
        skill_id = await create_skill(
            http_client, "subgraph-skill-doc", "Skill linked to document", "# Subgraph",
        )
        doc_id = await create_document(
            http_client, "Subgraph Document", "Document linked from skill",
            "Document content",
        )

        await link_skill(http_client, skill_id, "documents", doc_id)

        memory_id = await create_memory(
            http_client, "Hub Memory Doc", "Hub for skill+doc traversal",
            "Subgraph test", ["hub"], ["test"],
            skill_ids=[skill_id], document_ids=[doc_id],
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}"
            "&node_types=memory,skill,document",
        ))

        assert len(edges_of_type(data, "skill_document")) >= 1


class TestSubgraphSkillCTE:
    """Tests for SQLite recursive CTE traversal across skill branches."""

    @pytest.mark.asyncio
    async def test_subgraph_from_skill_center(self, http_client):
        """Subgraph centered on skill returns skill node + linked file at depth 1."""
        skill_id = await create_skill(
            http_client, "skill-center", "Skill at center", "# Center Skill",
        )
        file_id = await create_file(
            http_client, "skill-center-file.png", "File linked to center skill",
        )
        await link_skill(http_client, skill_id, "files", file_id)

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=skill_{skill_id}"
            "&node_types=skill,file",
        ))

        ids = node_ids(data)
        assert f"skill_{skill_id}" in ids
        assert f"file_{file_id}" in ids
        assert len(edges_of_type(data, "skill_file")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_traverses_memory_skill(self, http_client):
        """CTE traverses memory↔skill association from memory center."""
        skill_id = await create_skill(
            http_client, "memory-skill-traversal", "Skill for traversal test",
            "# Traversal",
        )
        memory_id = await create_memory(
            http_client, "Memory for skill traversal", "Memory traversed to skill",
            "Subgraph test", ["t"], ["test"], skill_ids=[skill_id],
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}"
            "&node_types=memory,skill",
        ))

        assert f"skill_{skill_id}" in node_ids(data)

    @pytest.mark.asyncio
    async def test_subgraph_traverses_skill_project(self, http_client):
        """CTE traverses skill↔project from skill center."""
        project_id = await create_project(http_client, "Skill CTE Project", "P")
        skill_id = await create_skill(
            http_client, "skill-with-project", "Skill linked to project", "# T",
            project_id=project_id,
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=skill_{skill_id}"
            "&node_types=skill,project",
        ))

        assert f"project_{project_id}" in node_ids(data)

    @pytest.mark.asyncio
    async def test_subgraph_traverses_skill_to_code_artifact(self, http_client):
        """CTE traverses skill↔code_artifact from skill center."""
        skill_id = await create_skill(
            http_client, "skill-with-artifact", "Skill linked to artifact", "# T",
        )
        artifact_id = await create_code_artifact(
            http_client, "Artifact via skill", "d", "x = 1",
        )
        await link_skill(http_client, skill_id, "code-artifacts", artifact_id)

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=skill_{skill_id}"
            "&node_types=skill,code_artifact",
        ))

        assert f"code_artifact_{artifact_id}" in node_ids(data)

    @pytest.mark.asyncio
    async def test_subgraph_traverses_skill_to_document(self, http_client):
        """CTE traverses skill↔document from skill center."""
        skill_id = await create_skill(
            http_client, "skill-with-document", "Skill linked to document", "# T",
        )
        doc_id = await create_document(
            http_client, "Document via skill", "d", "Document content",
        )
        await link_skill(http_client, skill_id, "documents", doc_id)

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=skill_{skill_id}"
            "&node_types=skill,document",
        ))

        assert f"document_{doc_id}" in node_ids(data)
