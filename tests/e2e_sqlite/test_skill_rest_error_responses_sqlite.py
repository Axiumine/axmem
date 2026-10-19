"""SQLite end-to-end tests: error answers of the skill REST routes.

Covers deleting and importing skills, and the link/unlink routes for files, code artifacts
and documents when the skill is missing or the request body lacks the resource id.
"""
import pytest
from graph_sqlite_helpers import create_skill

MISSING_ID = 99999

# Relation segment of /skills/{id}/<relation> -> body field naming the target id
RELATIONS = {
    "files": "file_id",
    "code-artifacts": "code_artifact_id",
    "documents": "document_id",
}


async def test_delete_missing_skill_returns_404(http_client):
    response = await http_client.delete(f"/api/v1/skills/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": "Skill not found"}


async def test_delete_skill_removes_it(http_client):
    skill_id = await create_skill(http_client, "doomed-skill", "Will be deleted", "# Steps")

    response = await http_client.delete(f"/api/v1/skills/{skill_id}")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert (await http_client.get(f"/api/v1/skills/{skill_id}")).status_code == 404


@pytest.mark.parametrize("body", [{}, {"skill_md": ""}], ids=["field-absent", "field-empty"])
async def test_import_without_skill_markdown_returns_400(http_client, body):
    response = await http_client.post("/api/v1/skills/import", json=body)

    assert response.status_code == 400
    assert response.json() == {"error": "skill_md field is required"}


async def test_import_into_missing_project_returns_404(http_client):
    skill_md = "---\nname: orphan-import\ndescription: Imported into nowhere\n---\n\n# Steps\n"

    response = await http_client.post(
        "/api/v1/skills/import", json={"skill_md": skill_md, "project_id": MISSING_ID},
    )

    assert response.status_code == 404
    assert str(MISSING_ID) in response.json()["error"]
    assert (await http_client.get("/api/v1/skills")).json()["skills"] == []


@pytest.mark.parametrize("relation", list(RELATIONS))
async def test_link_without_resource_id_returns_400(http_client, relation):
    skill_id = await create_skill(http_client, f"link-{relation}", "Needs a body", "# Steps")

    response = await http_client.post(f"/api/v1/skills/{skill_id}/{relation}", json={})

    assert response.status_code == 400
    assert response.json() == {"error": f"{RELATIONS[relation]} is required"}


@pytest.mark.parametrize("relation", list(RELATIONS))
async def test_link_to_missing_skill_returns_404(http_client, relation):
    response = await http_client.post(
        f"/api/v1/skills/{MISSING_ID}/{relation}", json={RELATIONS[relation]: 1},
    )

    assert response.status_code == 404
    assert response.json()["error"]


@pytest.mark.parametrize("relation", list(RELATIONS))
async def test_unlink_from_missing_skill_returns_404(http_client, relation):
    response = await http_client.delete(f"/api/v1/skills/{MISSING_ID}/{relation}/1")

    assert response.status_code == 404
    assert response.json()["error"]
