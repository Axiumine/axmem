"""E2E tests for Skill REST API endpoints.

Uses in-memory SQLite for test isolation.
Tests the /api/v1/skills list, CRUD, search and import/export endpoints.
"""
import pytest

from tests.e2e_sqlite.skill_api_helpers import (
    SKILLS_URL,
    export_skill_md,
    post_skill,
    post_skill_id,
)


class TestSkillAPIList:
    """Test GET /api/v1/skills endpoint."""

    @pytest.mark.asyncio
    async def test_list_skills_empty(self, http_client):
        """GET /api/v1/skills returns empty list initially."""
        response = await http_client.get(SKILLS_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["skills"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_skills_with_data(self, http_client):
        """GET /api/v1/skills returns created skills."""
        create_response = await post_skill(
            http_client,
            "list-test-skill",
            "A test skill for listing",
            "# List Test\n\nSteps here.",
            ["test"],
        )
        assert create_response.status_code == 201

        response = await http_client.get(SKILLS_URL)
        assert response.status_code == 200
        data = response.json()
        assert len(data["skills"]) >= 1
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_skills_filter_by_tags(self, http_client):
        """GET /api/v1/skills filters by tags."""
        await post_skill(
            http_client,
            "tagged-api-skill",
            "Skill with special tag",
            "Content for tagged skill.",
            ["special-api-tag"],
        )

        response = await http_client.get(f"{SKILLS_URL}?tags=special-api-tag")
        assert response.status_code == 200
        data = response.json()
        assert len(data["skills"]) >= 1

    @pytest.mark.asyncio
    async def test_list_skills_invalid_project_id(self, http_client):
        """GET /api/v1/skills returns 400 for invalid project_id."""
        response = await http_client.get(f"{SKILLS_URL}?project_id=not_a_number")
        assert response.status_code == 400
        assert "Invalid project_id" in response.json()["error"]


class TestSkillAPICrud:
    """Test Skill CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_skill_api(self, http_client):
        """POST /api/v1/skills creates a new skill."""
        response = await post_skill(
            http_client,
            "api-create-skill",
            "A skill created via REST API",
            "# API Skill\n\n## Steps\n1. First step",
            ["api", "create"],
            importance=8,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] > 0
        assert data["name"] == "api-create-skill"
        assert data["description"] == "A skill created via REST API"
        assert data["importance"] == 8

    @pytest.mark.asyncio
    async def test_create_skill_validation_error(self, http_client):
        """POST /api/v1/skills returns 400 for invalid data."""
        payload = {
            "name": "InvalidUpperCase",
            "description": "Should fail validation",
            "content": "Content",
        }
        response = await http_client.post(SKILLS_URL, json=payload)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_skill_api(self, http_client):
        """GET /api/v1/skills/{id} returns the skill."""
        skill_id = await post_skill_id(
            http_client,
            "get-api-skill",
            "Testing get endpoint",
            "Content for getting the skill.",
            ["test"],
        )

        response = await http_client.get(f"{SKILLS_URL}/{skill_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == skill_id
        assert data["name"] == "get-api-skill"

    @pytest.mark.asyncio
    async def test_get_skill_not_found(self, http_client):
        """GET /api/v1/skills/{id} returns 404 for missing skill."""
        response = await http_client.get(f"{SKILLS_URL}/99999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_skill_api(self, http_client):
        """PUT /api/v1/skills/{id} updates the skill."""
        skill_id = await post_skill_id(
            http_client,
            "update-api-skill",
            "Original description",
            "Original content for the skill.",
            ["original"],
        )

        update_payload = {
            "description": "Updated description via API",
            "tags": ["updated"],
        }
        response = await http_client.put(f"{SKILLS_URL}/{skill_id}", json=update_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description via API"

    @pytest.mark.asyncio
    async def test_update_skill_not_found(self, http_client):
        """PUT /api/v1/skills/{id} returns 404 for missing skill."""
        response = await http_client.put(f"{SKILLS_URL}/99999", json={"description": "Test"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_skill_api(self, http_client):
        """DELETE /api/v1/skills/{id} deletes the skill."""
        skill_id = await post_skill_id(
            http_client,
            "delete-api-skill",
            "Will be deleted",
            "Content that will be deleted.",
            ["delete"],
        )

        response = await http_client.delete(f"{SKILLS_URL}/{skill_id}")
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify deleted
        get_response = await http_client.get(f"{SKILLS_URL}/{skill_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_skill_not_found(self, http_client):
        """DELETE /api/v1/skills/{id} returns 404 for missing skill."""
        response = await http_client.delete(f"{SKILLS_URL}/99999")
        assert response.status_code == 404


class TestSkillAPISearch:
    """Test GET /api/v1/skills/search endpoint."""

    @pytest.mark.asyncio
    async def test_search_skills_api(self, http_client):
        """GET /api/v1/skills/search returns matching skills."""
        await post_skill(
            http_client,
            "search-api-deploy",
            "Deploy application to production using Docker containers",
            "# Deploy\n\n## Steps\n1. Build image\n2. Push to registry",
            ["deployment"],
            importance=8,
        )

        response = await http_client.get(f"{SKILLS_URL}/search?query=deploy+docker+production")
        assert response.status_code == 200
        data = response.json()
        assert "skills" in data
        assert "total" in data
        assert len(data["skills"]) >= 1

    @pytest.mark.asyncio
    async def test_search_skills_missing_query(self, http_client):
        """GET /api/v1/skills/search returns 400 without query param."""
        response = await http_client.get(f"{SKILLS_URL}/search")
        assert response.status_code == 400
        assert "query" in response.json()["error"].lower()


class TestSkillAPIImportExport:
    """Test import/export endpoints."""

    @pytest.mark.asyncio
    async def test_import_skill_api(self, http_client):
        """POST /api/v1/skills/import creates skill from SKILL.md format."""
        skill_md = """---
name: api-imported-skill
description: Imported via REST API
license: MIT
allowed-tools:
  - Read
  - Grep
---

# API Imported Skill

## Steps
1. First step
2. Second step
"""
        response = await http_client.post(f"{SKILLS_URL}/import", json={
            "skill_md": skill_md,
            "importance": 8,
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "api-imported-skill"
        assert data["description"] == "Imported via REST API"
        assert data["license"] == "MIT"
        assert data["importance"] == 8

    @pytest.mark.asyncio
    async def test_export_skill_api(self, http_client):
        """GET /api/v1/skills/{id}/export returns SKILL.md format."""
        skill_id = await post_skill_id(
            http_client,
            "api-export-skill",
            "Skill for export via API",
            "# Export\n\n## Steps\n1. Export step",
            ["export"],
            license="Apache-2.0",
        )

        response = await export_skill_md(http_client, skill_id)
        assert response.status_code == 200
        data = response.json()
        assert "skill_md" in data
        skill_md = data["skill_md"]
        assert "---" in skill_md
        assert "name: api-export-skill" in skill_md
        assert "# Export" in skill_md

    @pytest.mark.asyncio
    async def test_export_skill_not_found(self, http_client):
        """GET /api/v1/skills/{id}/export returns 404 for missing skill."""
        response = await export_skill_md(http_client, 99999)
        assert response.status_code == 404
