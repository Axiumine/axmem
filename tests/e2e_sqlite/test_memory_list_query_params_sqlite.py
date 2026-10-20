"""E2E (SQLite) tests for query-parameter handling on the memories list route.

Malformed or out-of-range values must produce a 400 naming the parameter instead of
reaching the database (500), and zero is a real value for ``project_id``.
"""
import pytest
from fastmcp import Client
from httpx import ASGITransport, AsyncClient

BEYOND_INT64 = 2**63  # 9223372036854775808, one past the largest SQL integer
INT32_MAX = 2**31 - 1  # largest id the PostgreSQL INTEGER id columns can hold
INT32_MIN = -(2**31)


@pytest.fixture
async def client(sqlite_app):
    """HTTP client that reports unhandled server errors as real 500 responses."""
    async with Client(sqlite_app):
        transport = ASGITransport(app=sqlite_app.http_app(), raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _create_memories(client, count, project_ids=None):
    for index in range(count):
        payload = {
            "title": f"Query params memory {index}",
            "content": f"content {index}",
            "context": "query params",
            "keywords": ["query-params"],
            "tags": ["query-params"],
            "importance": 5,
        }
        if project_ids:
            payload["project_ids"] = project_ids
        response = await client.post("/api/v1/memories", json=payload)
        assert response.status_code == 201, response.text


class TestMemoriesListQueryParams:
    async def test_project_id_zero_filters_instead_of_being_ignored(self, client):
        await _create_memories(client, 3)
        assert (await client.get("/api/v1/memories")).json()["total"] == 3
        assert (await client.get("/api/v1/memories?project_id=99")).json()["total"] == 0

        response = await client.get("/api/v1/memories?project_id=0")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0

    async def test_project_id_filter_still_selects_project_memories(self, client):
        project = await client.post(
            "/api/v1/projects",
            json={
                "name": "Query params project",
                "description": "project used to check the project_id filter",
                "project_type": "development",
            },
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        await _create_memories(client, 2, project_ids=[project_id])
        await _create_memories(client, 1)

        response = await client.get(f"/api/v1/memories?project_id={project_id}")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2

    @pytest.mark.parametrize("value", [BEYOND_INT64, -BEYOND_INT64 - 1])
    async def test_offset_beyond_int64_is_rejected(self, client, value):
        response = await client.get(f"/api/v1/memories?offset={value}")
        assert response.status_code == 400, f"offset={value} -> {response.status_code}"
        assert "offset" in response.json()["error"]

    @pytest.mark.parametrize("value", [INT32_MAX + 1, INT32_MIN - 1, 3_000_000_000, BEYOND_INT64])
    async def test_project_id_beyond_int32_is_rejected(self, client, value):
        """project_id is an INTEGER column on PostgreSQL, so a larger id must not reach it."""
        response = await client.get(f"/api/v1/memories?project_id={value}")
        assert response.status_code == 400, f"project_id={value} -> {response.status_code}"
        assert "project_id" in response.json()["error"]

    @pytest.mark.parametrize("value", [INT32_MAX, INT32_MIN])
    async def test_int32_edges_are_accepted_for_project_id(self, client, value):
        response = await client.get(f"/api/v1/memories?project_id={value}")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0
