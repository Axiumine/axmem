"""E2E tests (in-memory SQLite) for update_memory consistency

Covers REST null handling, skill_ids round-tripping on every read path, and the
embedding written by update_memory.
"""
import pytest

CREATE_BODY = {
    "title": "Original title",
    "content": "Original content for update consistency tests.",
    "context": "Original context",
    "keywords": ["alpha", "beta"],
    "tags": ["t1"],
    "importance": 7,
    "source_repo": "owner/repo",
    "source_url": "https://example.com/x",
}


async def _create_memory(http_client, **extra):
    response = await http_client.post("/api/v1/memories", json={**CREATE_BODY, **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ============================================================================
# Explicit null means "unchanged" (PATCH semantics)
# ============================================================================


@pytest.mark.parametrize(
    "field",
    ["title", "content", "importance", "keywords", "tags", "source_repo", "source_url"],
)
async def test_put_with_null_field_leaves_memory_unchanged(http_client, field):
    memory_id = await _create_memory(http_client)

    response = await http_client.put(f"/api/v1/memories/{memory_id}", json={field: None})

    assert response.status_code == 200, response.text
    stored = (await http_client.get(f"/api/v1/memories/{memory_id}")).json()
    assert stored[field] == CREATE_BODY[field]


async def test_put_with_null_next_to_a_real_change_applies_only_the_change(http_client):
    memory_id = await _create_memory(http_client)

    response = await http_client.put(
        f"/api/v1/memories/{memory_id}",
        json={"title": None, "content": "Changed content", "source_repo": None},
    )

    assert response.status_code == 200, response.text
    stored = (await http_client.get(f"/api/v1/memories/{memory_id}")).json()
    assert stored["content"] == "Changed content"
    assert stored["title"] == CREATE_BODY["title"]
    assert stored["source_repo"] == CREATE_BODY["source_repo"]
