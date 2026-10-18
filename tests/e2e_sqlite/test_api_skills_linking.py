"""E2E tests for Skill resource-linking REST API endpoints.

Uses in-memory SQLite for test isolation.
Tests linking files, code artifacts and documents to skills.
"""
import base64

import pytest

from tests.e2e_sqlite.skill_api_helpers import link_skill, post_skill, unlink_skill


class TestSkillResourceLinking:
    """Test skill resource linking REST API endpoints."""

    async def _create_skill(self, http_client):
        """Helper to create a skill and return its ID."""
        response = await post_skill(
            http_client,
            "link-test-skill",
            "A skill for testing resource linking",
            "# Link Test\n\nSteps here.",
            ["link-test"],
        )
        assert response.status_code == 201
        return response.json()["id"]

    async def _create_file(self, http_client):
        """Helper to create a file and return its ID."""
        payload = {
            "filename": "skill-link-test.txt",
            "description": "File for skill linking test",
            "data": base64.b64encode(b"test content").decode("utf-8"),
            "mime_type": "text/plain",
        }
        response = await http_client.post("/api/v1/files", json=payload)
        assert response.status_code == 201
        return response.json()["id"]

    async def _create_code_artifact(self, http_client):
        """Helper to create a code artifact and return its ID."""
        payload = {
            "title": "Skill Link Test Artifact",
            "description": "Code artifact for skill linking test",
            "code": "def test(): pass",
            "language": "python",
            "tags": ["test"],
        }
        response = await http_client.post(
            "/api/v1/code-artifacts", json=payload,
        )
        assert response.status_code == 201
        return response.json()["id"]

    async def _create_document(self, http_client):
        """Helper to create a document and return its ID."""
        payload = {
            "title": "Skill Link Test Document",
            "description": "Document for skill linking test",
            "content": "This is a test document for skill linking.",
            "document_type": "analysis",
            "tags": ["test"],
        }
        response = await http_client.post("/api/v1/documents", json=payload)
        assert response.status_code == 201
        return response.json()["id"]

    @pytest.mark.asyncio
    async def test_link_skill_to_file(self, http_client):
        """POST /api/v1/skills/{id}/files links a file to a skill."""
        skill_id = await self._create_skill(http_client)
        file_id = await self._create_file(http_client)

        response = await link_skill(http_client, skill_id, "files", file_id)
        assert response.status_code == 200
        data = response.json()
        assert data["skill_id"] == skill_id
        assert data["file_id"] == file_id
        assert data["linked"] is True

    @pytest.mark.asyncio
    async def test_unlink_skill_from_file(self, http_client):
        """DELETE /api/v1/skills/{id}/files/{file_id} unlinks a file."""
        skill_id = await self._create_skill(http_client)
        file_id = await self._create_file(http_client)

        await link_skill(http_client, skill_id, "files", file_id)

        response = await unlink_skill(http_client, skill_id, "files", file_id)
        assert response.status_code == 200
        data = response.json()
        assert data["unlinked"] is True

    @pytest.mark.asyncio
    async def test_link_skill_to_code_artifact(self, http_client):
        """POST /api/v1/skills/{id}/code-artifacts links a code artifact."""
        skill_id = await self._create_skill(http_client)
        ca_id = await self._create_code_artifact(http_client)

        response = await link_skill(http_client, skill_id, "code-artifacts", ca_id)
        assert response.status_code == 200
        data = response.json()
        assert data["skill_id"] == skill_id
        assert data["code_artifact_id"] == ca_id
        assert data["linked"] is True

    @pytest.mark.asyncio
    async def test_unlink_skill_from_code_artifact(self, http_client):
        """DELETE /api/v1/skills/{id}/code-artifacts/{ca_id} unlinks."""
        skill_id = await self._create_skill(http_client)
        ca_id = await self._create_code_artifact(http_client)

        await link_skill(http_client, skill_id, "code-artifacts", ca_id)

        response = await unlink_skill(http_client, skill_id, "code-artifacts", ca_id)
        assert response.status_code == 200
        data = response.json()
        assert data["unlinked"] is True

    @pytest.mark.asyncio
    async def test_link_skill_to_document(self, http_client):
        """POST /api/v1/skills/{id}/documents links a document."""
        skill_id = await self._create_skill(http_client)
        doc_id = await self._create_document(http_client)

        response = await link_skill(http_client, skill_id, "documents", doc_id)
        assert response.status_code == 200
        data = response.json()
        assert data["skill_id"] == skill_id
        assert data["document_id"] == doc_id
        assert data["linked"] is True

    @pytest.mark.asyncio
    async def test_unlink_skill_from_document(self, http_client):
        """DELETE /api/v1/skills/{id}/documents/{doc_id} unlinks."""
        skill_id = await self._create_skill(http_client)
        doc_id = await self._create_document(http_client)

        await link_skill(http_client, skill_id, "documents", doc_id)

        response = await unlink_skill(http_client, skill_id, "documents", doc_id)
        assert response.status_code == 200
        data = response.json()
        assert data["unlinked"] is True

    @pytest.mark.asyncio
    async def test_link_skill_not_found(self, http_client):
        """POST /api/v1/skills/{id}/files returns 404 for missing skill."""
        response = await link_skill(http_client, 99999, "files", 1)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_link_idempotent(self, http_client):
        """Bug #1: Linking same file twice should succeed (idempotent)."""
        skill_id = await self._create_skill(http_client)
        file_id = await self._create_file(http_client)

        resp1 = await link_skill(http_client, skill_id, "files", file_id)
        assert resp1.status_code == 200

        resp2 = await link_skill(http_client, skill_id, "files", file_id)
        assert resp2.status_code == 200
        assert resp2.json()["linked"] is True

    @pytest.mark.asyncio
    async def test_unlink_nonexistent_returns_false(self, http_client):
        """Bug #2: Unlinking non-existent link returns unlinked=False."""
        skill_id = await self._create_skill(http_client)

        response = await unlink_skill(http_client, skill_id, "files", 99999)
        assert response.status_code == 200
        assert response.json()["unlinked"] is False
