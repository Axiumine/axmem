"""PostgreSQL: REST behaviour of code artifacts, documents and files at their edges.

Unknown ids on update and delete, filters that narrow a listing (language, document type,
project) and the normalisation of a language on update. All checks go through the REST API
and read the persisted state back through a second request.
"""
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

UNKNOWN_ID = 987654

RESOURCES = {
    "code-artifacts": ("Code artifact", {"title": "renamed"}),
    "documents": ("Document", {"title": "renamed"}),
    "files": ("File", {"description": "renamed"}),
}


async def _create_project(http_client, name: str) -> int:
    response = await http_client.post("/api/v1/projects", json={
        "name": name, "description": "d", "project_type": "development",
    })
    assert response.status_code == 201
    return response.json()["id"]


async def _create_code_artifact(http_client, title: str, **extra) -> dict:
    response = await http_client.post("/api/v1/code-artifacts", json={
        "title": title, "description": "d", "code": "x = 1", "language": "python", **extra,
    })
    assert response.status_code == 201
    return response.json()


async def _create_document(http_client, title: str, **extra) -> dict:
    response = await http_client.post("/api/v1/documents", json={
        "title": title, "description": "d", "content": "body", **extra,
    })
    assert response.status_code == 201
    return response.json()


async def _create_file(http_client, filename: str, **extra) -> dict:
    response = await http_client.post("/api/v1/files", json={
        "filename": filename, "description": "d", "data": "aGVsbG8=",
        "mime_type": "text/plain", **extra,
    })
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("resource", sorted(RESOURCES))
async def test_updating_an_unknown_id_reports_not_found(http_client, resource):
    label, patch = RESOURCES[resource]

    response = await http_client.put(f"/api/v1/{resource}/{UNKNOWN_ID}", json=patch)

    assert response.status_code == 404
    assert response.json() == {"error": f"{label} not found"}


@pytest.mark.parametrize("resource", sorted(RESOURCES))
async def test_deleting_an_unknown_id_reports_not_found(http_client, resource):
    label, _ = RESOURCES[resource]

    response = await http_client.delete(f"/api/v1/{resource}/{UNKNOWN_ID}")

    assert response.status_code == 404
    assert response.json() == {"error": f"{label} not found"}


async def test_code_artifact_listing_filters_by_language_ignoring_case(http_client):
    await _create_code_artifact(http_client, "python snippet", language="python")
    await _create_code_artifact(http_client, "rust snippet", language="rust")

    response = await http_client.get("/api/v1/code-artifacts", params={"language": "PYTHON"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [a["title"] for a in body["code_artifacts"]] == ["python snippet"]


async def test_code_artifact_listing_filters_by_any_matching_tag(http_client):
    await _create_code_artifact(http_client, "api helper", tags=["api", "auth"])
    await _create_code_artifact(http_client, "cli helper", tags=["cli"])
    await _create_code_artifact(http_client, "untagged")

    response = await http_client.get("/api/v1/code-artifacts", params={"tags": "auth, db"})

    assert response.status_code == 200
    assert [a["title"] for a in response.json()["code_artifacts"]] == ["api helper"]


async def test_updating_a_code_artifact_language_stores_it_lowercase(http_client):
    created = await _create_code_artifact(http_client, "snippet", language="python")

    updated = await http_client.put(
        f"/api/v1/code-artifacts/{created['id']}", json={"language": "TypeScript"},
    )
    reread = await http_client.get(f"/api/v1/code-artifacts/{created['id']}")

    assert updated.status_code == 200
    assert updated.json()["language"] == "typescript"
    assert reread.json()["language"] == "typescript"
    assert reread.json()["title"] == "snippet"


async def test_document_listing_filters_by_document_type(http_client):
    await _create_document(http_client, "notes", document_type="markdown")
    await _create_document(http_client, "log", document_type="text")

    response = await http_client.get("/api/v1/documents", params={"document_type": "markdown"})

    assert response.status_code == 200
    body = response.json()
    assert [d["title"] for d in body["documents"]] == ["notes"]
    assert body["total"] == 1


async def test_file_listing_filters_by_project(http_client):
    project_id = await _create_project(http_client, "files project")
    await _create_file(http_client, "inside.txt", project_id=project_id)
    await _create_file(http_client, "outside.txt")

    response = await http_client.get("/api/v1/files", params={"project_id": project_id})

    assert response.status_code == 200
    body = response.json()
    assert [f["filename"] for f in body["files"]] == ["inside.txt"]
    assert body["total"] == 1
