"""E2E tests for query-parameter handling on the memories list route.

Malformed or out-of-range values must produce a 400 naming the parameter instead of
reaching the database (500). The ``project`` filter is a scope label: an empty value
means no filter, a blank or over-long one is a 400.
"""
import pytest
from fastmcp import Client
from httpx import ASGITransport, AsyncClient

BEYOND_INT64 = 2**63  # 9223372036854775808, one past the largest SQL integer


@pytest.fixture
async def client(qdrant_app):
    """HTTP client that reports unhandled server errors as real 500 responses."""
    async with Client(qdrant_app):
        transport = ASGITransport(app=qdrant_app.http_app(), raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _create_memories(client, count, project=None):
    for index in range(count):
        payload = {
            "title": f"Query params memory {index}",
            "content": f"content {index}",
            "context": "query params",
            "keywords": ["query-params"],
            "tags": ["query-params"],
            "importance": 5,
        }
        if project:
            payload["project"] = project
        response = await client.post("/api/v1/memories", json=payload)
        assert response.status_code == 201, response.text


class TestMemoriesListQueryParams:
    async def test_unknown_project_filters_instead_of_being_ignored(self, client):
        await _create_memories(client, 3)
        assert (await client.get("/api/v1/memories")).json()["total"] == 3

        response = await client.get("/api/v1/memories?project=no-such-project")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0

    async def test_project_filter_still_selects_project_memories(self, client):
        await _create_memories(client, 2, project="Query params project")
        await _create_memories(client, 1)

        response = await client.get("/api/v1/memories", params={"project": "Query params project"})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2

    async def test_empty_project_means_no_filter(self, client):
        await _create_memories(client, 2, project="Query params project")
        await _create_memories(client, 1)

        response = await client.get("/api/v1/memories?project=")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 3

    @pytest.mark.parametrize("value", ["%20%20", "x" * 501])
    async def test_blank_or_overlong_project_is_a_400_naming_the_parameter(self, client, value):
        response = await client.get(f"/api/v1/memories?project={value}")
        assert response.status_code == 400, f"project={value[:20]} -> {response.status_code}"
        assert "project" in response.json()["error"]

    @pytest.mark.parametrize("value", [BEYOND_INT64, -BEYOND_INT64 - 1])
    async def test_offset_beyond_int64_is_rejected(self, client, value):
        response = await client.get(f"/api/v1/memories?offset={value}")
        assert response.status_code == 400, f"offset={value} -> {response.status_code}"
        assert "offset" in response.json()["error"]
