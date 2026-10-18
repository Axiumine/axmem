"""E2E tests for project, document, code_artifact and file nodes in the Graph REST API.

Uses in-memory SQLite for test isolation.
Covers the node types and edges added beyond memories/entities, subgraphs
centered on them, and the meta count fields of both graph endpoints.
"""
import pytest
from graph_sqlite_helpers import (
    TINY_PNG_BASE64,
    assert_ok,
    create_code_artifact,
    create_document,
    create_entity,
    create_file,
    create_memory,
    create_project,
    edges_of_type,
    nodes_of_type,
)


class TestGraphNewNodeTypes:
    """Tests for project, document, and code_artifact node types in graph API."""

    @pytest.mark.asyncio
    async def test_graph_includes_project_nodes(self, http_client):
        """GET /api/v1/graph includes project nodes when projects exist."""
        project_resp = await http_client.post("/api/v1/projects", json={
            "name": "Test Project",
            "description": "Project for graph test",
            "project_type": "development",
        })
        assert project_resp.status_code in [200, 201]
        project_id = project_resp.json()["id"]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check project node exists and meta includes project_count
        project_nodes = nodes_of_type(data, "project")
        assert len(project_nodes) >= 1
        assert any(n["data"]["id"] == project_id for n in project_nodes)
        assert "project_count" in data["meta"]
        assert data["meta"]["project_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_includes_document_nodes(self, http_client):
        """GET /api/v1/graph includes document nodes when documents exist."""
        doc_resp = await http_client.post("/api/v1/documents", json={
            "title": "Test Document",
            "description": "Document for graph test",
            "content": "This is the document content for testing graph API",
            "document_type": "text",
            "tags": ["test"],
        })
        assert doc_resp.status_code in [200, 201]
        document_id = doc_resp.json()["id"]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check document node exists and meta includes document_count
        document_nodes = nodes_of_type(data, "document")
        assert len(document_nodes) >= 1
        assert any(n["data"]["id"] == document_id for n in document_nodes)
        assert "document_count" in data["meta"]
        assert data["meta"]["document_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_includes_code_artifact_nodes(self, http_client):
        """GET /api/v1/graph includes code_artifact nodes when artifacts exist."""
        artifact_resp = await http_client.post("/api/v1/code-artifacts", json={
            "title": "Test Artifact",
            "description": "Code artifact for graph test",
            "code": "def hello(): return 'world'",
            "language": "python",
            "tags": ["test"],
        })
        assert artifact_resp.status_code in [200, 201]
        artifact_id = artifact_resp.json()["id"]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check code_artifact node exists and meta includes code_artifact_count
        code_artifact_nodes = nodes_of_type(data, "code_artifact")
        assert len(code_artifact_nodes) >= 1
        assert any(n["data"]["id"] == artifact_id for n in code_artifact_nodes)
        assert "code_artifact_count" in data["meta"]
        assert data["meta"]["code_artifact_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_memory_project_edges(self, http_client):
        """Graph includes memory_project edges when memory linked to project."""
        project_id = await create_project(
            http_client, "Edge Test Project", "Project for edge test",
        )
        await create_memory(
            http_client, "Project Linked Memory", "Memory linked to project",
            "Testing memory_project edges", ["project"], ["project-test"],
            project_ids=[project_id],
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check memory_project edge exists and meta includes count
        memory_project_edges = edges_of_type(data, "memory_project")
        assert len(memory_project_edges) >= 1
        assert "memory_project_count" in data["meta"]
        assert data["meta"]["memory_project_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_document_project_edges(self, http_client):
        """Graph includes document_project edges when document linked to project."""
        project_id = await create_project(
            http_client, "Document Edge Project", "Project for document edge test",
        )
        await create_document(
            http_client, "Project Linked Document", "Document linked to project",
            "Document content for edge testing", tags=["test"], project_id=project_id,
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check document_project edge exists and meta includes count
        document_project_edges = edges_of_type(data, "document_project")
        assert len(document_project_edges) >= 1
        assert "document_project_count" in data["meta"]
        assert data["meta"]["document_project_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_code_artifact_project_edges(self, http_client):
        """Graph includes code_artifact_project edges when artifact linked to project."""
        project_id = await create_project(
            http_client, "Artifact Edge Project", "Project for artifact edge test",
        )
        await create_code_artifact(
            http_client, "Project Linked Artifact", "Artifact linked to project",
            "print('hello')", tags=["test"], project_id=project_id,
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check code_artifact_project edge exists and meta includes count
        code_artifact_project_edges = edges_of_type(data, "code_artifact_project")
        assert len(code_artifact_project_edges) >= 1
        assert "code_artifact_project_count" in data["meta"]
        assert data["meta"]["code_artifact_project_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_memory_document_edges(self, http_client):
        """Graph includes memory_document edges when memory linked to document."""
        document_id = await create_document(
            http_client, "Document for Memory Link", "Document to be linked",
            "Document content", tags=["test"],
        )
        await create_memory(
            http_client, "Document Linked Memory", "Memory linked to document",
            "Testing memory_document edges", ["document"], ["document-test"],
            document_ids=[document_id],
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check memory_document edge exists and meta includes count
        memory_document_edges = edges_of_type(data, "memory_document")
        assert len(memory_document_edges) >= 1
        assert "memory_document_count" in data["meta"]
        assert data["meta"]["memory_document_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_memory_code_artifact_edges(self, http_client):
        """Graph includes memory_code_artifact edges when memory linked to artifact."""
        artifact_id = await create_code_artifact(
            http_client, "Artifact for Memory Link", "Artifact to be linked",
            "x = 1", tags=["test"],
        )
        await create_memory(
            http_client, "Artifact Linked Memory", "Memory linked to artifact",
            "Testing memory_code_artifact edges", ["artifact"], ["artifact-test"],
            code_artifact_ids=[artifact_id],
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        # Check memory_code_artifact edge exists and meta includes count
        memory_code_artifact_edges = edges_of_type(data, "memory_code_artifact")
        assert len(memory_code_artifact_edges) >= 1
        assert "memory_code_artifact_count" in data["meta"]
        assert data["meta"]["memory_code_artifact_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_node_types_filter_excludes_projects(self, http_client):
        """node_types parameter can exclude project nodes."""
        await create_project(http_client, "Filter Test Project", "Project for filter test")

        # Get graph without projects
        data = assert_ok(await http_client.get("/api/v1/graph?node_types=memory,entity"))

        # Should have no project nodes
        assert len(nodes_of_type(data, "project")) == 0

    @pytest.mark.asyncio
    async def test_subgraph_from_project_center(self, http_client):
        """Subgraph can be centered on a project node."""
        project_id = await create_project(
            http_client, "Center Project", "Project to center subgraph on",
        )
        await create_memory(
            http_client, "Project Related Memory", "Memory for project subgraph test",
            "Testing subgraph from project center", ["project"], ["test"],
            project_ids=[project_id],
        )

        # Get subgraph centered on project
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=project_{project_id}",
        ))

        # Center should be the project
        assert data["meta"]["center_node_id"] == f"project_{project_id}"

        # Should include the linked memory
        assert len(nodes_of_type(data, "memory")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_from_document_center(self, http_client):
        """Subgraph can be centered on a document node."""
        document_id = await create_document(
            http_client, "Center Document", "Document to center subgraph on",
            "Document content for subgraph test", tags=["test"],
        )
        await create_memory(
            http_client, "Document Related Memory",
            "Memory for document subgraph test",
            "Testing subgraph from document center", ["document"], ["test"],
            document_ids=[document_id],
        )

        # Get subgraph centered on document
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=document_{document_id}",
        ))

        # Center should be the document
        assert data["meta"]["center_node_id"] == f"document_{document_id}"

        # Should include the linked memory
        assert len(nodes_of_type(data, "memory")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_from_code_artifact_center(self, http_client):
        """Subgraph can be centered on a code_artifact node."""
        artifact_id = await create_code_artifact(
            http_client, "Center Artifact", "Artifact to center subgraph on",
            "result = 42", tags=["test"],
        )
        await create_memory(
            http_client, "Artifact Related Memory",
            "Memory for artifact subgraph test",
            "Testing subgraph from artifact center", ["artifact"], ["test"],
            code_artifact_ids=[artifact_id],
        )

        # Get subgraph centered on artifact
        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=code_artifact_{artifact_id}",
        ))

        # Center should be the code artifact
        assert data["meta"]["center_node_id"] == f"code_artifact_{artifact_id}"

        # Should include the linked memory
        assert len(nodes_of_type(data, "memory")) >= 1

    @pytest.mark.asyncio
    async def test_subgraph_meta_includes_new_counts(self, http_client):
        """Subgraph meta includes counts for all new node and edge types."""
        memory_id = await create_memory(
            http_client, "Meta Test Memory", "Memory for meta fields test",
            "Testing new meta fields", ["meta"], ["test"],
        )

        data = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}",
        ))

        meta = data["meta"]

        # Check new node count fields exist
        assert "project_count" in meta
        assert "document_count" in meta
        assert "code_artifact_count" in meta

        # Check new edge count fields exist
        assert "memory_project_count" in meta
        assert "document_project_count" in meta
        assert "code_artifact_project_count" in meta
        assert "memory_document_count" in meta
        assert "memory_code_artifact_count" in meta


class TestGraphFileNodes:
    """Tests for file node type and memory_file edges in the full graph endpoint."""

    @pytest.mark.asyncio
    async def test_graph_includes_file_nodes(self, http_client):
        """GET /api/v1/graph includes file nodes when files exist."""
        file_resp = await http_client.post("/api/v1/files", json={
            "filename": "graph-test.png",
            "description": "File for graph node test",
            "data": TINY_PNG_BASE64,
            "mime_type": "image/png",
            "tags": ["test"],
        })
        assert file_resp.status_code in [200, 201]
        file_id = file_resp.json()["id"]

        data = assert_ok(await http_client.get("/api/v1/graph"))

        file_nodes = nodes_of_type(data, "file")
        assert len(file_nodes) >= 1
        assert any(n["data"]["id"] == file_id for n in file_nodes)
        assert "file_count" in data["meta"]
        assert data["meta"]["file_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_memory_file_edges(self, http_client):
        """Graph includes memory_file edges when memory linked to file."""
        file_id = await create_file(
            http_client, "linked-file.png", "File to link from memory", tags=["test"],
        )
        await create_memory(
            http_client, "File Linked Memory", "Memory linked to file",
            "Testing memory_file edges", ["file"], ["file-test"], file_ids=[file_id],
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        memory_file_edges = edges_of_type(data, "memory_file")
        assert len(memory_file_edges) >= 1
        assert "memory_file_count" in data["meta"]
        assert data["meta"]["memory_file_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_file_project_edges(self, http_client):
        """Graph includes file_project edges when file linked to project."""
        project_id = await create_project(
            http_client, "File Edge Project", "Project for file edge test",
        )
        await create_file(
            http_client, "project-linked.png", "File linked to project",
            tags=["test"], project_id=project_id,
        )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        file_project_edges = edges_of_type(data, "file_project")
        assert len(file_project_edges) >= 1
        assert "file_project_count" in data["meta"]
        assert data["meta"]["file_project_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_entity_file_edges(self, http_client, sqlite_app):
        """Graph includes entity_file edges when entity linked to file."""
        from sqlalchemy import insert

        from app.repositories.sqlite.sqlite_tables import entity_file_association

        entity_id = await create_entity(
            http_client, "Entity For File Edge", "Organization",
            notes="Entity to link to file",
        )
        file_id = await create_file(
            http_client, "entity-linked.png", "File linked to entity", tags=["test"],
        )

        # No public API to populate entity_file_association — insert directly
        db_adapter = sqlite_app.entity_service.entity_repo.db_adapter
        async with db_adapter.system_session() as session:
            await session.execute(
                insert(entity_file_association).values(
                    entity_id=entity_id, file_id=file_id,
                ),
            )

        data = assert_ok(await http_client.get("/api/v1/graph"))

        entity_file_edges = edges_of_type(data, "entity_file")
        assert len(entity_file_edges) >= 1
        assert "entity_file_count" in data["meta"]
        assert data["meta"]["entity_file_count"] >= 1


class TestGraphMetaShape:
    """Regression: every new node/edge meta count appears in both graph endpoints."""

    EXPECTED_NEW_KEYS = {
        # Node count fields
        "file_count",
        "skill_count",
        # Edge count fields added in Phase 1
        "memory_file_count",
        "file_project_count",
        "entity_file_count",
        "memory_skill_count",
        "skill_project_count",
        "skill_file_count",
        "skill_code_artifact_count",
        "skill_document_count",
    }

    @pytest.mark.asyncio
    async def test_full_graph_meta_includes_all_skill_file_counts(self, http_client):
        """Full /api/v1/graph meta exposes every skill_/file_ count field."""
        meta = assert_ok(await http_client.get("/api/v1/graph"))["meta"]

        missing = self.EXPECTED_NEW_KEYS - set(meta.keys())
        assert not missing, f"Missing meta keys in full graph: {missing}"

    @pytest.mark.asyncio
    async def test_subgraph_meta_includes_all_skill_file_counts(self, http_client):
        """Subgraph meta exposes every skill_/file_ count field."""
        memory_id = await create_memory(
            http_client, "Meta Shape Memory", "C", "Testing meta shape", ["m"], ["test"],
        )

        meta = assert_ok(await http_client.get(
            f"/api/v1/graph/subgraph?node_id=memory_{memory_id}",
        ))["meta"]

        missing = self.EXPECTED_NEW_KEYS - set(meta.keys())
        assert not missing, f"Missing meta keys in subgraph: {missing}"
