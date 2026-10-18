"""E2E regression tests for Skill REST API bugs and link lookup.

Uses in-memory SQLite for test isolation.
Covers exploratory-testing regressions and the /api/v1/skills/{id}/links endpoint.
"""
import pytest

from tests.e2e_sqlite.skill_api_helpers import (
    SKILLS_URL,
    export_skill_md,
    link_skill,
    post_skill,
    post_skill_id,
)


class TestSkillBugRegressions:
    """Regression tests for bugs found in exploratory testing."""

    @pytest.mark.asyncio
    async def test_export_includes_tags(self, http_client):
        """Bug #3: Exported SKILL.md should contain tags."""
        skill_id = await post_skill_id(
            http_client,
            "export-tag-bug",
            "Regression test for export tags",
            "# Content",
            ["alpha", "beta"],
        )

        export_response = await export_skill_md(http_client, skill_id)
        assert export_response.status_code == 200
        skill_md = export_response.json()["skill_md"]
        assert "tags:" in skill_md
        assert "- alpha" in skill_md
        assert "- beta" in skill_md

    @pytest.mark.asyncio
    async def test_import_export_roundtrip_preserves_tags(self, http_client):
        """Bug #3: Tags survive export→import roundtrip."""
        skill_id = await post_skill_id(
            http_client,
            "roundtrip-tag-bug",
            "Roundtrip tags regression",
            "# Content",
            ["one", "two"],
        )

        export_response = await export_skill_md(http_client, skill_id)
        skill_md = export_response.json()["skill_md"]

        # Delete original so re-import doesn't hit name uniqueness
        await http_client.delete(f"{SKILLS_URL}/{skill_id}")

        import_response = await http_client.post(
            f"{SKILLS_URL}/import",
            json={"skill_md": skill_md, "importance": 7},
        )
        assert import_response.status_code == 201
        reimported = import_response.json()
        assert reimported["tags"] == ["one", "two"]

    @pytest.mark.asyncio
    async def test_duplicate_skill_name_rejected(self, http_client):
        """Bug #4: Creating two skills with the same name returns 400."""
        resp1 = await post_skill(
            http_client, "unique-name-bug", "First skill", "# First",
        )
        assert resp1.status_code == 201

        resp2 = await post_skill(
            http_client, "unique-name-bug", "Second skill, same name", "# First",
        )
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["error"]


class TestSkillAPILinks:
    """Test skill link reverse-lookup endpoint (issue #51)."""

    @pytest.mark.asyncio
    async def test_get_skill_links(self, http_client):
        """GET /api/v1/skills/{id}/links returns linked content IDs."""
        skill_id = await post_skill_id(
            http_client,
            "api-links-skill",
            "Skill for REST link lookup",
            "# Content",
        )

        doc_response = await http_client.post("/api/v1/documents", json={
            "title": "API Links Document",
            "description": "Document for REST link lookup",
            "content": "Reference content for the skill link test.",
        })
        document_id = doc_response.json()["id"]

        link_response = await link_skill(http_client, skill_id, "documents", document_id)
        assert link_response.status_code == 200

        response = await http_client.get(f"{SKILLS_URL}/{skill_id}/links")
        assert response.status_code == 200
        data = response.json()
        assert data["document_ids"] == [document_id]
        assert data["memory_ids"] == []
        assert data["file_ids"] == []
        assert data["code_artifact_ids"] == []
        assert data["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_skill_links_not_found(self, http_client):
        """GET /api/v1/skills/{id}/links returns 404 for non-existent skill."""
        response = await http_client.get(f"{SKILLS_URL}/999999/links")
        assert response.status_code == 404
